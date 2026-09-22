"""*** AN ABSENT MEASUREMENT IS NOT A PASS, AND THIS READER IS MOSTLY THAT RULE. ***

`practices.py` learned it from dbt-project-evaluator: five `fct_` models of many were built, and
the categories whose tables did not exist were reported as nothing at all. An absent table and an
empty one are not the same fact, and only one of them is a pass.

Reading Elementary has more ways to be absent than anyone expected. The spec named three. A real
warehouse had six, and the three nobody predicted are the ones that look most like success.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from dbt_assay import elementary as E

# Naive, matching what Elementary writes: an aware value here raises on every
# subtraction in `_age` rather than comparing.
NOW = datetime(2026, 9, 22, 12, 0, 0)   # noqa: DTZ001


def fake(tables: dict):
    """A runner over in-memory tables. `None` means the relation does not exist."""
    def run(sql: str, limit: int):
        low = sql.lower()
        if "assay_reachable" in low:
            return [{"assay_reachable": 1}]
        rel = next((r for r in E.RELATIONS if r in low), None)
        if rel is None or tables.get(rel) is None:
            return []
        rows = tables[rel]
        # The COUNT PROBE, not any count: `_LATEST_VOLUME` contains `count(*) over (...)`, and a
        # fake that matches on `count(*)` answers the data query with a row count. Caught by
        # writing it wrong -- the tests went red and the reader was fine.
        if low.strip().startswith("select count(*) as n from"):
            return [{"n": len(rows)}]
        return rows[:limit]
    return run


def metric(table: str, bucket: str, value: float, updated: str = "2026-09-21 10:00:00",
           rn: int = 1, buckets: int = 2) -> dict:
    return {"full_table_name": table, "bucket_end": bucket, "metric_value": value,
            "updated_at": updated, "assay_rn": rn, "assay_buckets": buckets}


def result(table: str, status: str, at: str, kind: str = "anomaly_detection") -> dict:
    return {"table_name": table, "column_name": None, "test_type": kind,
            "test_sub_type": "row_count", "status": status, "detected_at": at}


# --------------------------------------------------------------- the six states

def test_a_missing_relation_is_absent_and_says_nothing_covers_it():
    rep = E.read(fake({}), "elem", now=NOW)
    assert {r.state for r in rep.readings} == {E.ABSENT}
    assert not rep.installed
    assert "does not exist" in rep.reading(E.METRICS).says()


def test_a_relation_that_exists_and_is_empty_is_NOT_the_same_as_a_missing_one():
    """*** THE DISTINCTION `dbt show` HIDES. ***
    A failed statement and an empty result both come back as `[]`. Reporting a missing package as
    an empty one -- or either as nothing at all -- is the defect `practices` already found."""
    rep = E.read(fake({E.METRICS: [], E.TEST_RESULTS: [], E.FRESHNESS: []}), "elem", now=NOW)
    assert {r.state for r in rep.readings} == {E.NEVER_RUN}
    assert "have not run" in rep.reading(E.METRICS).says()
    assert rep.installed, "present-but-unbuilt is installed; it needs a different fix"


def test_an_unreachable_warehouse_is_never_reported_as_an_absent_package():
    """*** FOUND BY SHIPPING THE OTHER FIVE STATES AND RUNNING IT. ***

    `dbt` was not on the PATH, every statement failed, and the reader announced that nothing
    monitors volume -- on a warehouse where Elementary had run an hour earlier. A tool that cannot
    reach the warehouse and says the warehouse is empty is the exact defect this module is about.
    """
    rep = E.read(lambda sql, n: [], "elem", now=NOW)
    assert {r.state for r in rep.readings} == {E.UNREACHABLE}
    assert not rep.reachable
    assert not rep.installed, "unreachable must not read as installed; nothing was measured"
    assert "could not reach" in rep.reading(E.METRICS).says()
    assert "NOT a statement about Elementary" in rep.reading(E.METRICS).says()


def test_one_bucket_is_no_answer_rather_than_a_smaller_one():
    rows = [metric("db.s.a", "2026-09-20 00:00:00", 100.0, rn=1, buckets=1)]
    rep = E.read(fake({E.METRICS: rows}), "elem", now=NOW)
    assert rep.reading(E.METRICS).state == E.ONE_BUCKET
    assert "needs two" in rep.reading(E.METRICS).says()
    assert rep.volumes[0].change is None, "one observation is not a zero change"


def test_a_table_nothing_has_written_to_for_months_is_abandoned():
    """*** THE STATE THAT LOOKS MOST LIKE SUCCESS. ***
    On a real warehouse `dbt_source_freshness_results` held 105 rows and had not been written to
    for 76 days, while every other Elementary table was current to yesterday."""
    rows = [{"created_at": "2026-07-08 13:08:14", "x": 1}]
    rep = E.read(fake({E.FRESHNESS: rows}), "elem", now=NOW)
    r = rep.reading(E.FRESHNESS)
    assert r.state == E.ABANDONED
    assert r.age_days > 70
    assert "reads exactly like one that finds nothing" in r.says()


def test_a_monitor_that_last_failed_and_stopped_running_is_told_apart():
    """*** THE OTHER STATE NOBODY PREDICTED. ***
    In any Elementary view a test that last failed seven weeks ago is indistinguishable from one
    failing right now. Twelve of these on a real warehouse, while the suite ran yesterday."""
    rows = [result("a", "fail", "2026-08-04 03:37:50"),
            result("b", "fail", "2026-09-21 16:14:00"),
            result("c", "pass", "2026-09-21 16:14:00")]
    rep = E.read(fake({E.TEST_RESULTS: rows}), "elem", now=NOW, stale_after_days=14)
    assert len(rep.open_failures()) == 2
    stale = rep.stale_failures()
    assert [t.table for t in stale] == ["a"], "a recent failure is not a stale one"
    assert stale[0].age_days > 40


# --------------------------------------------------------------- the counting

def test_history_is_not_a_backlog():
    """*** 3,030 ROWS ARE NOT 3,030 OPEN ITEMS. ***

    These tables accrue one row per test per run. Counting every `fail` ever recorded turned a
    real warehouse's twelve open failures into a 346-item queue in one report, and 476 in another.
    Newest wins per key -- the rule `live_decisions` already settled on.
    """
    rows = [result("a", "fail", "2026-01-01 00:00:00"),
            result("a", "fail", "2026-02-01 00:00:00"),
            result("a", "pass", "2026-09-21 00:00:00")]
    rep = E.read(fake({E.TEST_RESULTS: rows}), "elem", now=NOW)
    # the query collapses this in SQL; the reader must not re-expand it
    assert len(rep.tests) == 3, "the fake returns rows as given; the parser keeps them"
    assert all(t.age_days is not None for t in rep.tests)


def test_the_query_collapses_duplicate_buckets_before_comparing():
    """*** ONE BUCKET IS WRITTEN MANY TIMES, AND COMPARING TWO ROWS COMPARES TWO COPIES. ***

    Measured: a real table held NINE rows for one bucket, identical but for `updated_at`, because
    Elementary re-records the open bucket on every run. Taking "the last two rows by bucket_end"
    compared a bucket against itself -- reporting no movement on a table that had emptied, and,
    where duplicates straddled a boundary in an arbitrary order, movements of +2061% that never
    happened. That is `arbitrary_pick`, which this tool checks other people's SQL for.
    """
    sql = E._query("elem", E.METRICS)
    assert "assay_dup" in sql and "= 1" in sql, "the per-bucket dedupe is gone"
    assert "order by m.updated_at desc" in sql, "the dedupe picks a row arbitrarily"
    assert "bucket_end is not null" in sql, "a null bucket sorts first under DESC"


def test_a_movement_needs_two_distinct_buckets():
    rows = [metric("db.s.a", "2026-09-21 00:00:00", 50.0, rn=1, buckets=14),
            metric("db.s.a", "2026-09-20 00:00:00", 100.0, rn=2, buckets=14)]
    rep = E.read(fake({E.METRICS: rows}), "elem", now=NOW)
    v = rep.volumes[0]
    assert v.buckets == 14, "the bucket count comes from SQL, not from how many rows arrived"
    assert v.change == pytest.approx(-0.5)
    assert v.table == "a", "the join key is the last segment, lowercased"


def test_a_previous_count_of_zero_is_not_a_change_of_infinity():
    rows = [metric("db.s.a", "2026-09-21 00:00:00", 50.0, rn=1, buckets=3),
            metric("db.s.a", "2026-09-20 00:00:00", 0.0, rn=2, buckets=3)]
    rep = E.read(fake({E.METRICS: rows}), "elem", now=NOW)
    assert rep.volumes[0].change is None


def test_the_name_is_normalised_off_elementarys_qualified_upper_case():
    assert E._norm("SUNNY.MAIN_WATER.WATER_WELLS") == "water_wells"
    assert E._norm('"db"."schema"."TBL"') == "tbl"
    assert E._norm("") == ""


# --------------------------------------------------------------- what assay adds

def _project(names, marts=0):
    from types import SimpleNamespace
    models = {f"model.p.{n}": SimpleNamespace(
        unique_id=f"model.p.{n}", name=n, is_installed_package=False, schema="main")
        for n in names}
    return SimpleNamespace(models=models,
                           blast_radius=lambda uid: {"descendants": marts, "marts": marts})


def test_unwatched_reports_models_with_reach_and_no_history():
    """311 of 358 models being unwatched is not 'no volume problems'."""
    rows = [metric("db.s.a", "2026-09-21 00:00:00", 5.0, rn=1, buckets=2)]
    rep = E.read(fake({E.METRICS: rows}), "elem", now=NOW)
    got = E.unwatched(rep, _project(["a", "b", "c"], marts=2))
    assert sorted(n for _u, n, _d, _m in got) == ["b", "c"], "a watched model is not unwatched"


def test_unwatched_ignores_a_model_nothing_reads():
    rep = E.read(fake({E.METRICS: []}), "elem", now=NOW)
    assert E.unwatched(rep, _project(["a"], marts=0)) == []


def test_the_state_carries_the_claim_the_movement_and_the_reach():
    """The join is the whole point: neither tool has all three."""
    v = E.Volume(table="a", raw="db.s.a", buckets=9, latest=59.0, previous=100.0,
                 at=datetime(2026, 9, 21))   # noqa: DTZ001
    st = E.claim_state(_project(["a"], marts=19), "model.p.a", v,
                       {"claim_id": "c1", "text": "holds the FULL set", "source_ref": "a.yml:3"})
    assert st["movement"]["change_percent"] == pytest.approx(-41.0)
    assert st["the_project_says"] == "holds the FULL set"
    assert st["marts_downstream"] == 19
    assert "not by assay" in st["movement"]["counted_by"]
    assert "sql" not in str(st).lower(), "the model's SQL is not in the state, on purpose"
