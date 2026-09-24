"""Exposures: what outside the warehouse a model feeds, used to rank and to gate, never to judge.

*** EVERYTHING RANKED BY `marts`, WHICH IS A PROXY. *** (25.23d, 25.24c)
`stg_blm_plss_sections` was "24 marts" -- a count of downstream models that happen to sit in a
layer. With the project's own exposures it is "reaches the Water Table report", and that is a
different sentence to put in front of somebody deciding what to fix first.
"""
from __future__ import annotations

import json

from typer.testing import CliRunner

from dbt_assay import live
from dbt_assay.checks.structural import Finding
from dbt_assay.cli import _load, app
from dbt_assay.config import Config

runner = CliRunner()


def _expose(target, depends_on=("model.p.int_bad_unique",), label="The paid report"):
    m = json.loads((target / "manifest.json").read_text())
    m["exposures"] = {"exposure.p.report": {
        "name": "report", "label": label, "type": "application",
        "owner": {"name": "Somebody", "email": "x@example.com"}, "url": None,
        "maturity": "high", "depends_on": {"nodes": list(depends_on)},
        "original_file_path": "models/_exposures.yml"}}
    (target / "manifest.json").write_text(json.dumps(m))


def test_a_model_reaches_an_exposure_directly_and_through_what_it_feeds(project_dir):
    _expose(project_dir)
    project, *_ = _load(project_dir, None)
    assert [e.title for e in project.exposures_of("model.p.int_bad_unique")] == ["The paid report"]
    # stg_bad_notnull feeds int_bad_unique, so it reaches the report too.
    assert project.blast_radius("model.p.stg_bad_notnull")["exposures"] == ["The paid report"]
    assert project.blast_radius("model.p.stg_ok_tilde")["exposures"] == []


def test_an_exposed_finding_outranks_an_unexposed_one_of_the_same_severity():
    wide = Finding(check="c", subject="a", subject_name="a", file="", summary="s", detail="",
                   base=2, descendants=50, marts=10)
    exposed = Finding(check="c", subject="b", subject_name="b", file="", summary="s", detail="",
                      base=2, exposures=["The paid report"])
    assert exposed.weight > wide.weight
    # and declaring an exposure never moves a finding's identity, so no ruling is orphaned
    plain = Finding(check="c", subject="b", subject_name="b", file="", summary="s", detail="",
                    base=2)
    assert plain.id == exposed.id


def test_the_one_stream_carries_exposures_to_check_json_and_the_store(project_dir, tmp_path):
    _expose(project_dir)
    project, digests, _f, schema, _s = _load(project_dir, None)
    fs = live.all_findings(project, digests, schema)
    reached = {f.subject_name for f in fs if f.exposures}
    assert "int_bad_unique" in reached and "stg_bad_tilde" not in reached
    r = runner.invoke(app, ["check", "-t", str(project_dir), "--store", str(tmp_path / "s.duckdb"),
                            "--config", str(tmp_path), "--json"])
    doc = json.loads(r.stdout)
    assert any(f["exposures"] == ["The paid report"] for f in doc["findings"])
    runner.invoke(app, ["check", "-t", str(project_dir), "--store", str(tmp_path / "s.duckdb"),
                        "--config", str(tmp_path)])
    import duckdb
    c = duckdb.connect(str(tmp_path / "s.duckdb"), read_only=True)
    try:
        got = c.execute("select exposures from findings where subject_name = 'int_bad_unique'"
                        ).fetchall()
    finally:
        c.close()
    assert got and all(json.loads(x[0]) == ["The paid report"] for x in got)


