"""Is assay's parse of a model what its SQL says? The round trip. (L2)

*** EVERY CERTIFICATE IS ABOUT THE PARSE, SO THE PARSE IS A PREMISE. ***
`assay prove` reasons about the structure sqlglot read from the SQL. If sqlglot misread it -- a
join condition attached to the wrong join, a precedence it got wrong -- every proof about it is a
proof about some other query. So each model's parse is printed back to SQL and both are run on the
same inputs; the premise `parse_faithful(model)` is `holding` when every row agrees, `broken` with
the rows that differ, `unchecked` when the SQL could not run on generated inputs (a function the
inputs cannot feed, an extension not loaded). L4 replaces this measurement with a proof.

The inputs are adversarial on purpose: for every relation the model reads, rows that duplicate a
key, rows that are all NULL, and rows that tie on everything but one column -- the cases where a
misread join, NULL comparison or tie-break shows.

Two places it runs:
* `duckdb` (the default): an in-memory DuckDB with each input relation created and filled. Free,
  no connection, and only for projects whose SQL is DuckDB's.
* `warehouse`: through the project's own dbt connection (`dbt show`), each input replaced by a
  `VALUES` CTE in the statement itself, so nothing is created. Read-only, priced in the warehouse
  ledger under `assay.prove`, and counted against the spend cap like any other statement.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone

import sqlglot
from sqlglot import exp

from . import bulk
from . import ledger as L

N_ROWS = 5


def _kind(t: str) -> str:
    from .cost import normalize_type
    n = normalize_type(t or "")
    if n in ("integer", "int", "bigint", "smallint", "tinyint", "hugeint", "int64", "int4",
             "int8", "int2", "ubigint", "uinteger", "number") or n.endswith("int"):
        return "int"
    if n in ("double", "float", "real", "decimal", "numeric", "float8", "float4", "float64"):
        return "float"
    if n in ("date",):
        return "date"
    if n.startswith("timestamp") or n == "datetime":
        return "timestamp"
    if n in ("boolean", "bool"):
        return "bool"
    if n in ("geometry", "geography", "blob", "json", "struct", "map", "list", "array"):
        return "null"                             # a NULL feeds any function without a guess
    if n in ("varchar", "text", "string", "char", "bpchar", "nvarchar") or not n:
        return "text"
    return "null"


def _values(kind: str, last: bool) -> list:
    """Five values: a key, the same key again, another, NULL, and the other again -- except
    the last column, which breaks the tie so the rows are not identical."""
    base = {"int": [1, 1, 2, None, 2], "float": [1.5, 1.5, 2.25, None, 2.25],
            "text": ["a", "a", "b", None, "b"],
            "date": ["2026-01-01", "2026-01-01", "2026-01-02", None, "2026-01-02"],
            "timestamp": ["2026-01-01 00:00:00", "2026-01-01 00:00:00", "2026-01-02 00:00:00",
                          None, "2026-01-02 00:00:00"],
            "bool": [True, True, False, None, False], "null": [None] * 5}[kind]
    if last and kind != "null":
        alt = {"int": 3, "float": 3.5, "text": "c", "date": "2026-01-03",
               "timestamp": "2026-01-03 00:00:00", "bool": True}[kind]
        base = [*base[:4], alt]
    return base


def _sqltype(kind: str) -> str:
    return {"int": "BIGINT", "float": "DOUBLE", "text": "VARCHAR", "date": "DATE",
            "timestamp": "TIMESTAMP", "bool": "BOOLEAN", "null": "VARCHAR",
            "numtext": "VARCHAR"}[kind]


# A geometry carried as text (GeoJSON, WKT, WKB): `'a'` is not one, and a NULL is always valid.
_GEO_TEXT = re.compile(r"geojson|geom|wkt|wkb|shape|polygon|boundary", re.IGNORECASE)


def _seed_columns(project, name: str) -> list:
    """A seed's columns, from its CSV header: seeds are relations the manifest gives no
    columns for unless a YAML declares them."""
    import csv
    from pathlib import Path
    for n in ((project.raw or {}).get("nodes") or {}).values():
        if n.get("resource_type") != "seed" or \
                (n.get("alias") or n.get("name") or "").lower() != name.lower():
            continue
        if n.get("columns"):
            return [c.lower() for c in n["columns"]]
        f = Path(getattr(project, "project_root", ".")) / (n.get("original_file_path") or "")
        try:
            with open(f, newline="") as fh:
                return [c.strip().lower() for c in next(csv.reader(fh))]
        except (OSError, StopIteration):
            return []
    return []


def _coltype(kind: str, raw: str, spatial: bool) -> str:
    from .cost import normalize_type
    if kind == "null" and spatial and normalize_type(raw) == "geometry":
        return "GEOMETRY"
    return _sqltype(kind)


def _inputs(project, schema, sql: str, dialect: str):
    """[(relation as written, catalog, db, name, [(column, kind)])] the model reads, or raises."""
    from .cost import declared_types
    types = {k.replace('"', "").lower(): v for k, v in declared_types(project, schema).items()}
    tree = sqlglot.parse_one(sql, read=dialect)
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    out, seen = [], set()
    for t in tree.find_all(exp.Table):
        name = t.name
        if not name or (not t.db and name.lower() in ctes):
            continue
        full = ".".join(p for p in (t.catalog, t.db, name) if p)
        if full.lower() in seen:
            continue
        seen.add(full.lower())
        uid = (getattr(schema, "uid_of", {}) or {}).get(full.lower()) or next(
            (u for r, u in (getattr(schema, "uid_of", {}) or {}).items()
             if r.replace('"', "").lower().endswith("." + name.lower())), None)
        cols = list(schema.columns(uid).names) if uid else []
        if not cols:
            cols = _seed_columns(project, name)
        if not cols:
            raise ValueError(f"the columns of `{full}` are not known")
        rt = types.get(full.lower()) or next(
            (v for k, v in types.items() if k.endswith("." + name.lower())), {})
        out.append((full, t.catalog, t.db, name,
                    [(c.lower(), "null" if _GEO_TEXT.search(c) else
                      _kind(rt.get(c.lower(), "")), rt.get(c.lower(), "")) for c in cols]))
    return out


def _bag(rows) -> Counter:
    import math

    def norm(v):
        if isinstance(v, float):
            # NaN is not equal to itself, so two identical results would never compare equal
            return "nan" if math.isnan(v) else round(v, 9)
        return str(v) if v is not None and not isinstance(v, (int, bool)) else v
    return Counter(tuple(norm(v) for v in r) for r in rows)


def _why_not_run(err: str) -> str:
    first = err.splitlines()[0][:200]
    m = re.search(r"Scalar Function with name \"?(\w+)\"? does not exist", err)
    if m:
        return (f"the SQL calls `{m.group(1)}`, a function the project defines outside its SQL "
                f"(a UDF on the connection), which the round trip cannot run")
    return "the SQL could not run on generated rows: " + first


def order_dependent(sql: str, dialect: str) -> str:
    """The first aggregate whose result depends on row order and has no ORDER BY, or ''."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:                                            # noqa: BLE001
        return ""
    for node in tree.find_all(exp.GroupConcat, exp.ArrayAgg, exp.First, exp.Last,
                              exp.AnyValue, exp.Anonymous):
        name = (node.sql_name() if not isinstance(node, exp.Anonymous) else str(node.name))
        name = name.lower()
        if isinstance(node, exp.Anonymous) and name not in ("string_agg", "listagg", "list",
                                                             "group_concat", "arbitrary"):
            continue
        if node.find(exp.Order) is None and not isinstance(node.parent, exp.WithinGroup):
            return f"`{name}`"
    return ""


