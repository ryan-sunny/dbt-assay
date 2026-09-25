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


def measure(cands: list[Candidate], project, probe_mod, project_dir: str,
            profiles_dir: str | None, dbt_bin: str) -> list[Finding]:
    """Count each candidate through the project's dbt; a finding for each that lost values."""
    if not cands:
        return []
    dialect = getattr(project, "dialect", "duckdb") or "duckdb"
    # ONE statement per source relation, every candidate on it a pair of columns: a few dbt calls
    # for the whole project, not one per column.
    by_rel: dict = {}
    for c in cands:
        by_rel.setdefault(c.relation, []).append(c)
    rels = sorted(by_rel)
    stmts = [probe_mod.Statement(
        "select " + ", ".join(f"{_lost_expr(c)} as l{i}, {_present_expr(c)} as p{i}"
                              for i, c in enumerate(by_rel[r])) + f" from {r}",
        caller="assay.valueloss", kind="count", limit=1, relation=r,
        columns=sorted({c.source_column for c in by_rel[r]})) for r in rels]
    got = probe_mod.run_many(stmts, project_dir, profiles_dir, dbt_bin, dialect)
    hits = []
    for r, res in zip(rels, got):
        if res.failed or not res.rows:
            continue
        row = res.rows[0]
        for i, c in enumerate(by_rel[r]):
            try:
                lost, present = int(row.get(f"l{i}") or 0), int(row.get(f"p{i}") or 0)
            except (TypeError, ValueError):
                continue
            if lost > 0:
                hits.append((c, lost, present))
    if not hits:
        return []
    samples = probe_mod.run_many(
        [probe_mod.Statement(_sample_sql(c, dialect), caller="assay.valueloss", kind="sample",
                             limit=SAMPLE, relation=c.relation, columns=[c.source_column],
                             sampled=True, sample_rows=SAMPLE) for c, _l, _p in hits],
        project_dir, profiles_dir, dbt_bin, dialect)
    out = []
    for (c, lost, present), s in zip(hits, samples):
        sample = [str(next(iter(x.values()))) for x in (s.rows or [])][:SAMPLE] if not s.failed \
            else []
        share = lost / present if present else 0.0
        if c.cause == "variant":
            summary = (f"`{c.column}` misses {lost:,} value(s) the loader put in "
                       f"`{c.source_column}`, which nothing reads")
            detail = ("The loader split a mixed-type column: the base column holds only the "
                      "values that matched its first type, and the rest went to the variant. "
                      "Declare the column's type in the loader and reload (preferred), or read "
                      "both in staging: coalesce(x, x__v_<type>).")
        else:
            summary = (f"`{c.column}` loses {lost:,} of {present:,} value(s) "
                       f"({share:.0%}) of `{c.source_column}` to `{c.expression[:80]}`")
            detail = ("These values are present in the source and NULL after the expression "
                      "that reads them: text a cast cannot parse, a pattern that does not match, "
                      "a value mapped away. Every row still arrives, so nothing counting rows "
                      "notices. Fix the source values, widen the expression, or keep the raw "
                      "value beside the parsed one so the loss is visible.")
        out.append(Finding(
            check="values_lost_at_hop", subject=c.model, subject_name=c.model_name,
            file=project.models[c.model].path, summary=summary, detail=detail,
            base=3 if share >= 0.05 or lost >= 1000 else 2,
            evidence={"column": c.column, "source": c.relation, "source_column": c.source_column,
                      "expression": c.expression, "cause": c.cause, "lost": lost,
                      "present": present, "share": round(share, 4), "sample": sample}))
    for f in out:
        b = project.blast_radius(f.subject)
        f.descendants, f.marts = b["descendants"], b["marts"]
    return out
