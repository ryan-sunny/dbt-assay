"""Does the engine do what assay's definition of SQL says? Measured, construct by construct. (L4)

*** THE ONE LINK NO PROOF CAN CLOSE. *** An engine is not a mathematical object, so "Lean's meaning
of a LEFT JOIN is what Snowflake does" cannot be proven -- it can only be run. For each construct a
proof leans on (a join on a NULL key, `row_number()` ties, `count(x)` against `count(*)`, NULL in
`IN`, integer division, ...) the same small tables go through Lean's evaluator (`assay_sql eval`)
and through the engine, and the two bags of rows must match. The result is a ledger premise,
`engine_conforms(construct, engine)`; a certificate using a construct whose conformance is not
`holding` says so.

Engines: DuckDB in memory (free, always), and the project's own warehouse through its dbt
connection (`--engine warehouse`), each table a `VALUES` CTE, read-only and priced. A Snowflake
premise reads `unchecked` until it has run against a real Snowflake connection.

Random differential tests (`random`) generate small tables with duplicates, NULLs and ties and
queries over them from a fixed seed, and compare the same way: the definition is kept honest by
being run, not by being read.
"""
from __future__ import annotations

import random
import subprocess
from collections import Counter
from datetime import datetime, timezone

DDL = """
create table if not exists conformance (
    construct      varchar,
    engine         varchar,        -- duckdb | snowflake | bigquery | ...
    engine_version varchar,
    status         varchar,        -- holding | broken | unchecked
    detail         varchar,
    checked_at     timestamp,
    primary key (construct, engine, engine_version)
);
"""

T = {"l": (["k", "v"], [[1, 10], [1, 11], [None, 12], [2, 13]]),
     "r": (["k", "w"], [[1, "a"], [None, "b"], [3, "c"]]),
     "t": (["k", "v", "s"], [[1, 5, "x"], [1, None, "y"], [2, 7, None], [None, 7, "x"],
                            [2, 7, "z"]])}

# name -> (tables, sql, what it settles). Every query stays inside the fragment.
CONSTRUCTS = {
    "inner_join_null_key": ("select l.k, r.w from l join r on l.k = r.k",
                            "a NULL key never matches in an inner join"),
    "left_join_null_key": ("select l.k, l.v, r.w from l left join r on l.k = r.k",
                           "a left row with a NULL or unmatched key is kept once, right side NULL"),
    "join_fanout": ("select a.v, b.v from l as a join l as b on a.k = b.k",
                    "a duplicated key multiplies rows"),
    "count_star_vs_count_col": ("select count(*) as n, count(v) as c from t",
                                "count(*) counts rows, count(x) skips NULLs"),
    "count_distinct_null": ("select count(distinct v) as c from t",
                            "count(distinct x) skips NULLs"),
    "sum_skips_null": ("select k, sum(v) as s from t group by k",
                       "sum skips NULLs; all-NULL is NULL"),
    "group_by_null_key": ("select k, count(*) as n from t group by k",
                          "NULL keys form one group"),
    "null_in_list": ("select v from t where v in (5, NULL)", "a NULL in IN never matches"),
    "not_in_with_null": ("select v from t where v not in (5, NULL)",
                         "NOT IN a list holding NULL keeps nothing"),
    "null_equality": ("select v from t where v = NULL", "= NULL is never TRUE"),
    "three_valued_not": ("select v from t where not (v > 5)", "NOT UNKNOWN is UNKNOWN"),
    "coalesce_null": ("select coalesce(v, 0) as c from t", "coalesce takes the first non-NULL"),
    "case_null": ("select case when v > 5 then 1 else 0 end as c from t",
                  "a CASE condition that is UNKNOWN takes the ELSE"),
    "distinct_null": ("select distinct v from t", "DISTINCT keeps one NULL"),
    "row_number_ties": ("select k from t qualify row_number() over (partition by k order by v) = 1",
                        "one row per partition, whatever ties"),
    "row_number_desc_nulls": ("select k, v from t qualify row_number() over "
                              "(partition by k order by v desc) = 1",
                              "where NULL sorts: larger than every value"),
    "integer_division": ("select v / 2 as h from t where v is not null",
                         "dividing integers truncates"),
    "concat_null": ("select s || 'x' as c from t", "concatenating NULL is NULL"),
    "union_all_keeps_duplicates": ("select v from t union all select v from t",
                                   "UNION ALL keeps every row"),
    "union_removes_duplicates": ("select v from t union select v from t",
                                 "UNION keeps one of each"),
    "aggregate_filter": ("select count(*) filter (where v > 5) as n from t",
                         "FILTER counts only the rows it keeps"),
    "like": ("select s from t where s like 'x%'", "LIKE with % and _"),
}

