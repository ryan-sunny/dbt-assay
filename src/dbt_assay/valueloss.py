"""Values present in a source that the model reading it never sees, counted.

*** HALF OF A COLUMN WENT MISSING AND EVERY ROW WAS STILL THERE. *** (sunny-data, 2026-09-25)
`raw_az_water.usfs_wui_az.huden1990__v_double` held 86,105 of 169,989 values: the loader split a
mixed-type column in two, the model read the base column, and 51% of the values never reached the
paid Arizona report. `hop_drops_most_rows` counts ROWS, so it saw nothing: every row arrived, with
a NULL where the value had been.

The cause does not matter to the count, so the check is general: a loader's type split (dlt's
`<col>__v_<type>`), a `try_cast` turning text it cannot parse into NULL, a regex that does not
match, a `nullif`. For each column a model takes from ONE source column through an expression, it
counts the source values that are non-null before the expression and NULL after it, with a sample
of them. Counted, never judged; it runs under `check --verify`, through the project's own dbt.
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from .checks.structural import Finding

SAMPLE = 5


@dataclass
class Candidate:
    model: str                 # uid
    model_name: str
    column: str                # the model's output column ("" for an unread variant)
    relation: str              # the source relation, as the compiled SQL names it
    source_column: str
    expression: str            # over the bare source column; "" for an unread variant
    cause: str                 # transform | variant


def _source_uid(project, relation: str) -> str | None:
    rel = relation.replace('"', "").lower()
    for uid, s in project.sources.items():
        name = f"{getattr(s, 'schema', '') or getattr(s, 'source_name', '')}.{s.name}".lower()
        if rel == s.name.lower() or rel.endswith("." + s.name.lower()) or rel == name:
            return uid
    return None


def _bare(expr_sql: str, dialect: str) -> tuple[str, str] | None:
    """(the expression with its column unqualified, that column) when it reads exactly one
    column and is not that column itself; None otherwise."""
    try:
        e = sqlglot.parse_one(expr_sql, read=dialect)
    except Exception:                                            # noqa: BLE001
        return None
    cols = {c.name.lower() for c in e.find_all(exp.Column) if c.name}
    if len(cols) != 1 or isinstance(e, exp.Column):
        return None
    if e.find(exp.AggFunc) or e.find(exp.Window) or e.find(exp.Coalesce):
        return None          # an aggregate changes the grain; a coalesce fills NULLs on purpose
    for c in e.find_all(exp.Column):
        c.set("table", None)
    return e.sql(dialect=dialect), next(iter(cols))


def candidates(project, digests, schema=None) -> list[Candidate]:
    dialect = getattr(project, "dialect", "duckdb") or "duckdb"
    out: list[Candidate] = []
    for uid, d in digests.items():
        m = project.models.get(uid)
        if m is None or not d.ok or getattr(m, "is_installed_package", False):
            continue
        rels = sorted(set(d.relations))
        if len(rels) != 1:
            continue
        src = _source_uid(project, rels[0])
        if src is None:
            continue
        for col, expr_sql in (d.output_exprs or {}).items():
            got = _bare(expr_sql, dialect)
            if got is None:
                continue
            out.append(Candidate(uid, m.name, col, rels[0], got[1], got[0], "transform"))
        # a loader's type split: `x` and `x__v_<type>` in the source, and the model reads x only
        if schema is not None:
            try:
                names = [c.lower() for c in schema.columns(src).names]
            except Exception:                                    # noqa: BLE001
                names = []
            read = set(d.referenced_columns or [])
            for v in names:
                if "__v_" not in v:
                    continue
                base = v.split("__v_")[0]
                if base in names and base in read and v not in read:
                    out.append(Candidate(uid, m.name, base, rels[0], v, "", "variant"))
    return out


def _lost_expr(c: Candidate) -> str:
    """Portable: no FILTER clause (Snowflake and BigQuery have none)."""
    if c.cause == "variant":
        return f"sum(case when {c.source_column} is not null then 1 else 0 end)"
    return (f"sum(case when {c.source_column} is not null and ({c.expression}) is null "
            f"then 1 else 0 end)")


def _present_expr(c: Candidate) -> str:
    return "count(*)" if c.cause == "variant" else f"count({c.source_column})"


def _sample_sql(c: Candidate, dialect: str = "duckdb") -> str:
    cond = (f"{c.source_column} is not null" if c.cause == "variant" else
            f"{c.source_column} is not null and ({c.expression}) is null")
    text = "string" if dialect == "bigquery" else "varchar"
    return (f"select distinct cast({c.source_column} as {text}) as v from {c.relation} "
            f"where {cond} limit {SAMPLE}")


# *** 441 SECONDS, THEN 524, FOR THE SAME COUNTS. *** (RC box, 25 GB, ~200 sources) Every source
# was scanned in full on every run. A source's values only change when its data does, so each run
# first reads a fingerprint (the row count, and the newest load id where the loader writes one:
# cheap, one statement per source), counts again only where it moved or the last count is a week
# old, oldest first, inside a time budget, and reads the rest from the last count.
RECOUNT_DAYS = 7

DDL = """
create table if not exists value_loss (
    relation     varchar,
    column_key   varchar,
    fingerprint  varchar,
    lost         bigint,
    present      bigint,
    sample       varchar,
    counted_at   timestamp,
    primary key (relation, column_key)
);
"""


def _key(c: Candidate) -> str:
    return f"{c.cause}|{c.source_column}|{c.expression}"


def _norm(rel: str) -> tuple:
    parts = [x.strip('"`[]').lower() for x in str(rel).split(".") if x]
    return tuple(parts[-2:]) if len(parts) >= 2 else tuple(parts)


def fingerprints(rels: list[str], project, probe_mod, project_dir: str, profiles_dir,
                 dbt_bin: str, dialect: str) -> dict:
    """{relation: fingerprint} from the engine's own table metadata, in one or two statements for
    every source at once. Never a column scan (RC box: `max(_dlt_load_id)` per source was the
    scan it was meant to avoid). DuckDB: `duckdb_tables()` row estimates and dlt's `_dlt_loads`;
    Snowflake: information_schema row_count and last_altered; BigQuery: `__TABLES__`; Postgres
    and Redshift: the statistics views. Anything else: `count(*)`, which reads metadata."""
    want = {_norm(r): r for r in rels}
    stmts = []
    d = (dialect or "duckdb").lower()
    if d == "duckdb":
        stmts.append(("tables", ("select schema_name as s, table_name as t, "
                                "cast(estimated_size as varchar) as v from duckdb_tables()")))
        stmts.append(("loads", ("select schema_name as s from duckdb_tables() "
                               "where table_name = '_dlt_loads'")))
    elif d == "snowflake":
        dbs = sorted({str(r).replace('"', "").split(".")[0] for r in rels if r.count(".") >= 2})
        for db in dbs:
            stmts.append(("tables", (f"select lower(table_schema) as s, lower(table_name) as t, "
                                    f"row_count || '|' || to_varchar(last_altered) as v "
                                    f"from {db}.information_schema.tables")))
    elif d == "bigquery":
        sets = sorted({".".join(str(r).replace("`", "").split(".")[:-1]) for r in rels
                       if r.count(".") >= 1})
        for ds in sets:
            stmts.append(("tables", (f"select lower('{ds.split('.')[-1]}') as s, "
                                    f"lower(table_id) as t, cast(row_count as string) || '|' || "
                                    f"cast(last_modified_time as string) as v "
                                    f"from `{ds}.__TABLES__`")))
    elif d in ("postgres", "redshift"):
        stmts.append(("tables", ("select lower(schemaname) as s, lower(relname) as t, "
                                "cast(n_live_tup + n_tup_ins + n_tup_upd + n_tup_del as varchar) "
                                "as v from pg_stat_user_tables")))
    out: dict = {}
    if stmts:
        got = probe_mod.run_many([probe_mod.Statement(sql, caller="assay.valueloss",
                                                      kind="metadata", limit=100000)
                                  for _k, sql in stmts], project_dir, profiles_dir, dbt_bin,
                                 dialect)
        loads: dict = {}
        for (kind, _sql), res in zip(stmts, got):
            if res.failed:
                continue
            if kind == "tables":
                for row in res.rows:
                    k = (str(row.get("s") or "").lower(), str(row.get("t") or "").lower())
                    if k in want:
                        out[want[k]] = str(row.get("v") or "")
            elif kind == "loads" and res.rows:
                # dlt's own record: each dlt schema's newest completed load, and which tables that
                # schema holds (its latest `_dlt_version`). A load that did not touch a table still
                # recounts it, which is the safe side. (Measured on the box: 93 tables, 0.01s.)
                datasets = [str(row.get("s")) for row in res.rows if row.get("s")]
                q = " union all ".join(
                    f"select * from (with v as (select schema_name, schema, row_number() over "
                    f"(partition by schema_name order by inserted_at desc) rn "
                    f"from {ds}._dlt_version), l as (select schema_name, max(load_id) as "
                    f"last_load from {ds}._dlt_loads where status = 0 group by 1) "
                    f"select '{ds}' as ds, v.schema_name as sn, l.last_load as v, "
                    f"v.schema as js from v join l using (schema_name) where rn = 1)"
                    for ds in datasets[:50])
                lr = probe_mod.run_many([probe_mod.Statement(
                    q, caller="assay.valueloss", kind="metadata", limit=10000)],
                    project_dir, profiles_dir, dbt_bin, dialect)[0] if q else None
                if lr is not None and not lr.failed:
                    import json as _json
                    for x in lr.rows:
                        try:
                            tables = (_json.loads(x.get("js") or "{}") or {}).get("tables") or {}
                        except (ValueError, TypeError):
                            continue
                        for t in tables:
                            if not str(t).startswith("_dlt"):
                                loads[(str(x.get("ds")).lower(), str(t).lower())] = \
                                    str(x.get("v") or "")
        for r in list(out):
            k = _norm(r)
            if k in loads:
                out[r] = "dlt:" + loads[k]          # the load id says it all
    missing = [r for r in rels if r not in out]
    if missing:
        got = probe_mod.run_many([probe_mod.Statement(
            f"select count(*) as n from {r}", caller="assay.valueloss", kind="count", limit=1,
            relation=r) for r in missing], project_dir, profiles_dir, dbt_bin, dialect)
        for r, res in zip(missing, got):
            if not res.failed and res.rows:
                out[r] = str(res.rows[0].get("n"))
    return out


def _cached(store) -> dict:
    if store is None:
        return {}
    try:
        store.con.execute(DDL)
        rows = store.con.execute("select relation, column_key, fingerprint, lost, present, "
                                 "sample, epoch(counted_at) from value_loss").fetchall()
    except Exception:                                            # noqa: BLE001
        return {}
    out: dict = {}
    for rel, key, fp, lost, present, sample, at in rows:
        out.setdefault(rel, {})[key] = {"fp": fp, "lost": lost, "present": present,
                                        "sample": sample, "at": at}
    return out


def measure(cands: list[Candidate], project, probe_mod, project_dir: str,
            profiles_dir: str | None, dbt_bin: str, *, store=None, schema=None,
            max_seconds: float = 120.0, say=None) -> list[Finding]:
    """Findings for each candidate that loses values: counted where the source's data moved,
    read from the last count where it did not."""
    import json as _j
    import time as _t
    if not cands:
        return []
    dialect = getattr(project, "dialect", "duckdb") or "duckdb"
    by_rel: dict = {}
    for c in cands:
        by_rel.setdefault(c.relation, []).append(c)
    rels = sorted(by_rel)
    cache = _cached(store)
    # 1. the fingerprints, one cheap statement per source
    started = _t.monotonic()
    fp = fingerprints(rels, project, probe_mod, project_dir, profiles_dir, dbt_bin, dialect)
    # 2. what needs counting: moved, never counted, a candidate new since, or a week old
    stale_before = _t.time() - RECOUNT_DAYS * 86400

    def fresh(r) -> bool:
        have = cache.get(r) or {}
        return r in fp and all(
            (have.get(_key(c)) or {}).get("fp") == fp[r]
            and (have.get(_key(c)) or {}).get("at") is not None
            and have[_key(c)]["at"] >= stale_before for c in by_rel[r])

    todo = [r for r in rels if r in fp and not fresh(r)]
    todo.sort(key=lambda r: (min(((cache.get(r) or {}).get(_key(c)) or {}).get("at") or 0.0
                                 for c in by_rel[r]), r))
    counted: dict = {}
    left = list(todo)
    while left and _t.monotonic() - started < max_seconds:
        chunk, left = left[:6], left[6:]
        # hard: no statement may run past what is left of the budget
        remaining = max(5, int(max_seconds - (_t.monotonic() - started)))
        stmts = [probe_mod.Statement(
            "select " + ", ".join(f"{_lost_expr(c)} as l{i}, {_present_expr(c)} as p{i}"
                                  for i, c in enumerate(by_rel[r])) + f" from {r}",
            caller="assay.valueloss", kind="count", limit=1, relation=r,
            columns=sorted({c.source_column for c in by_rel[r]}),
            timeout=max(5, remaining // 3))           # a batch may run 3x its longest
            for r in chunk]
        for r, res in zip(chunk, probe_mod.run_many(stmts, project_dir, profiles_dir, dbt_bin,
                                                    dialect)):
            if res.failed or not res.rows:
                continue
            row = res.rows[0]
            for i, c in enumerate(by_rel[r]):
                try:
                    counted[(r, _key(c))] = (int(row.get(f"l{i}") or 0),
                                             int(row.get(f"p{i}") or 0))
                except (TypeError, ValueError):
                    continue
    # 3. samples for what was counted and lost values
    hits = [(r, c) for r in by_rel for c in by_rel[r] if counted.get((r, _key(c)), (0, 0))[0]]
    samples: dict = {}
    if hits:
        got = probe_mod.run_many(
            [probe_mod.Statement(_sample_sql(c, dialect), caller="assay.valueloss",
                                 kind="sample", limit=SAMPLE, relation=r,
                                 columns=[c.source_column], sampled=True, sample_rows=SAMPLE)
             for r, c in hits], project_dir, profiles_dir, dbt_bin, dialect)
        for (r, c), res in zip(hits, got):
            samples[(r, _key(c))] = [str(next(iter(x.values()))) for x in (res.rows or [])][
                :SAMPLE] if not res.failed else []
    if store is not None and counted:
        from . import bulk
        store.con.execute(DDL)
        bulk.many(store.con, "insert or replace into value_loss values (?,?,?,?,?,?,now())",
                  [(r, k, fp.get(r, ""), lost, present, _j.dumps(samples.get((r, k), [])))
                   for (r, k), (lost, present) in counted.items()])
    if say is not None:
        n_skip = len(rels) - len(todo)
        say(f"--verify: values lost at a hop: {len(todo) - len(left)} source(s) counted, "
            f"{n_skip} unchanged since the last count"
            + (f", {len(left)} left for the next run (the {max_seconds:.0f}s budget)"
               if left else ""))
    # 4. the findings: what was counted now, else the last count
    out = []
    for r in rels:
        for c in by_rel[r]:
            k = _key(c)
            if (r, k) in counted:
                lost, present = counted[(r, k)]
                sample, when = samples.get((r, k), []), "now"
            else:
                prev = (cache.get(r) or {}).get(k)
                if not prev or not prev["lost"]:
                    continue
                lost, present = prev["lost"], prev["present"]
                when = _t.strftime("%Y-%m-%d %H:%M", _t.gmtime(prev["at"] or 0))
                try:
                    sample = _j.loads(prev["sample"] or "[]")
                except ValueError:
                    sample = []
            if not lost:
                continue
            out.append(_finding(project, c, lost, present, sample, when,
                                changed_since=(r in fp and (r, k) not in counted
                                               and ((cache.get(r) or {}).get(k) or {}).get("fp")
                                               != fp[r])))
    for f in out:
        b = project.blast_radius(f.subject)
        f.descendants, f.marts = b["descendants"], b["marts"]
    return out


def _finding(project, c: Candidate, lost: int, present: int, sample: list, when: str,
             changed_since: bool) -> Finding:
    share = lost / present if present else 0.0
    if c.cause == "variant":
        summary = (f"`{c.column}` misses {lost:,} value(s) the loader put in "
                   f"`{c.source_column}`, which nothing reads")
        detail = ("The loader split a mixed-type column: the base column holds only the values "
                  "that matched its first type, and the rest went to the variant. Declare the "
                  "column's type in the loader and reload (preferred), or read both in staging: "
                  "coalesce(x, x__v_<type>).")
    else:
        summary = (f"`{c.column}` loses {lost:,} of {present:,} value(s) ({share:.0%}) of "
                   f"`{c.source_column}` to `{c.expression[:80]}`")
        detail = ("These values are present in the source and NULL after the expression that "
                  "reads them: text a cast cannot parse, a pattern that does not match, a value "
                  "mapped away. Every row still arrives, so nothing counting rows notices. Fix the "
                  "source values, widen the expression, or keep the raw value beside the parsed "
                  "one so the loss is visible.")
    return Finding(
        check="values_lost_at_hop", subject=c.model, subject_name=c.model_name,
        file=project.models[c.model].path, summary=summary, detail=detail,
        base=3 if share >= 0.05 or lost >= 1000 else 2,
        evidence={"column": c.column, "source": c.relation, "source_column": c.source_column,
                  "expression": c.expression, "cause": c.cause, "lost": lost,
                  "present": present, "share": round(share, 4), "sample": sample,
                  "counted": when,
                  **({"data_changed_since": True} if changed_since else {})})
