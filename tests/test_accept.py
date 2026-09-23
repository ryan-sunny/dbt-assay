"""The fourth verdict: `accept`. Correct, and left as it is on purpose.

*** THERE WAS NO WAY TO SAY "THE FINDING IS RIGHT AND I ACCEPT IT". ***
`agree` left it outstanding forever, in "63 agreed, 0 fixed". `disagree` was a lie that removed a
true finding and told a working check it was wrong, which is the one signal allowed to decide
whether a check may gate. `accept` is neither.
"""
import json

import pytest
from typer.testing import CliRunner

from dbt_assay import reviewform
from dbt_assay.cli import app
from dbt_assay.config import Config, ThresholdError
from dbt_assay.outcomes import confirmed_and_fixed
from dbt_assay.store import Store

runner = CliRunner()


def _baseline(project_dir, tmp_path):
    store = str(tmp_path / "s.duckdb")
    r = runner.invoke(app, ["check", "-t", str(project_dir), "--store", store,
                            "--config", str(tmp_path), "--json"])
    doc = json.loads(r.output)
    return store, doc["findings"]


def _rule(project_dir, store, fid, *args):
    r = runner.invoke(app, ["review", "--store", store, "-t", str(project_dir),
                            "--finding", fid, *args])
    assert r.exit_code == 0, r.output
    return r


def _check_ids(project_dir, tmp_path, store):
    r = runner.invoke(app, ["check", "-t", str(project_dir), "--store", store,
                            "--config", str(tmp_path), "--json"])
    return {f["finding"] for f in json.loads(r.output)["findings"]}


def test_accept_needs_a_reason():
    s = Store(":memory:")
    with pytest.raises(ValueError, match="reason"):
        s.adjudicate("m::finding::x", "c", "c", "", "accept", note="")


def test_accept_suppresses_counts_as_right_and_is_not_outstanding(project_dir, tmp_path):
    store, findings = _baseline(project_dir, tmp_path)
    f = findings[0]
    _rule(project_dir, store, f["finding"], "--verdict", "accept", "--note", "intended here",
          "--by", "me", "--until", "2999-01-01")
    assert f["finding"] not in _check_ids(project_dir, tmp_path, store)

    s = Store(store)
    assert f["finding"] in s.accepted()
    assert f["finding"] not in s.ruled_findings("agree")
    fam_rows = [e for e in s.effectiveness() if e["family"] == f["check"]]
    assert fam_rows and fam_rows[0]["accept"] >= 1 and fam_rows[0]["disagree"] == 0
    assert fam_rows[0]["agreement"] == 1.0, "an accept says the check was right"
    s.close()


def test_an_accept_lapses_on_its_date(project_dir, tmp_path):
    store, findings = _baseline(project_dir, tmp_path)
    f = findings[0]
    _rule(project_dir, store, f["finding"], "--verdict", "accept", "--note", "for now",
          "--until", "2000-01-01")
    assert f["finding"] in _check_ids(project_dir, tmp_path, store), "expired: it comes back"


def test_the_latest_ruling_wins(project_dir, tmp_path):
    """Agreed, then accepted: no longer 'agreed and still here'."""
    store, findings = _baseline(project_dir, tmp_path)
    f = findings[0]
    _rule(project_dir, store, f["finding"], "--verdict", "agree", "--note", "real")
    s = Store(store)
    assert f["finding"] in s.ruled_findings("agree")
    s.close()
    _rule(project_dir, store, f["finding"], "--verdict", "accept", "--note", "leaving it")
    s = Store(store)
    assert confirmed_and_fixed(s, [])["agreed"] == 0
    assert f["finding"] in s.accepted()
    s.close()


def test_an_unknown_finding_is_refused(project_dir, tmp_path):
    store, _ = _baseline(project_dir, tmp_path)
    r = runner.invoke(app, ["review", "--store", store, "-t", str(project_dir),
                            "--finding", "nope", "--verdict", "accept", "--note", "x"])
    assert r.exit_code == 1


def test_an_accept_from_the_form_needs_its_reason_and_a_real_date():
    ok, bad = reviewform.load({"verdicts": [
        {"subject": "a", "question": "q", "verdict": "accept", "note": ""},
        {"subject": "b", "question": "q", "verdict": "accept", "note": "why", "until": "soon"},
        {"subject": "c", "question": "q", "verdict": "accept", "note": "why",
         "until": "2027-01-01", "findings": ["f1"]}]})
    assert [r["subject"] for r in ok] == ["c"] and ok[0]["until"] == "2027-01-01"
    assert len(bad) == 2


def test_the_waivers_pane_proposes_from_accepts_never_from_disagreements(tmp_path):
    s = Store(str(tmp_path / "s.duckdb"))
    s.adjudicate("model.p.m1::finding::a", "bbox_as_radius", "bbox_as_radius", "", "accept",
                 note="a grid cell, not a radius", who="me", until="2999-01-01")
    s.adjudicate("model.p.m2::finding::b", "bbox_as_radius", "bbox_as_radius", "", "disagree",
                 note="misread", who="me")
    rows = reviewform._waiver_rows(s, Config(), [])
    assert [(r["model"], r["check"], r["reason"]) for r in rows] == [
        ("m1", "bbox_as_radius", "a grid cell, not a radius")]
    s.close()


def test_a_named_waiver_covers_what_its_selector_selects(project_dir):
    from dbt_assay.manifest import Project
    project = Project.load(project_dir)
    cfg = Config.from_dict({"waivers": {"tildes": {
        "question": "regex_full_match", "reason": "known",
        "applies_to": {"select": "stg_bad_tilde stg_ok_tilde", "exclude": "stg_ok_tilde"}}}})
    assert cfg.waived("stg_bad_tilde", "regex_full_match", project, "model.p.stg_bad_tilde")
    assert not cfg.waived("stg_ok_tilde", "regex_full_match", project, "model.p.stg_ok_tilde")
    assert not cfg.waived("stg_bad_tilde", "other_check", project, "model.p.stg_bad_tilde")


@pytest.mark.parametrize("body, needle", [
    ({"question": "x", "reason": "r"}, "applies_to"),
    ({"question": "x", "applies_to": "m"}, "reason"),
    ({"reason": "r", "applies_to": "m"}, "question"),
    ({"question": "x", "reason": "r", "applies_to": "tagz:bad"}, "does not understand"),
    ({"question": "x", "reason": "r", "applies_to": "m", "until": "someday"}, "date"),
])
def test_a_named_waiver_refuses_what_it_cannot_honour(body, needle):
    with pytest.raises(ThresholdError, match=needle):
        Config.from_dict({"waivers": {"w": body}})


def test_one_keypress_is_one_verdict_toward_the_gate(tmp_path):
    """A card over three findings writes four rows, and the floor counts one."""
    from dbt_assay.cli import _record_one_verdict
    s = Store(str(tmp_path / "s.duckdb"))
    _record_one_verdict(s, "model.p.m", "test_cannot_fail", "agree", "", "real", "me",
                        findings=["a", "b", "c"])
    assert s.adjudication_counts()["test_cannot_fail"] == 1
    s.close()
