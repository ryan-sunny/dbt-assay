"""Many rows into the store in one statement.

*** `executemany` IS A LOOP, AND ON THE BOX IT WAS A MINUTE OF EVERY DAILY RUN. ***
DuckDB's Python `executemany` binds and runs the statement once per row: 5,000 test results took
4.1s on the laptop and seven seconds on the box for ONE table in ONE command, and `check` writes
a dozen tables that way. Binding a Python list as a parameter is slow too (0.3s for 5,000
strings). One JSON string is not: DuckDB parses it in C, and the same 5,000 rows land in 0.014s.

`many(con, sql, rows)` takes exactly the statement `executemany` took. The `values (...)` tuple is
rewritten into a select over the JSON rows, each `?` becoming the row's next field, and the
insert's own cast to the column types does the rest, as it does for a literal. When the one
statement cannot say the same thing, the rows go through `executemany` as before: slower, never
different. One statement sees every row at once, so two rows with one primary key are narrowed
first to the one the loop would have left (the last under `or replace`, else the first), and a key
the statement does not bind plainly, a float JSON cannot write or a value DuckDB binds as a list,
takes the loop.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

_INSERT = re.compile(r"^(?P<head>\s*insert\b.*?)\bvalues\s*\((?P<items>.*)\)\s*$",
                     re.IGNORECASE | re.DOTALL)
_TARGET = re.compile(r"\binto\s+(?P<table>[\w.]+)\s*(?:\((?P<cols>[^)]*)\))?", re.IGNORECASE)


def _split(items: str) -> list[str]:
    """The tuple's items, split on the commas outside parentheses."""
    out, depth, cur = [], 0, ""
    for ch in items:
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
            continue
        depth += (ch == "(") - (ch == ")")
        cur += ch
    return [*out, cur.strip()]


def _one_per_key(con, sql: str, rows: list) -> list | None:
    """The rows `executemany` would have left: per primary key, the LAST under `or replace`, the
    first otherwise. None when the key cannot be read off the statement."""
    m, t = _INSERT.match(sql), _TARGET.search(sql)
    if not m or not t:
        return None
    table = t.group("table").split(".")[-1]
    try:
        pk = con.execute("select constraint_column_names from duckdb_constraints() "
                         "where table_name = ? and constraint_type = 'PRIMARY KEY'",
                         [table]).fetchone()
        cols = ([c.strip().lower() for c in t.group("cols").split(",")] if t.group("cols")
                else [r[0].lower() for r in con.execute(
                    "select column_name from duckdb_columns() where table_name = ? "
                    "order by column_index", [table]).fetchall()])
    except Exception:                                            # noqa: BLE001
        return None
    if not pk:
        return rows
    items = _split(m.group("items"))
    if len(items) != len(cols):
        return None
    param, at = {}, 0
    for col, item in zip(cols, items):
        if item == "?":
            param[col] = at
        at += item.count("?")
    try:
        where = [param[c.lower()] for c in pk[0]]
    except KeyError:
        return None
    keep: dict = {}
    verb = re.search(r"\binsert\s+or\s+(replace|ignore)\b", sql, re.IGNORECASE)
    for r in rows:
        k = tuple(r[i] for i in where)
        if k in keep and verb is None:
            return None                   # a plain insert of one key twice fails as it did
        if k not in keep or verb.group(1).lower() == "replace":
            keep[k] = r
    return list(keep.values())


def _select(sql: str) -> str | None:
    m = _INSERT.match(sql)
    if not m:
        return None
    k = 0

    def field(_m):
        nonlocal k
        k += 1
        return f"r[{k}]"
    items = re.sub(r"\?", field, m.group("items"))
    return (f"{m.group('head')} select {items} "
            f"from (select unnest(from_json(?::json, '[\"VARCHAR[]\"]')) as r)")


def _text(v, zone=None):
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, datetime) and v.tzinfo is not None:
        # *** TEXT WITH AN OFFSET, CAST TO `timestamp`, DROPS THE OFFSET. *** The loop binds an
        # aware datetime as an instant and DuckDB writes it in the session's zone; the text
        # `10:00-06:00` became 10:00. So it is written as the session's wall time, which both
        # `timestamp` and `timestamptz` read as that same instant.
        if zone is None:
            raise TypeError("no session zone")
        return str(v.astimezone(zone).replace(tzinfo=None))
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (dict, list, tuple, set, bytes, bytearray)):
        # DuckDB binds these as STRUCT/LIST/BLOB, and their text is not that; the loop keeps it.
        raise TypeError(type(v).__name__)
    return str(v)


def _zone(con):
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(con.execute("select current_setting('TimeZone')").fetchone()[0])
    except Exception:                                            # noqa: BLE001
        return None


def many(con, sql: str, rows) -> None:
    """`con.executemany(sql, rows)`, in one statement when it can be."""
    rows = [list(r) for r in rows]
    if not rows:
        return
    stmt = _select(sql) if len(rows) > 1 else None
    once = _one_per_key(con, sql, rows) if stmt is not None else None
    if stmt is not None and once is not None:
        try:
            zone = _zone(con) if any(isinstance(v, datetime) and v.tzinfo is not None
                                     for r in once for v in r) else None
            payload = json.dumps([[_text(v, zone) for v in r] for r in once], allow_nan=False)
            con.execute(stmt, [payload])
            return
        except (ValueError, TypeError, OverflowError):
            pass
        except Exception:                                        # noqa: BLE001, S110
            # The statement is atomic: nothing was written, so the loop below starts clean.
            pass
    con.executemany(sql, rows)