def printed(sql: str, dialect: str) -> str:
    """The parse, printed back: what every other part of assay reasons about."""
    return sqlglot.parse_one(sql, read=dialect).sql(dialect=dialect)


def check_duckdb(project, schema, sql: str, dialect: str, worker=None) -> tuple[str, str, int]:
    """`worker`: a sandbox.Worker the SQL runs in, so an engine crash is a result (B3)."""
    from .parse import deep
    with deep():
        return _check_duckdb_retry(project, schema, sql, dialect, worker)


def _check_duckdb_retry(project, schema, sql: str, dialect: str,
                        worker=None) -> tuple[str, str, int]:
    """(status, detail, rows compared) in an in-memory DuckDB.

    A column with no known type is filled with text first; if the SQL cannot run because it
    compares one to a number, it is tried again with those columns as integers."""
    got = _check_duckdb(project, schema, sql, dialect, untyped="text", worker=worker)
    if got[0] == L.UNCHECKED and re.search(r"VARCHAR and type (INTEGER|DECIMAL|DOUBLE|BIGINT)"
                                           r"|Could not convert string", got[1]):
        again = _check_duckdb(project, schema, sql, dialect, untyped="int", worker=worker)
        if again[0] != L.UNCHECKED:
            return again
    return got


def _default_rows(full: str, cols: list) -> list:
    vals = [_values(k, i == len(cols) - 1) for i, (c, k, _r) in enumerate(cols)]
    return [[v[r] for v in vals] for r in range(N_ROWS)]


