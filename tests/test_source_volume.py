"""U2 (sunny-data feedback): whether a model is watched is a lookup, and a gap is a source.

`monitor_covers_what_matters` asked a person about 222 models, "X has N marts reading it and no
volume monitor", and some of them were built from a source that carried
`elementary.volume_anomalies`. The manifest says which relations carry a monitor and lineage says
what each covers, so the finding is one per unmonitored SOURCE, with the marts it reaches and the
yml to add, and the judged question is asked once per source.
"""
import json

from dbt_assay import elementary as E
from dbt_assay import judged, monitoring_bank
from dbt_assay.checks import sources as sc
from dbt_assay.manifest import Project

# watched_src -> stg_watched -> fct_a            (covered by the source's monitor)
# bare_src    -> stg_bare    -> fct_a, fct_b     (nothing on the way: a finding, 2 marts)
# ref_src     -> stg_ref (monitored) -> fct_c    (the model catches it: no finding)
# lone_src    -> stg_lone                        (reaches no mart: no finding)
# seed.p.codes -> stg_codes -> fct_d             (a seed does not move)
MODELS = {"stg_watched": ["source.p.raw.watched_src"], "stg_bare": ["source.p.raw.bare_src"],
          "stg_ref": ["source.p.raw.ref_src"], "stg_lone": ["source.p.raw.lone_src"],
          "stg_codes": ["seed.p.codes"],
          "fct_a": ["model.p.stg_watched", "model.p.stg_bare"], "fct_b": ["model.p.stg_bare"],
          "fct_c": ["model.p.stg_ref"], "fct_d": ["model.p.stg_codes"]}


def _source_test(src):
    t = src.split(".")[-1]
    return {"resource_type": "test", "name": f"elementary_source_volume_anomalies_raw_{t}_",
            "package_name": "p", "column_name": None,
            "test_metadata": {"name": "volume_anomalies", "namespace": "elementary",
                              "kwargs": {"model": f"{{{{ source('raw', '{t}') }}}}"}},
            "config": {"severity": "warn"}, "depends_on": {"nodes": [src]}}


def build(tmp_path, elementary=True, dbt_version="1.11.2"):
    nodes, pm, cm = {}, {}, {}
    for name, parents in MODELS.items():
        uid = f"model.p.{name}"
        layer = "marts" if name.startswith("fct") else "staging"
        nodes[uid] = {"resource_type": "model", "name": name, "package_name": "p",
                      "original_file_path": f"models/{layer}/{name}.sql", "schema": "main",
                      "description": "", "columns": {}, "config": {"materialized": "table"}}
        pm[uid] = parents
    sources = {}
    for t in ("watched_src", "bare_src", "ref_src", "lone_src"):
        uid = f"source.p.raw.{t}"
        sources[uid] = {"resource_type": "source", "name": t, "source_name": "raw",
                        "schema": "raw", "description": f"the {t} feed", "columns": {},
                        "loader": "dlt", "original_file_path": "models/staging/_sources.yml"}
        pm[uid] = []
    nodes["seed.p.codes"] = {"resource_type": "seed", "name": "codes", "package_name": "p"}
    pm["seed.p.codes"] = []
    nodes["test.p.vol_watched"] = _source_test("source.p.raw.watched_src")
    nodes["test.p.vol_ref"] = {**_source_test("source.p.raw.ref_src"), "attached_node":
                               "model.p.stg_ref", "depends_on": {"nodes": ["model.p.stg_ref"]}}
    if elementary:
        nodes["model.elementary.data_monitoring_metrics"] = {
            "resource_type": "model", "name": "data_monitoring_metrics",
            "package_name": "elementary", "original_file_path": "models/edr/dmm.sql",
            "schema": "elem", "description": "", "columns": {}, "config": {}}
        pm["model.elementary.data_monitoring_metrics"] = []
    for u, ps in pm.items():
        cm.setdefault(u, [])
        for p in ps:
            cm.setdefault(p, []).append(u)
    target = tmp_path / "target"
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_text(json.dumps({
        "metadata": {"project_name": "p", "adapter_type": "duckdb", "dbt_version": dbt_version},
        "nodes": nodes, "sources": sources, "parent_map": pm, "child_map": cm}))
    return Project.load(target)


