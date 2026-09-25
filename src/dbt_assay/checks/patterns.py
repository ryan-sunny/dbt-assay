"""Findings from the counted SQL shapes the parse records (sqlpatterns.py). One finding per model
and kind, with every occurrence in the evidence.

Counted, not judged: each fires on the text. Where the text cannot settle whether it is a defect
(is a rolling window meant, can the subquery return NULL), the finding says what would, and the
severity follows what is known: a view that reads the clock drifts every day with nothing rebuilt;
a `NOT IN` over a column a test keeps non-null is quiet.
"""
from __future__ import annotations

from .structural import Finding

DRIFTS_UNBUILT = ("view", "ephemeral")


def _not_null_known(project, relation: str, column: str) -> bool:
    """A not_null test, a unique+not_null key, or a contract says the column is never NULL."""
    if not relation or not column:
        return False
    uid = next((u for u, m in project.models.items() if m.name.lower() == relation), None)
    if uid is None:
        uid = next((u for u, s in project.sources.items() if s.name.lower() == relation), None)
    if uid is None:
        return False
    for t in project.tests:
        if t.tests_model == uid and (t.kind or "") == "not_null" \
                and (t.column or "").lower() == column:
            return True
    return False


def run_all(project, digests) -> list[Finding]:
    out: list[Finding] = []
    for uid, d in digests.items():
        if not d.ok or not getattr(d, "patterns", None):
            continue
        m = project.models.get(uid)
        if m is None or getattr(m, "is_installed_package", False):
            continue
        by: dict = {}
        for p in d.patterns:
            by.setdefault(p["kind"], []).append(p)
        mat = (m.materialized or "").lower()
        exposed = bool(project.exposures_of(uid)) if hasattr(project, "exposures_of") else False
        kw = {"subject": uid, "subject_name": m.name, "file": m.path}

        clock = [c for c in by.get("clock", []) if not c["guard"]]
        # A model whose only clock reads are bounds against future-dated rows is not reported:
        # the bound changes nothing unless the data holds dates from the future.
        if clock:
            base = 3 if (mat in DRIFTS_UNBUILT or exposed) else 2
            where = sorted({c["where"] for c in clock})
            out.append(Finding(
                check="output_depends_on_the_clock", **kw,
                summary=(f"reads today's date in {len(clock)} place(s)"
                         + (f"; as a {mat} its rows change every day with nothing rebuilt"
                            if mat in DRIFTS_UNBUILT else "")),
                detail=("A rebuild of unchanged code on unchanged data gives different rows, "
                        "so the model is not reproducible and a test or a golden pinned to its "
                        "output breaks with the calendar. Pass the date in (a project `as_of()` "
                        "macro reading `var('as_of')`, defaulting to the current date) so a "
                        "build, a test and a golden can name the day they represent. "
                        + "Only the SQL is read: code outside dbt that reads the clock is not "
                          "seen."),
                base=base,
                evidence={"uses": [c["sql"] for c in clock][:8], "where": where,
                          "materialized": mat}))

        aggs = by.get("order_sensitive_aggregate", [])
        if aggs:
            picks = [a for a in aggs if a["function"] in ("anyvalue", "first", "last")]
            out.append(Finding(
                check="order_sensitive_aggregate", **kw,
                summary=(f"{len(aggs)} aggregate(s) whose result depends on the order rows "
                         f"arrive in, with no ORDER BY"),
                detail=("string_agg, array_agg and list build their output in arrival order, "
                        "and first, last and any_value take whichever row came first. Without "
                        "an ORDER BY inside the aggregate, the same data can give a different "
                        "value on the next build. Add `order by` on a total key inside the "
                        "call, or aggregate something order does not change."),
                base=2 if len(picks) < len(aggs) else 1,
                evidence={"calls": [a["sql"] for a in aggs][:8]}))

        one = by.get("one_sided_normalisation", [])
        if one:
            out.append(Finding(
                check="join_key_normalised_on_one_side", **kw,
                summary=f"{len(one)} join key(s) cleaned on one side only",
                detail=("One side of the join is lowered, uppered or trimmed and the other is "
                        "not, so a value that differs only in case or spacing silently fails to "
                        "match and the row drops (inner) or loses its match (left). Clean both "
                        "sides the same way, ideally once in staging."),
                base=2, evidence={"conditions": [o["sql"] for o in one][:8]}))

        notin = [n for n in by.get("not_in_subquery", [])
                 if not _not_null_known(project, n["from"], n["column"])]
        if notin:
            out.append(Finding(
                check="not_in_over_a_nullable_subquery", **kw,
                summary=f"{len(notin)} NOT IN (subquery) whose column nothing keeps non-null",
                detail=("If the subquery returns a single NULL, `x NOT IN (...)` is never true "
                        "and the filter keeps no rows. Nothing here (a not_null test, a key) "
                        "says the column cannot be NULL. Use NOT EXISTS, or filter the NULLs "
                        "out inside the subquery."),
                base=2, evidence={"conditions": [n["sql"] for n in notin][:8],
                                  "columns": [f"{n['from']}.{n['column']}" for n in notin][:8]}))

        undone = by.get("left_join_undone", [])
        if undone:
            out.append(Finding(
                check="left_join_undone_by_where", **kw,
                summary=(f"a WHERE on the right side of {len(undone)} LEFT JOIN condition(s) "
                         f"turns it into an inner join"),
                detail=("Rows with no match have NULLs on the right, and a WHERE comparing a "
                        "right-side column drops them, so the LEFT JOIN keeps only matches. If "
                        "that is meant, write an inner join; if not, move the condition into "
                        "the ON clause."),
                base=2, evidence={"conditions": [u["sql"] for u in undone][:8],
                                  "joined": sorted({u["joined"] for u in undone})}))

        lim = by.get("limit", [])
        if lim:
            unordered = [x for x in lim if not x["ordered"]]
            out.append(Finding(
                check="limit_in_a_model", **kw,
                summary="LIMIT in the model" + (", with no ORDER BY" if unordered else ""),
                detail=("A model is a table other models trust to be complete. A LIMIT caps it, "
                        "and without an ORDER BY on a unique key which rows survive changes from "
                        "build to build. If it is a sample for development, put it behind a "
                        "target check; if it picks top-N, order by a total key."),
                base=2 if unordered else 1,
                evidence={"limits": [x["sql"] for x in lim][:4],
                          "ordered": not unordered}))
    for f in out:
        b = project.blast_radius(f.subject)
        f.descendants, f.marts = b["descendants"], b["marts"]
    return out
