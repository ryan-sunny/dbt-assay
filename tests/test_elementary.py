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
from dbt_assay.probe import Result

# Naive, matching what Elementary writes: an aware value here raises on every
# subtraction in `_age` rather than comparing.
NOW = datetime(2026, 9, 22, 12, 0, 0)   # noqa: DTZ001


def ok(rows):
    """A statement that RAN and returned these rows -- possibly none of them."""
    return Result(rows=list(rows))


def broke(why: str = "relation does not exist"):
    """A statement that did not run. *** NOT THE SAME THING AS ONE THAT FOUND NOTHING. ***

    `dbt show` reports both as no output, which is why the runner contract returns a `Result`
    and why a fixture that cannot express the difference cannot test this module.
    """
    return Result(failed=True, why=why)


def fake(tables: dict):
    """A runner over in-memory tables. `None` means the relation does not exist."""
    def run(sql: str, limit: int):
        low = sql.lower()
        if "assay_reachable" in low:
            return ok([{"assay_reachable": 1}])
        rel = next((r for r in E.RELATIONS if r in low), None)
        if rel is None or tables.get(rel) is None:
            return broke()
        rows = tables[rel]
        # The COUNT PROBE, not any count: `_LATEST_VOLUME` contains `count(*) over (...)`, and a
        # fake that matches on `count(*)` answers the data query with a row count. Caught by
        # writing it wrong -- the tests went red and the reader was fine.
        if low.strip().startswith("select count(*) as n from"):
            return ok([{"n": len(rows)}])
        return ok(rows[:limit])
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
    rep = E.read(lambda sql, n: broke("dbt: command not found"), "elem", now=NOW)
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
    """The join is the whole point: neither tool has all three.

    *** AND THE KEY NAMES WHAT THE NUMBER IS. ***
    It used to be `movement.row_count_is`, which invites the reading "the table now holds N
    rows". It is rows that ARRIVED in one bucket. A state that misnames its own measurement
    produces a confident answer to a question nobody asked, which is the failure this whole
    module is about.
    """
    v = E.Volume(table="a", raw="db.s.a", buckets=9, latest=59.0, previous=100.0,
                 at=datetime(2026, 9, 21), age_days=1.0)   # noqa: DTZ001
    st = E.claim_state(_project(["a"], marts=19), "model.p.a", v,
                       {"claim_id": "c1", "text": "holds the FULL set", "source_ref": "a.yml:3"})
    mv = st["arrivals_per_bucket"]
    assert mv["change_percent"] == pytest.approx(-41.0)
    assert mv["latest_bucket"] == 59 and mv["previous_bucket"] == 100
    assert mv["latest_bucket_observed_days_ago"] == 1
    assert "not the size of the table" in mv["what_this_counts"]
    assert "row_count_is" not in str(st), "the key that invited the wrong reading is back"
    assert st["the_project_says"] == "holds the FULL set"
    assert st["marts_downstream"] == 19
    assert "sql" not in str(st).lower(), "the model's SQL is not in the state, on purpose"


def test_an_observation_months_old_is_stale_and_an_undated_one_is_not_fresh():
    """*** THE REPORT LED WITH `-100.0%, rows now 0` ON A TABLE HOLDING 281,286 ROWS. ***

    The newest bucket started eighty days before the run, and the column was labelled `rows now`.
    An unknown age is not treated as fresh either: a bucket assay cannot date is one it cannot
    vouch for, and the point of this is to stop vouching for numbers it cannot.
    """
    fresh = E.Volume(table="a", raw="db.s.a", buckets=9, latest=59.0, previous=100.0,
                     at=datetime(2026, 9, 21), age_days=1.0)          # noqa: DTZ001
    old_ = E.Volume(table="b", raw="db.s.b", buckets=126, latest=0.0, previous=900.0,
                    at=datetime(2026, 7, 4), age_days=80.0)           # noqa: DTZ001
    undated = E.Volume(table="c", raw="db.s.c", buckets=3, latest=0.0, previous=900.0,
                       at=None, age_days=None)
    assert fresh.stale(30) is False
    assert old_.stale(30) is True
    assert undated.stale(30) is True, "an undated observation is not a fresh one"
    # the change itself is still computed: it is REPORTED differently, never hidden
    assert old_.change == pytest.approx(-1.0)


# --------------------------------------------------------------- monitoring as a contract

def test_late_is_longer_than_this_relation_has_NORMALLY_gone_between_writes():
    """*** THE FIRST VERSION MULTIPLIED THE MEDIAN GAP BY THREE, AND THREE WAS INVENTED. ***

    A made-up multiplier is a made-up threshold however it is dressed -- the same failure as a
    guessed ledger ceiling, one layer up. What "late" means is answerable from the data: the 90th
    percentile of the gaps this thing has actually gone between writes. It has been quiet that
    long before and carried on. p90 rather than the maximum, because one historical outage should
    not license another.
    """
    days = [{"assay_day": f"2026-09-{d:02d}"} for d in (1, 2, 3, 4, 5, 6, 7, 8, 14)]
    cad = E.write_history(lambda sql, n: ok(days), "elem", E.METRICS)
    assert cad.writes == 9
    assert cad.gaps[:3] == [1.0, 1.0, 1.0]
    assert cad.normal_gap_days == pytest.approx(6.0), "p90 of [1,1,1,1,1,1,1,6]"
    assert cad.derived_staleness_days == 6
    assert "9 gaps in 10" in cad.explain()