# which constructs each proven rule leans on
RULE_CONSTRUCTS = {
    "inner_join_no_fanout": ["inner_join_null_key", "join_fanout"],
    "left_join_preserves_rows": ["left_join_null_key"],
    "join_onto_grouped_no_fanout": ["inner_join_null_key", "group_by_null_key"],
    "left_join_onto_grouped_preserves_rows": ["left_join_null_key", "group_by_null_key"],
    "grain_through_join": ["inner_join_null_key", "join_fanout"],
    "grain_through_left_join": ["left_join_null_key"],
    "group_by_unique": ["group_by_null_key"],
    # the pick theorems hold for ANY total order on the sort key, so where an engine puts NULLs
    # (row_number_desc_nulls) is not a premise of theirs; ties are
    "pick_is_order_independent": ["row_number_ties"],
    "pick_total_on_unique_key": ["row_number_ties"],
}


# ------------------------------------------------------------------ running one case

def _enc(v) -> str:
    if v is None:
        return "n"
    if isinstance(v, bool):
        return "b1" if v else "b0"
    if isinstance(v, int):
        return f"i{v}"
    return "s" + str(v).encode().hex()


def _dec(s: str):
    if s == "n":
        return None
    if s in ("b0", "b1"):
        return s == "b1"
    if s.startswith("i"):
        return int(s[1:])
    if s.startswith("s"):
        return bytes.fromhex(s[1:]).decode()
    return s


def lean_rows(tables: dict, sql: str) -> list | None:
    from .parseproof import exe_path
    lines = []
    for name, (cols, rows) in tables.items():
        lines.append("TABLE " + " ".join([name, *cols]))
        lines += ["ROW " + "|".join(_enc(v) for v in r) for r in rows]
    lines += ["SQL", sql]
    r = subprocess.run([str(exe_path()), "eval"], input="\n".join(lines), capture_output=True,
                       text=True, timeout=60, check=False)
    if r.returncode != 0:
        return None
    return [tuple(_dec(x) for x in ln[4:].split("|")) if ln[4:] else ()
            for ln in r.stdout.splitlines() if ln.startswith("ROW")]


def _norm(v):
    if isinstance(v, float) and v.is_integer():
        return v                                  # 2.0 is not 2: DuckDB's `/` is visible here
    return v


def duckdb_rows(tables: dict, sql: str) -> list:
    import duckdb
    con = duckdb.connect(":memory:")
    try:
        for name, (cols, rows) in tables.items():
            types = []
            for i, _c in enumerate(cols):
                vals = [r[i] for r in rows if r[i] is not None]
                if not vals:                  # all NULL: the column's kind is in its name
                    types.append("VARCHAR" if _c in ("s", "w_s") else "BIGINT")
                    continue
                types.append("BIGINT" if all(isinstance(x, int) and not isinstance(x, bool)
                                             for x in vals) else
                             "BOOLEAN" if all(isinstance(x, bool) for x in vals) else
                             "VARCHAR")
            con.execute(f"create table {name} (" + ", ".join(
                f"{c} {t}" for c, t in zip(cols, types)) + ")")
            if rows:
                con.executemany(f"insert into {name} values ({', '.join('?' * len(cols))})", rows)
        return [tuple(_norm(v) for v in r) for r in con.execute(sql).fetchall()]
    finally:
        con.close()


def warehouse_sql(tables: dict, sql: str) -> str:
    def lit(v):
        if v is None:
            return "NULL"
        if isinstance(v, bool):
            return "TRUE" if v else "FALSE"
        if isinstance(v, int):
            return str(v)
        return "'" + str(v).replace("'", "''") + "'"
    ctes = []
    for name, (cols, rows) in tables.items():
        sels = [("SELECT " + ", ".join(f"{lit(v)} AS {c}" for c, v in zip(cols, r))) for r in rows]
        ctes.append(f"{name} AS (" + " UNION ALL ".join(sels) + ")")
    return f"WITH {', '.join(ctes)}, __assay_q AS ({sql}) SELECT * FROM __assay_q"


def compare(a: list | None, b: list) -> tuple[str, str]:
    from .ledger import BROKEN, HOLDING, UNCHECKED
    if a is None:
        return UNCHECKED, "Lean could not evaluate it"
    ca, cb = Counter(a), Counter(b)
    if ca == cb:
        return HOLDING, f"{len(a)} row(s), identical"
    only_l = list((ca - cb).elements())[:3]
    only_e = list((cb - ca).elements())[:3]
    return BROKEN, f"Lean's meaning gives {only_l} where the engine gives {only_e}"


