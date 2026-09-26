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


def test_a_monitor_that_never_ran_on_a_customer_facing_model_is_worth_a_look():
    """(sunny-data: Elementary's volume anomalies on every source ran once, by hand, and never
    since; assay filed it as a note.) A declared monitor that never ran, on a model a paying
    customer reaches, is queued; the same on a model nobody pays for, or a plain not_null, keeps
    the default by severity."""
    from types import SimpleNamespace

    from dbt_assay import judged
    from dbt_assay.checks.structural import Finding
    nodes = {"test.p.v": {"resource_type": "test", "name": "vol_a",
                          "test_metadata": {"namespace": "elementary", "name": "volume_anomalies"}},
             "test.p.n": {"resource_type": "test", "name": "nn_a",
                          "test_metadata": {"name": "not_null"}}}
    paid = SimpleNamespace(customer_facing=True)
    project = SimpleNamespace(raw={"nodes": nodes},
                              exposures_of=lambda uid: [paid] if uid == "model.p.a" else [])
    cfg = SimpleNamespace(for_question=lambda c: SimpleNamespace(
        enabled=True, select=None, exposed_only=False, act=None,
        action_for=lambda *a, **k: None),
        waived=lambda *a: None, min_adjudications=20, min_agreement=None)

    def f(test, subject):
        return Finding(check="test_never_ran_is_a_gap_or_a_leftover", subject=subject,
                       subject_name=subject.split(".")[-1], file="", summary=test, detail="",
                       base=2, evidence={"context": test, "answer": "a_coverage_gap"})
    kept, _w = judged.apply_policy([f("vol_a", "model.p.a"), f("vol_a", "model.p.b"),
                                    f("nn_a", "model.p.a")], cfg, None, project)
    acts = [(k.subject, k.summary, a) for k, a, _why in kept]
    assert acts == [("model.p.a", "vol_a", "queue"), ("model.p.b", "vol_a", "annotate"),
                    ("model.p.a", "nn_a", "annotate")], acts
