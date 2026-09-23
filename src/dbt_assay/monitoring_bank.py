"""Four judged questions about the MONITORING, asked from what Elementary already counted.

*** MONITORING HAD ONE JUDGED FAMILY, AND IT NARROWED TO NOTHING. ***
`volume_contradicts_a_claim` only fires where a movement lines up with a claim, and on the field
warehouse none did. Everything else in the monitoring tier was a count: 2061% on one table, 233
models with marts downstream and no history, 63 monitors that last failed and stopped, 459 tests
that never produced a result. Each count is a pile somebody has to sort by hand, and the sorting is
exactly a judgment: a backfilled feed doubling is routine and a reference table doubling is not.

*** THEY JUDGE THE MONITORING, NEVER THE DATA. ***
Elementary's numbers are taken as fact and never re-litigated. The line the tier already draws
holds: assay asserts a monitor exists, is current and covers what matters. Every subject here is
filed under the model it is about, so its finding rests on its own family and cannot fail a build
until people have ruled on it.
"""
from __future__ import annotations

FAMILIES = ("movement_is_expected_for_this_kind_of_table", "monitor_covers_what_matters",
            "stale_monitor_still_matters", "test_never_ran_is_a_gap_or_a_leftover")


def _by_name(project) -> dict:
    return {m.name.lower(): u for u, m in project.models.items()}


def _model_facts(project, uid: str) -> dict:
    m = project.models[uid]
    radius = project.blast_radius(uid)
    reads = [project.name_of(p) for p in (m.parents or [])][:6]
    return {"model": m.name, "layer": getattr(m, "layer", "") or "",
            "materialized": m.materialized,
            "description": str(getattr(m, "description", "") or "")[:300],
            "reads": reads, "models_downstream": radius["descendants"],
            "marts_downstream": radius["marts"]}


def _prune(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def subjects(rep, project, ran_test_ids: set | None, threshold: float = 0.10,
             limit: int = 0) -> dict:
    """{family: [(uid, key, name, state)]}. Only what maps to a model in this project is asked:
    a finding has to be filed under something, and an unmapped table is counted, not guessed at."""
    from .elementary import unwatched
    names = _by_name(project)
    out: dict = {f: [] for f in FAMILIES}

    for v in rep.volumes:
        uid = names.get(v.table)
        if uid is None or v.change is None or abs(v.change) < threshold or v.stale():
            continue
        out["movement_is_expected_for_this_kind_of_table"].append((
            uid, f"{uid}::movement", f"{v.table} {v.change * 100:+.0f}%", _prune({
                **_model_facts(project, uid),
                "arrivals_per_bucket": {"previous": v.previous, "latest": v.latest,
                                        "change_percent": round(v.change * 100, 1),
                                        "what_this_counts": "rows that ARRIVED in one bucket, "
                                                            "not the size of the table"}})))

    for uid, name, desc, marts in unwatched(rep, project):
        out["monitor_covers_what_matters"].append((
            uid, f"{uid}::unwatched", name, _prune({
                **_model_facts(project, uid),
                "row_count_history": "none: no volume monitor covers this model"})))

    seen = set()
    for t in rep.stale_failures():
        uid = names.get(t.table)
        if uid is None or (uid, t.column, t.sub_type) in seen:
            continue
        seen.add((uid, t.column, t.sub_type))
        out["stale_monitor_still_matters"].append((
            uid, f"{uid}::stale::{t.kind}::{t.sub_type}::{t.column}",
            f"{t.table} {t.sub_type or t.kind}", _prune({
                **_model_facts(project, uid),
                "monitor": {"kind": t.kind, "sub_type": t.sub_type, "column": t.column,
                            "last_status": t.status,
                            "last_ran_days_ago": None if t.age_days is None
                            else round(t.age_days)}})))

    if ran_test_ids is not None:
        for t in project.tests:
            if t.unique_id in ran_test_ids or not t.tests_model \
                    or t.tests_model not in project.models:
                continue
            out["test_never_ran_is_a_gap_or_a_leftover"].append((
                t.tests_model, f"{t.tests_model}::neverran::{t.name}", t.name, _prune({
                    **_model_facts(project, t.tests_model),
                    "test": {"kind": t.kind, "column": t.column, "severity": t.severity,
                             "name": t.name},
                    "results_ever_recorded": 0})))

    for f in FAMILIES:
        out[f].sort(key=lambda s: (-s[3].get("marts_downstream", 0), s[1]))
        if limit:
            out[f] = out[f][:limit]
    return out


def ran_test_ids(runner, schema: str) -> set | None:
    """Every dbt test unique_id Elementary has a result for. None when it could not be read --
    which is not "none ran", and is never treated as one."""
    from .elementary import TEST_RESULTS
    res = runner(f"select distinct test_unique_id as t from {schema}.{TEST_RESULTS} "
                 f"where test_type = 'dbt_test'", 100000)
    if res.failed:
        return None
    return {str(r.get("t") or "") for r in res.rows if r.get("t")}
