"""What the last build actually DID, and the cap that made every failure count an understatement."""
from __future__ import annotations

import json

import pytest

from dbt_assay import outcomes


def _project(limits: dict, statuses=()):
    """A manifest carrying tests with configured limits."""
    nodes = {}
    for uid, lim in limits.items():
        nodes[uid] = {"resource_type": "test", "name": uid.split(".")[-1],
                      "attached_node": "model.p.m", "original_file_path": "tests/x.sql",
                      "config": ({"limit": lim} if lim is not None else {})}
    nodes["model.p.m"] = {"resource_type": "model", "name": "m", "original_file_path": "m.sql",
                          "schema": "main", "config": {}}
    return type("P", (), {"raw": {"nodes": nodes}, "models": {"model.p.m": object()}})()


def _build(rows):
    b = outcomes.Build(generated_at="x", dbt_version="1.11.0")
    for uid, status, failures in rows:
        b.outcomes[uid] = outcomes.Outcome(uid, status, failures)
    return b


def test_a_failure_count_equal_to_the_limit_is_the_cap_not_the_count():
    """*** "GOT 500 RESULTS" AGAINST A REAL 6,251 IS AN ORDER OF MAGNITUDE, REPORTED AS A FACT. ***

    dbt applies a configured `limit` to the test query, so the number it prints is
    `min(real, limit)`. When those are equal the count says nothing about the real size except
    that it is at least that. On the field warehouse `+limit: 500` is set project-wide and all
    1,291 tests carry it, so every failure count it has ever printed may be an understatement.

    EQUALITY IS THE WHOLE TEST. A count below the limit is a true count and must be left alone,
    or the check cries wolf on every failing test in the project.
    """
    p = _project({"test.p.at_cap": 500, "test.p.under": 500, "test.p.no_limit": None})
    b = _build([("test.p.at_cap", "fail", 500), ("test.p.under", "fail", 499),
                ("test.p.no_limit", "fail", 900)])
    got = {c["test"] for c in outcomes.capped(p, b)}
    assert got == {"at_cap"}, got


def test_a_passing_test_and_a_missing_count_are_not_capped():
    """`failures` is absent on a pass and on a skip. Treating absent as zero, or as a cap, both
    turn silence into a claim."""
    p = _project({"test.p.a": 500})
    assert outcomes.capped(p, _build([("test.p.a", "pass", 0)])) == []
    assert outcomes.capped(p, _build([("test.p.a", "skipped", None)])) == []


def test_a_skipped_node_is_named_because_it_is_not_a_pass():
    """*** TWO MODEL ERRORS SKIPPED 108 MODELS IN A 643-NODE BUILD. ***

    And the alerting said "112 of 645 failed", with no distinction between failed and never ran.
    A test that did not execute asserted nothing.
    """
    p = _project({"test.p.a": None, "test.p.b": None})
    got = outcomes.skipped(p, _build([("test.p.a", "skipped", None), ("test.p.b", "pass", 0)]))
    assert [g["name"] for g in got] == ["a"]


def test_coverage_counts_assertions_that_actually_executed():
    """`assay tests` says which models have a test DECLARED. This says which had one RUN, and the
    gap between those two numbers is exactly what a skipped build hides."""
    p = _project({"test.p.ran": None, "test.p.skipped": None, "test.p.absent": None})
    c = outcomes.coverage(p, _build([("test.p.ran", "pass", 0),
                                     ("test.p.skipped", "skipped", None)]))
    assert c["tests_declared"] == 3
    assert c["tests_that_ran"] == 1
    assert c["tests_that_did_not_run"] == 2, "a skipped and an absent test both did not assert"


def test_a_missing_or_wrong_file_says_why_rather_than_reading_as_an_empty_build(tmp_path):
    """*** AN EMPTY Build READS EXACTLY LIKE A BUILD IN WHICH NOTHING RAN. ***

    And the usual cause is mundane and worth naming: `dbt compile` overwrites run_results with a
    compile-only result, so the file present is rarely the build somebody is asking about.
    """
    with pytest.raises(FileNotFoundError, match="OVERWRITES"):
        outcomes.read(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"metadata": {}}))
    with pytest.raises(ValueError, match="not a run_results"):
        outcomes.read(bad)


def test_it_reads_the_real_shape_dbt_writes(tmp_path):
    """Exercised against the keys dbt actually emits, taken from a real file."""
    f = tmp_path / "run_results.json"
    f.write_text(json.dumps({
        "metadata": {"dbt_version": "1.11.12", "generated_at": "2026-09-21T16:23:20Z"},
        "results": [{"unique_id": "test.p.a", "status": "fail", "failures": 500,
                     "message": "Got 500 results", "execution_time": 1.0},
                    {"unique_id": "test.p.b", "status": "pass", "failures": 0, "message": ""}]}))
    b = outcomes.read(f)
    assert b.n == 2 and b.dbt_version == "1.11.12"
    assert b.outcomes["test.p.a"].failures == 500
    assert b.outcomes["test.p.a"].status == "fail"


def test_a_finding_that_goes_while_its_model_is_unchanged_is_retired_not_fixed(tmp_path):
    """*** A RELEASE MOVED THE LOOP NUMBER. *** Reported from the field: 0.51.1 fixed a grain
    derivation, an agreed finding stopped firing on an unchanged model, and it counted as fixed."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from dbt_assay.outcomes import confirmed_and_fixed
    from dbt_assay.store import Store
    s = Store(str(tmp_path / "s.duckdb"))
    try:
        s.con.execute("insert into runs (run_id, started_at, project) values ('r1', ?, 'p')",
                      [datetime(2026, 9, 1, tzinfo=timezone.utc)])
        for fid, uid in (("same1", "model.p.same"), ("moved1", "model.p.moved")):
            s.con.execute("insert into findings (run_id, check_name, subject, summary, finding_id,"
                          " file_checksum) values ('r1', 'c', ?, ?, ?, 'v1')", [uid, fid, fid])
            s.adjudicate(f"{uid}::finding::{fid}", "c", "c", "", "agree", "", "real", "ryan",
                         source="human")
        project = SimpleNamespace(models={"model.p.same": SimpleNamespace(checksum="v1"),
                                          "model.p.moved": SimpleNamespace(checksum="v2")})
        got = confirmed_and_fixed(s, [], (), project)
        assert got["fixed"] == 1 and [r["finding"] for r in got["retired"]] == ["same1"]
        assert got["agreed"] == 1, "a retired finding is beside the loop, not in it"
    finally:
        s.close()