def load_spatial(con, sql: str) -> bool:
    """Geometry functions live in DuckDB's spatial extension; a project using them had it
    loaded when it built. Loaded when it is installed; absent, those models read unchecked."""
    if not re.search(r"\bst_\w+\s*\(", sql, re.IGNORECASE):
        return False
    for stmt in (["load spatial"], ["install spatial", "load spatial"]):
        try:
            for x in stmt:
                con.execute(x)
            return True
        except Exception:                                        # noqa: BLE001, S112
            continue
    return False


def fill(con, ins, sql: str, rows_for=None) -> None:
    """Create and fill every relation the model reads. `rows_for(full, cols)` gives each one's
    rows; the default is the adversarial five (a key, the same key, another, NULL, a tie)."""
    rows_for = rows_for or _default_rows
    spatial = load_spatial(con, sql)
    cats = set()
    for full, cat, db, name, cols in ins:
        if cat and cat.lower() not in cats and cat.lower() != "memory":
            con.execute(f'attach \':memory:\' as "{cat}"')
            cats.add(cat.lower())
        prefix = ".".join(f'"{p}"' for p in (cat, db) if p)
        if db:
            con.execute(f"create schema if not exists {prefix}")
        q = f'{prefix + "." if prefix else ""}"{name}"'
        con.execute(f"create table if not exists {q} (" + ", ".join(
            f'"{c}" {_coltype(k, raw, spatial)}' for c, k, raw in cols) + ")")
        rows = rows_for(full, cols)
        if rows:
            con.executemany(f"insert into {q} values (" + ", ".join("?" * len(cols)) + ")", rows)


def execute(ins, sql: str, again: str) -> tuple:
    """In the worker process (sandbox): fill the inputs, run the SQL and its printed parse.
    ("ok", rows, rows) | ("sql", error) | ("printed", error, rows of the SQL)."""
    import duckdb
    con = duckdb.connect(":memory:")
    try:
        fill(con, ins, sql)
        try:
            a = con.execute(sql).fetchall()
        except Exception as e:                                   # noqa: BLE001
            return ("sql", str(e))
        try:
            b = con.execute(again).fetchall()
        except Exception as e:                                   # noqa: BLE001
            return ("printed", str(e), a)
        return ("ok", a, b)
    finally:
        con.close()


def _check_duckdb(project, schema, sql: str, dialect: str, untyped: str = "text",
                  worker=None):
    if (dialect or "duckdb") != "duckdb":
        return (L.UNCHECKED, (f"this project's SQL is {dialect}; the in-memory round trip runs "
                             f"DuckDB only. `assay prove --parse-on warehouse` runs it through "
                             f"your own connection"), 0)
    try:
        ins = _inputs(project, schema, sql, dialect)
        if untyped != "text":
            ins = [(f, c, db, n, [(col, untyped if not raw and k == "text" else k, raw)
                                  for col, k, raw in cols]) for f, c, db, n, cols in ins]
        again = printed(sql, dialect)
    except Exception as e:                                       # noqa: BLE001
        return L.UNCHECKED, f"not read: {str(e)[:200]}", 0
    if worker is not None:
        ok, got = worker.run(execute, ins, sql, again)
        if not ok:
            return L.UNCHECKED, str(got), 0
    else:
        got = execute(ins, sql, again)
    if got[0] == "sql":
        return L.UNCHECKED, _why_not_run(got[1]), 0
    if got[0] == "printed":
        return (L.BROKEN, "the printed parse failed where the SQL ran: "
                          + got[1].splitlines()[0][:200], len(got[2]))
    a, b = got[1], got[2]
    if True:
        if _bag(a) == _bag(b):
            return (L.HOLDING, (f"the SQL and its printed parse agree on all {len(a)} row(s) "
                               f"from generated inputs"), len(a))
        diff = (_bag(a) - _bag(b)) + (_bag(b) - _bag(a))
        loose = order_dependent(sql, dialect)
        if loose:
            return (L.UNCHECKED, (f"the results differ, and the SQL uses {loose} with no ORDER BY, "
                                 f"whose output order is the engine's choice: a difference here "
                                 f"is not evidence about the parse"), len(a))
        return (L.BROKEN, (f"{sum(diff.values())} row(s) differ between the SQL and its printed "
                          f"parse, e.g. {list(diff)[:2]}"), len(a))


