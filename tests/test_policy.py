from dbt_assay.checks.structural import Finding
from dbt_assay.config import Config
from dbt_assay.judged import apply_policy
from dbt_assay.manifest import Project
from dbt_assay.store import Store


def _f(check, subject="model.p.int_bad_unique", name="int_bad_unique", base=2, conf=None):
    return Finding(check=check, subject=subject, subject_name=name, file="f.sql",
                   summary="s", detail="d", base=base,
                   evidence={"confidence": conf} if conf is not None else {})


def test_a_config_that_parses_but_is_never_consulted_is_worse_than_none(project_dir, tmp_path):
    """The whole point of this wiring: audit.yml has to actually do something."""
    p = Project.load(project_dir)
    cfg = Config.from_dict({"questions": {"duckdb_full_match": {"action": "fail"}}})
    kept, waived = apply_policy([_f("duckdb_full_match")], cfg, None, p)
    assert kept[0][1] == "fail" and kept[0][2] == "audit.yml"
    assert not waived


def test_an_exact_check_gates_without_any_adjudications(project_dir, tmp_path):
    """A parser decided it. There is no error rate to measure first."""
    p = Project.load(project_dir)
    s = Store(tmp_path / "s.duckdb")
    cfg = Config.from_dict({"questions": {"duckdb_full_match": {"action": "fail"}}})
    kept, _ = apply_policy([_f("duckdb_full_match")], cfg, s, p)
    assert kept[0][1] == "fail"
    s.close()


def test_a_judged_fail_is_refused_until_the_question_has_verdicts(project_dir, tmp_path):
    p = Project.load(project_dir)
    s = Store(tmp_path / "s.duckdb")
    cfg = Config.from_dict({
        "questions": {"grain_contradicts_test": {"act": {"fail": "p > 0.6", "queue": "p > 0.3"}}}})
    kept, _ = apply_policy([_f("grain_contradicts_test", conf=0.9)], cfg, s, p)
    assert kept[0][1] == "queue"          # refused, not granted
    for i in range(cfg.min_adjudications):
        s.adjudicate(f"subj{i}", "q", "grain_contradicts_test", "x", "agree")
    kept, _ = apply_policy([_f("grain_contradicts_test", conf=0.9)], cfg, s, p)
    assert kept[0][1] == "fail"           # measured, so now it may
    s.close()


def test_a_waiver_removes_a_finding_and_says_why(project_dir):
    p = Project.load(project_dir)
    cfg = Config.from_dict({"waivers": {"int_bad_unique": [
        {"question": "test_cannot_fail", "reason": "the grain is guaranteed by construction"}]}})
    kept, waived = apply_policy([_f("test_cannot_fail")], cfg, None, p)
    assert not kept
    assert "guaranteed by construction" in waived[0][1]


def test_an_expired_waiver_stops_waiving(project_dir):
    p = Project.load(project_dir)
    cfg = Config.from_dict({"waivers": {"int_bad_unique": [
        {"question": "test_cannot_fail", "reason": "r", "until": "2000-01-01"}]}})
    kept, waived = apply_policy([_f("test_cannot_fail")], cfg, None, p)
    assert kept and not waived


def test_a_scoped_question_does_not_fire_outside_its_scope(project_dir):
    p = Project.load(project_dir)
    cfg = Config.from_dict({"questions": {
        "test_cannot_fail": {"when": {"select": "path:models/intermediate"}}}})
    inside = _f("test_cannot_fail", "model.p.int_bad_unique", "int_bad_unique")
    outside = _f("test_cannot_fail", "model.p.stg_bad_notnull", "stg_bad_notnull")
    kept, waived = apply_policy([inside, outside], cfg, None, p)
    assert [f.subject_name for f, _a, _w in kept] == ["int_bad_unique"]
    assert "out of scope" in waived[0][1]


def test_a_disabled_question_is_suppressed(project_dir):
    p = Project.load(project_dir)
    cfg = Config.from_dict({"questions": {"test_cannot_fail": {"enabled": False}}})
    kept, waived = apply_policy([_f("test_cannot_fail")], cfg, None, p)
    assert not kept and "disabled" in waived[0][1]


def test_with_no_config_severity_alone_decides(project_dir):
    p = Project.load(project_dir)
    kept, _ = apply_policy([_f("x", base=3), _f("y", base=1)], Config(), None, p)
    assert [a for _f_, a, _w in kept] == ["queue", "annotate"]