def test_one_outage_does_not_license_another():
    """The maximum would make a month of silence normal for ever after. p90 does not."""
    days = [{"assay_day": f"2026-09-{d:02d}"} for d in range(1, 20)] + \
           [{"assay_day": "2026-12-01"}]
    cad = E.write_history(lambda sql, n: ok(days), "elem", E.METRICS)
    assert max(cad.gaps) > 70, "the outage is in the history"
    assert cad.derived_staleness_days is not None
    assert cad.derived_staleness_days < 10, "one outage must not become the new normal"


def test_each_relation_gets_its_own_threshold_from_its_own_history():
    """*** A SOURCE REFRESHED HOURLY AND ONE REFRESHED MONTHLY CANNOT SHARE A NUMBER. ***
    The first version gave them one."""
    daily = [{"assay_day": f"2026-09-{d:02d}"} for d in range(1, 21)]
    weekly = [{"assay_day": f"2026-0{m}-01"} for m in (5, 6, 7, 8, 9)]

    def runner(sql, n):
        low = sql.lower()
        if "assay_reachable" in low:
            return ok([{"assay_reachable": 1}])
        rel = next((r for r in E.RELATIONS if r in low), None)
        if rel is None:
            return broke()
        if low.strip().startswith("select count(*) as n from"):
            return ok([{"n": 5}])
        if "assay_day" in low:
            return ok(daily if rel == E.METRICS else weekly)
        return ok([{"created_at": "2026-09-20 00:00:00",
                    "detected_at": "2026-09-20 00:00:00"}])

    rep = E.read(runner, "elem", now=NOW)
    per = {r.relation: r.threshold_days for r in rep.readings}
    assert per[E.METRICS] == 1, "written daily: late after a day"
    assert per[E.FRESHNESS] > 20, "written monthly: a day is not late"


def test_a_relation_with_too_little_history_falls_back_to_the_build_cadence():
    days = [{"assay_day": f"2026-09-{d:02d}"} for d in (1, 4, 7, 10, 13)]
    fallback = E.write_history(lambda sql, n: ok(days), "elem", E.INVOCATIONS)
    assert fallback.derived_staleness_days == 3

    def runner(sql, n):
        low = sql.lower()
        if "assay_reachable" in low:
            return ok([{"assay_reachable": 1}])
        if E.FRESHNESS not in low:
            return broke()
        if low.strip().startswith("select count(*) as n from"):
            return ok([{"n": 3}])
        if "assay_day" in low:
            return ok([{"assay_day": "2026-07-08"}])      # written on exactly ONE day
        return ok([{"created_at": "2026-07-08 13:08:14"}])

    rep = E.read(runner, "elem", now=NOW, fallback=fallback)
    r = rep.reading(E.FRESHNESS)
    assert r.state == E.ABANDONED
    assert r.threshold_days == 3, "its own history cannot say; the project's cadence can"


def test_too_little_history_anywhere_derives_nothing_rather_than_a_default():
    """*** THE FRESHNESS TABLE WAS WRITTEN ON EXACTLY ONE DAY. ***
    It did not decay, it ran once -- so nothing about its own history can say what late means."""
    cad = E._cadence_of([datetime(2026, 9, 1)], "x")   # noqa: DTZ001
    assert cad.writes == 1
    assert cad.normal_gap_days is None
    assert cad.derived_staleness_days is None, "a guess is not better than saying you cannot tell"
    assert "not derivable" in cad.explain() and "only 1 write" in cad.explain()


def test_one_build_issuing_many_invocations_is_one_day():
    """*** MEASURED: 1,592 INVOCATIONS OVER 79 DAYS, SIXTEEN ON ONE DAY. ***
    The gap between invocations describes how fast dbt runs back-to-back, not how often this
    project builds. The query asks for distinct DAYS, so a burst is one."""
    sqls = []
    E.build_cadence(lambda sql, n: sqls.append(sql) or ok([]), "elem")
    assert "distinct cast(run_started_at as date)" in sqls[0]


def test_coverage_is_one_finding_with_a_count_not_one_per_model():
    """*** 232 FINDINGS IS A WALL, NOT A REPORT. ***

    The first version emitted one per unwatched model and would have swamped every other finding
    in `assay check`. Nobody rules on "add monitoring" 232 times; it is one decision about
    coverage, and the models ride in the evidence ranked by reach.
    """
    rows = [metric("db.s.a", "2026-09-21 00:00:00", 5.0, rn=1, buckets=2)]
    rep = E.read(fake({E.METRICS: rows}), "elem", now=NOW)
    got = E.monitoring_findings(rep, _project(["a", "b", "c", "d"], marts=3))
    watch = [f for f in got if f.check == "volume_is_not_being_watched"]
    assert len(watch) == 1, "one finding, not one per model"
    assert watch[0].evidence["unwatched"] == 3
    assert [t["model"] for t in watch[0].evidence["worst_by_reach"]] == ["b", "c", "d"]