def test_a_source_s_monitor_is_read_from_depends_on_and_covers_what_it_feeds(tmp_path):
    p = build(tmp_path)
    assert E.volume_monitored(p) == {"source.p.raw.watched_src", "model.p.stg_ref"}
    covered = E.volume_coverage(p)
    assert covered("model.p.stg_watched") and covered("model.p.stg_codes")
    assert covered("model.p.fct_c") and covered("model.p.fct_d")
    assert not covered("model.p.fct_a"), "one parent is covered and the other is not"
    assert not covered("model.p.stg_bare")


def test_one_finding_per_unmonitored_source_that_reaches_a_mart(tmp_path):
    p = build(tmp_path)
    got = sc.source_volume_not_monitored(p)
    assert [f.subject for f in got] == ["source.p.raw.bare_src"]
    f = got[0]
    assert f.evidence["marts"] == ["fct_a", "fct_b"] and f.evidence["marts_reached"] == 2
    assert "- name: bare_src" in f.evidence["yml"] and "data_tests:" in f.evidence["yml"]
    assert "elementary.volume_anomalies" in f.detail and "nothing would fail" in f.detail
    assert f.file == "models/staging/_sources.yml"


def test_the_yml_matches_the_dbt_version_and_nothing_fires_without_elementary(tmp_path):
    old = build(tmp_path / "a", dbt_version="1.7.4")
    assert "\n        tests:" in sc.source_volume_not_monitored(old)[0].evidence["yml"]
    assert sc.source_volume_not_monitored(build(tmp_path / "b", elementary=False)) == []


def test_a_model_whose_sources_are_monitored_is_not_unwatched(tmp_path):
    p = build(tmp_path)
    rep = E.Report()
    names = sorted(n for _u, n, _d, _m in E.unwatched(rep, p))
    assert "stg_watched" not in names and "stg_ref" not in names and "stg_codes" not in names
    assert "stg_bare" in names


def test_the_question_is_asked_once_per_source_and_never_about_a_model(tmp_path):
    p = build(tmp_path)
    subs = monitoring_bank.subjects(E.Report(), p, None)["monitor_covers_what_matters"]
    assert [(u, k) for u, k, _n, _st in subs] == [("source.p.raw.bare_src",
                                                    "source.p.raw.bare_src::unwatched")]
    st = subs[0][3]
    assert st["marts"] == ["fct_a", "fct_b"] and st["loader"] == "dlt"
    from dbt_assay.contracts import QUESTIONS
    q = QUESTIONS["monitor_covers_what_matters"]
    assert q["asked_about"] == "source" and set(q["criteria"]) == {"worth_watching", "static",
                                                                   "cannot_tell"}
    assert "nothing would fail to say so" in q["criteria"]["worth_watching"]["what"]


def test_an_answer_stored_under_a_model_is_no_card_and_a_source_s_is_its_reading(tmp_path):
    from types import SimpleNamespace

    from dbt_assay import live
    from dbt_assay.store import Store
    e = SimpleNamespace(uid="model.p.stg_watched", name="stg_watched", path="x.sql", marts=1,
                        descendants=1, judged={"mcov": {"answer": "worth_watching",
                                                        "probabilities": {"worth_watching": 0.9},
                                                        "context": "stg_watched"}})
    assert not [f for f in judged.declared_findings(None, [e])
                if f.check == "monitor_covers_what_matters"]
    p = build(tmp_path)
    s = Store(str(tmp_path / "s.duckdb"))
    s.con.execute("""insert into model_decisions (decision_key, question, answer, confidence,
                     probabilities, prompt_version, model_version, decided_at, state_hash, context)
                     values ('source.p.raw.bare_src::unwatched', 'mcov', 'static', 0.8,
                             '{"static": 0.8}', 'coverage.v2', 'm', now(), 'h', 'raw.bare_src')""")
    fs = live.all_findings(p, {}, None, [], store=s)
    (f,) = [x for x in fs if x.check == "source_volume_not_monitored"]
    assert f.evidence["answer"] == "static" and f.evidence["probability"] == 0.8
    assert f.evidence["asked"] == "monitor_covers_what_matters"
    assert not [x for x in fs if x.check == "monitor_covers_what_matters"]