def warehouse_sql(project, schema, sql: str, dialect: str) -> tuple[str, str]:
    """(original, printed) as statements over VALUES inputs, in the project's dialect."""
    ins = _inputs(project, schema, sql, dialect)
    ctes, names = [], {}
    for i, (full, cat, db, name, cols) in enumerate(ins):
        cte = f"__assay_in_{i}"
        names[full.lower()] = cte
        rows = []
        vals = [_values(k, j == len(cols) - 1) for j, (c, k, _r) in enumerate(cols)]
        for r in range(N_ROWS):
            cells = []
            for (c, k, _r), v in zip(cols, vals):
                lit = "NULL" if v[r] is None else (
                    ("TRUE" if v[r] else "FALSE") if k == "bool" else
                    str(v[r]) if k in ("int", "float") else "'" + str(v[r]) + "'")
                cells.append(f"CAST({lit} AS {_sqltype(k)}) AS {c}")
            rows.append("SELECT " + ", ".join(cells))
        ctes.append(f"{cte} AS (" + " UNION ALL ".join(rows) + ")")
    orig = sql
    for full, cte in sorted(names.items(), key=lambda kv: -len(kv[0])):
        parts = full.split(".")
        pat = r"\.".join(r'"?' + re.escape(p) + r'"?' for p in parts)
        orig = re.sub(pat, cte, orig, flags=re.IGNORECASE)
    tree = sqlglot.parse_one(sql, read=dialect)
    for t in tree.find_all(exp.Table):
        full = ".".join(p for p in (t.catalog, t.db, t.name) if p).lower()
        if full in names:
            t.replace(exp.to_table(names[full]))
    again = tree.sql(dialect=dialect)
    pre = "WITH " + ", ".join(ctes)
    # a new line before `)`: a model ending in a `--` comment would swallow it (M5)
    wrap = lambda body: f"{pre}, __assay_model AS ({body}\n) SELECT * FROM __assay_model"
    return wrap(orig), wrap(again)


def check_warehouse(project, schema, sql, dialect, runner) -> tuple[str, str, int]:
    from .parse import deep
    with deep():
        return _check_warehouse(project, schema, sql, dialect, runner)


def _check_warehouse(project, schema, sql, dialect, runner) -> tuple[str, str, int]:
    try:
        a_sql, b_sql = warehouse_sql(project, schema, sql, dialect)
    except Exception as e:                                       # noqa: BLE001
        return L.UNCHECKED, f"not read: {str(e)[:200]}", 0
    a = runner(a_sql)
    if a.failed:
        return L.UNCHECKED, f"the SQL could not run on generated rows: {a.why[:200]}", 0
    b = runner(b_sql)
    if b.failed:
        return L.BROKEN, f"the printed parse failed where the SQL ran: {b.why[:200]}", len(a.rows)
    ra = [tuple(r.values()) for r in a.rows]
    rb = [tuple(r.values()) for r in b.rows]
    if _bag(ra) == _bag(rb):
        return (L.HOLDING, (f"the SQL and its printed parse agree on all {len(ra)} row(s) "
                           f"from generated inputs"), len(ra))
    return L.BROKEN, "the SQL and its printed parse return different rows", len(ra)


def run(project, digests, schema, store, *, via: str = "duckdb", select=None, force=False,
        project_dir: str = ".", profiles_dir: str | None = None, dbt_bin: str = "dbt",
        runner=None, say=print) -> dict:
    """Round-trip every readable model whose current file has not been checked `via` yet."""
    store.con.execute(L.DDL_PARSE)
    done = {(m, cs) for m, cs in store.con.execute(
        "select model, model_checksum from parse_checks where via = ?", [via]).fetchall()}
    dialect = getattr(project, "dialect", "duckdb") or "duckdb"
    if via == "warehouse" and runner is None:
        from . import probe as probe_mod

        def runner(sql):
            return probe_mod.run_sql(sql, project_dir, profiles_dir, dbt_bin, limit=1000,
                                     caller="assay.prove", kind="parse_check")
    rows, by_model = [], {}
    todo = [(u, m) for u, m in sorted(project.models.items())
            if m.readable and not getattr(m, "is_installed_package", False)
            and (select is None or u in select)
            and (force or (u, m.checksum or "") not in done)]
    if todo:
        say(f"checking the parse of {len(todo)} model(s) against their SQL ({via})")
    from .sandbox import Worker, default_slots
    # In DuckDB each model runs in a sandbox process, several at once; through the warehouse it
    # is a dbt call, and those share one DuckDB writer, so they go one at a time.
    with Worker(slots=1 if via == "warehouse" else default_slots()) as worker:
        def one(item):
            _uid, m = item
            if via == "warehouse":
                return check_warehouse(project, schema, m.compiled, dialect, runner)
            return check_duckdb(project, schema, m.compiled, dialect, worker)
        for (uid, m), (st, detail, n) in zip(todo, worker.map(one, todo)):
            rows.append((uid, m.checksum or "", st, detail, n, via, datetime.now(timezone.utc)))
            by_model[m.name] = st
    if rows:
        bulk.many(store.con, "insert or replace into parse_checks values (?,?,?,?,?,?,?)", rows)
    counts = Counter(r[2] for r in rows)
    return {"checked": len(rows), "by_status": dict(counts), "by_model": by_model, "via": via}