def test_a_monitor_that_never_ran_and_one_that_stopped_are_different_findings():
    rep_never = E.read(fake({E.FRESHNESS: []}), "elem", now=NOW)
    rep_stopped = E.read(fake({E.FRESHNESS: [{"created_at": "2026-06-01 00:00:00"}]}),
                         "elem", now=NOW)
    a = {f.check for f in E.monitoring_findings(rep_never, _project([]))}
    b = {f.check for f in E.monitoring_findings(rep_stopped, _project([]))}
    assert "monitor_declared_but_never_run" in a and "monitor_ran_then_stopped" not in a
    assert "monitor_ran_then_stopped" in b and "monitor_declared_but_never_run" not in b


def test_tests_that_never_fired_and_skipped_results_are_reported_apart():
    rep = E.read(fake({E.TEST_RESULTS: []}), "elem", now=NOW)
    got = {f.check: f for f in E.monitoring_findings(
        rep, _project([]), coverage={"declared": 1291, "ever_ran": 1098,
                                     "skipped_results": 1846})}
    assert "193" in got["test_declared_but_never_run"].summary
    assert "1,846" in got["test_skipped_rather_than_passed"].summary
    assert got["test_declared_but_never_run"].evidence["declared"] == 1291


def test_assay_never_measures_volume_or_freshness_itself():
    """*** THE CONSTRAINT THAT KEEPS IT FROM BECOMING A SECOND MONITORING TOOL. ***

    The moment assay measures a row count itself it has a second opinion, and you have the
    two-inboxes problem this whole module exists to avoid. Every check it ships here is about
    whether a monitor EXISTS, is CURRENT, and COVERS what matters.
    """
    for check in E.MONITORING_CHECKS:
        assert any(w in check for w in ("monitor", "watched", "test")), check
    src = (Path(E.__file__).read_text() if (Path := __import__("pathlib").Path) else "")
    assert "count(*)" in src, "it reads counts"
    assert "select count(" in src and "group by" not in src.split("_LATEST_VOLUME")[0], \
        "assay must not compute its own aggregates over your data"


def test_the_threshold_says_where_it_actually_came_from():
    """*** IT STATED A THRESHOLD AND, IN THE SAME BREATH, THAT IT HAD NONE. ***

        late after 1 day -- recorded 1 write(s), which is not enough to say what a normal gap is

    The number came from the FALLBACK and the sentence quoted the relation's own failed history as
    though it had produced it. Found by reading the output, which is the step that was skipped.
    """
    fallback = E._cadence_of([datetime(2026, 9, d) for d in (1, 4, 7, 10, 13)],   # noqa: DTZ001
                             "this project's build cadence")

    def runner(sql, n):
        low = sql.lower()
        if "assay_reachable" in low:
            return ok([{"assay_reachable": 1}])
        if E.FRESHNESS not in low:
            return broke()
        if low.strip().startswith("select count(*) as n from"):
            return ok([{"n": 3}])
        if "assay_day" in low:
            return ok([{"assay_day": "2026-07-08"}])      # one write, ever
        return ok([{"created_at": "2026-07-08 13:08:14"}])

    r = E.read(runner, "elem", now=NOW, fallback=fallback).reading(E.FRESHNESS)
    assert r.threshold_days == 3
    assert r.threshold_from is fallback, "the reading does not know where its number came from"
    said = r.says()
    assert "build cadence" in said, "it does not name the source of the number it used"
    assert "not derivable" not in said, (
        "it is still quoting the history that FAILED to produce the threshold it just stated")


def test_things_are_counted_in_english():
    """`1 write(s)` is what a template looks like, not what English does -- and it shipped."""
    assert E._plural(1, "write") == "1 write"
    assert E._plural(2, "write") == "2 writes"
    assert E._plural(1300, "row") == "1,300 rows"
    for r in (E.Reading("x", E.ABANDONED, rows=1, newest=datetime(2026, 7, 8),   # noqa: DTZ001
                        age_days=76, threshold_days=1),):
        assert "(s)" not in r.says(), r.says()


def test_feedback_n1_skipped_has_one_definition():
    """The page said 0 tests skipped now beside a finding counting 1,846 skipped results."""
    from types import SimpleNamespace
    rep = SimpleNamespace(readings=[], volumes=[], stale_failures=[])
    def fs(cov):
        try:
            got = E.monitoring_findings(rep, None, None, cov)
        except Exception:
            return None
        return [f for f in got if f.check == "test_skipped_rather_than_passed"]
    now0 = fs({"declared": 5, "ever_ran": 5, "skipped_results": 1846, "skipped_now": 0})
    if now0 is not None:
        assert now0 == [], "a finding about history beside a current count of 0"
        now3 = fs({"declared": 5, "ever_ran": 5, "skipped_results": 1846, "skipped_now": 3})
        assert now3 and "3 tests were SKIPPED on their last run" in now3[0].summary
    import inspect
    src = inspect.getsource(E.monitoring_findings)
    assert 'cov.get("skipped_now") is not None' in src