def test_when_exposed_gates_only_what_reaches_a_product(project_dir, tmp_path):
    from dbt_assay import judged
    (tmp_path / "audit.yml").write_text(
        "questions:\n  test_cannot_fail:\n    action: fail\n    when:\n      exposed: true\n")
    cfg = Config.load(tmp_path)
    on = Finding(check="test_cannot_fail", subject="a", subject_name="a", file="", summary="s",
                 detail="", base=2, exposures=["The paid report"])
    off = Finding(check="test_cannot_fail", subject="b", subject_name="b", file="", summary="t",
                  detail="", base=2)
    kept, _w = judged.apply_policy([on, off], cfg, None)
    acts = {f.subject: a for f, a, _why in kept}
    assert acts == {"a": "fail", "b": "annotate"}


def test_an_unknown_when_key_is_refused(tmp_path):
    import pytest

    from dbt_assay.config import ThresholdError
    (tmp_path / "audit.yml").write_text(
        "questions:\n  test_cannot_fail:\n    action: fail\n    when:\n      exposd: true\n")
    with pytest.raises(ThresholdError, match="exposed"):
        Config.load(tmp_path)


def test_exposure_undeclared_proposes_candidates_and_never_the_declaration(project_dir):
    from dbt_assay.checks.sources import exposure_undeclared
    _expose(project_dir)
    project, *_ = _load(project_dir, None)
    names = {f.subject_name for f in exposure_undeclared(project)}
    assert "stg_ok_tilde" in names, "a leaf nothing reads and no exposure covers"
    assert "int_bad_unique" not in names, "the exposure covers it"
    assert "stg_bad_notnull" not in names, "a model reads it"
    f = next(x for x in exposure_undeclared(project) if x.subject_name == "stg_ok_tilde")
    assert "yours to write" in f.detail and set(f.evidence) == {"layer"}


def test_a_model_whose_reader_is_declared_in_meta_is_not_a_candidate(project_dir):
    from dbt_assay.checks.sources import exposure_undeclared
    m = json.loads((project_dir / "manifest.json").read_text())
    m["nodes"]["model.p.stg_ok_tilde"]["config"] = {"meta": {"read_by": "scripts/export.py"}}
    (project_dir / "manifest.json").write_text(json.dumps(m))
    project, *_ = _load(project_dir, None)
    assert "stg_ok_tilde" not in {f.subject_name for f in exposure_undeclared(project)}


def test_no_judged_state_carries_an_exposure(project_dir, tmp_path):
    """*** EXPOSURES ARE FOR RANKING AND GATING, NOT FOR TELLING JEV WHAT IS AT STAKE. ***

    `guide questions` measured one extra correct sentence moving an answer from 0.96 to 0.47.
    """
    from dbt_assay import subjects
    from dbt_assay.store import Store
    _expose(project_dir)
    project, digests, _f, schema, _s = _load(project_dir, None)
    s = Store(str(tmp_path / "s.duckdb"))
    try:
        src = subjects.SubjectSource(project, digests, schema, s)
        seen = 0
        for kind in subjects.KINDS:
            try:
                subs = subjects.build(kind, src)
            except Exception:                                    # noqa: BLE001, S112
                continue
            for sub in subs:
                seen += 1
                # The exposure's own label, or a field carrying exposures. A finding ABOUT
                # exposures (`exposure_undeclared`) names the word in its prose, which is fine.
                text = json.dumps(sub.state, default=str).lower()
                assert "paid report" not in text and '"exposures"' not in text, (kind, sub.key)
        assert seen, "no subject was built; the guard saw nothing"
    finally:
        s.close()


def test_the_review_card_leads_with_what_it_reaches(project_dir, tmp_path):
    from dbt_assay import reviewform
    from dbt_assay.store import Store
    s = Store(str(tmp_path / "s.duckdb"))
    try:
        fs = [Finding(check="c", subject="model.p.a", subject_name="a", file="", summary="s",
                      detail="", base=1, marts=9),
              Finding(check="c", subject="model.p.b", subject_name="b", file="", summary="s",
                      detail="", base=1, exposures=["The paid report"])]
        cards, _sql = reviewform.cards(fs, s, tmp_path)
    finally:
        s.close()
    assert [c["model"] for c in cards] == ["b", "a"]
    assert cards[0]["exposures"] == ["The paid report"]
