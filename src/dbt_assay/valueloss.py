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
LOAD_COLUMNS = ("_dlt_load_id", "_loaded_at", "loaded_at", "_airbyte_extracted_at",
                "_fivetran_synced", "_etl_loaded_at", "ingested_at")
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


def _load_column(project, schema, relation: str) -> str:
    uid = _source_uid(project, relation)
    names = []
    if schema is not None and uid:
        try:
            names = [x.lower() for x in schema.columns(uid).names]
        except Exception:                                        # noqa: BLE001
            names = []
    return next((c for c in LOAD_COLUMNS if c in names), "")


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
    loadc = {r: _load_column(project, schema, r) for r in rels}
    text = "string" if dialect == "bigquery" else "varchar"
    fps_res = probe_mod.run_many([probe_mod.Statement(
        "select count(*) as n" + (f", max(cast({loadc[r]} as {text})) as v" if loadc[r] else "")
        + f" from {r}", caller="assay.valueloss", kind="count", limit=1, relation=r)
        for r in rels], project_dir, profiles_dir, dbt_bin, dialect)
    fp: dict = {}
    for r, res in zip(rels, fps_res):
        if not res.failed and res.rows:
            row = res.rows[0]
            fp[r] = f"{row.get('n')}|{row.get('v') or ''}"
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
    started = _t.monotonic()
    counted: dict = {}
    left = list(todo)
    while left and _t.monotonic() - started < max_seconds:
        chunk, left = left[:12], left[12:]
        stmts = [probe_mod.Statement(
            "select " + ", ".join(f"{_lost_expr(c)} as l{i}, {_present_expr(c)} as p{i}"
                                  for i, c in enumerate(by_rel[r])) + f" from {r}",
            caller="assay.valueloss", kind="count", limit=1, relation=r,
            columns=sorted({c.source_column for c in by_rel[r]})) for r in chunk]
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