# ------------------------------------------------------------------ the suite

def run(store, engine: str = "duckdb", runner=None, n_random: int = 0, seed: int = 7,
        say=print) -> dict:
    """Run every construct (and `n_random` random cases) through Lean and the engine."""
    import duckdb
    from .ledger import BROKEN, HOLDING
    store.con.execute(DDL)
    rows, out = [], {}
    now = datetime.now(timezone.utc)
    version = duckdb.__version__ if engine == "duckdb" else "via dbt"
    cases = [(name, T, sql) for name, (sql, _w) in CONSTRUCTS.items()]
    cases += [(f"random:{i}", *case) for i, case in enumerate(random_cases(n_random, seed))]
    for name, tables, sql in cases:
        lean = lean_rows(tables, sql)
        try:
            if engine == "duckdb":
                eng = duckdb_rows(tables, sql)
            else:
                got = runner(warehouse_sql(tables, sql))
                if got.failed:
                    raise RuntimeError(got.why)
                eng = [tuple(r.values()) for r in got.rows]
        except Exception as e:                                   # noqa: BLE001
            st, detail = "unchecked", f"the engine could not run it: {str(e)[:200]}"
        else:
            st, detail = compare(lean, eng)
        out[name] = (st, detail)
        if not name.startswith("random:"):
            rows.append((name, engine, version, st, detail, now))
    rnd = {k: v for k, v in out.items() if k.startswith("random:")}
    if rnd:
        bad = [(k, v[1]) for k, v in rnd.items() if v[0] == BROKEN]
        rows.append(("random_differential", engine, version,
                     BROKEN if bad else HOLDING,
                     (f"{len(bad)} of {len(rnd)} random cases differ, e.g. {bad[0][1]}"
                      if bad else f"all {len(rnd)} random cases agree (seed {seed})"), now))
    store.con.executemany("insert or replace into conformance values (?,?,?,?,?,?)", rows)
    by = Counter(v[0] for k, v in out.items() if not k.startswith("random:"))
    return {"engine": engine, "version": version, "by_status": dict(by),
            "constructs": {k: {"status": v[0], "detail": v[1]} for k, v in out.items()
                           if not k.startswith("random:")},
            "random": ({"cases": len(rnd), "differ": sum(1 for v in rnd.values()
                                                          if v[0] == BROKEN)} if rnd else None)}


def random_cases(n: int, seed: int) -> list:
    """Small tables with duplicates, NULLs and ties, and queries over them in the fragment."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        def table(cols):
            return (cols, [[rng.choice([None, 1, 2, 2, 3]) if c != "s" else
                            rng.choice([None, "a", "b", "ab"]) for c in cols]
                           for _ in range(rng.randint(0, 6))])
        tables = {"a": table(["k", "v", "s"]), "b": table(["k", "w"])}
        pred = rng.choice(["a.v > 1", "a.v = b.w", "a.s like 'a%'", "a.v in (1, NULL)",
                           "a.v not in (2, 3)", "a.v is null", "not (a.v < 2)",
                           "a.v between 1 and 2", "coalesce(a.v, 0) = 0"])
        shape = rng.choice(["join", "left", "group", "pick", "plain", "union"])
        if shape == "join":
            sql = f"select a.k, a.v, b.w from a join b on a.k = b.k where {pred}"
        elif shape == "left":
            sql = f"select a.k, b.w from a left join b on a.k = b.k and {pred}"
        elif shape == "group":
            sql = ("select a.k, count(*) as n, count(a.v) as c, sum(a.v) as s, min(a.s) as m "
                   "from a group by a.k")
        elif shape == "pick":
            sql = ("select a.k from a qualify row_number() over "
                   "(partition by a.k order by a.v) = 1")
        elif shape == "union":
            sql = "select a.k from a union all select b.k from b"
        else:
            sql = (f"select a.k, case when {pred.replace('b.w', '1')} then 1 else 0 end as c "
                   f"from a")
        out.append((tables, sql))
    return out


def status_of(store, construct: str, engine: str) -> tuple[str, str]:
    """(status, detail) of a construct on an engine, latest first; unchecked when never run."""
    try:
        store.con.execute(DDL)
        got = store.con.execute(
            "select status, detail, engine_version from conformance where construct = ? and "
            "engine = ? order by checked_at desc limit 1", [construct, engine]).fetchone()
    except Exception:                                            # noqa: BLE001
        got = None
    if got is None:
        return "unchecked", f"not run on {engine} yet"
    return got[0], f"{got[1]} ({engine} {got[2]})"
