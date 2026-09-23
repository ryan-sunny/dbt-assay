"""The build queue's families: code narrows to the shape, the judgment is asked only there."""
import hashlib
import json
from types import SimpleNamespace

from dbt_assay import subjects
from dbt_assay.cli import _load

SQL = {
    "stg_status": "select id, coalesce(analysis_status, 'not looked up') as analysis_status, "
                  "coalesce(wells, 0) as wells from raw.t",
    "stg_filtered": "select id, aquifer from raw.t "
                    "where aquifer not in ('Dawson', 'Denver', 'Arapahoe', 'Laramie')",
    "stg_area_a": "select id, st_area(geom) / 4046.86 as area_acres from raw.a",
    "stg_area_b": "select id, lot_sqft / 43560.0 as area_acres from raw.b",
    "int_dedupe": "select * from (select id, name, updated_at, row_number() over "
                  "(partition by id order by updated_at desc) as rn from raw.t) where rn = 1",
    "stg_readings": "select id, case when level = -9999 then null else level end as level, "
                    "coalesce(expires, '9999-12-31') as expires from raw.r",
    "int_daily": "select d.day, m.total from raw.daily d join raw.monthly m on d.day = m.month_date",
    "mart_x": "select * from stg_status",
}


def make(tmp_path):
    nodes, parents, children = {}, {}, {}
    for name, sql in SQL.items():
        uid = f"model.p.{name}"
        layer = {"stg": "staging", "int": "intermediate", "mart": "marts"}[name.split("_")[0]]
        path = f"models/{layer}/{name}.sql"
        nodes[uid] = {"resource_type": "model", "name": name, "original_file_path": path,
                      "schema": "main", "description": "", "columns": {},
                      "config": {"materialized": "view", "meta": {}},
                      "checksum": {"name": "sha256",
                                   "checksum": hashlib.sha256(sql.encode()).hexdigest()}}
        parents[uid], children[uid] = [], []
        f = tmp_path / "target" / "compiled" / "p" / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(sql)
    parents["model.p.mart_x"] = ["model.p.stg_status"]
    children["model.p.stg_status"] = ["model.p.mart_x"]
    (tmp_path / "target" / "manifest.json").write_text(json.dumps({
        "metadata": {"project_name": "p", "dbt_version": "1.11.0", "adapter_type": "duckdb"},
        "nodes": nodes, "sources": {}, "parent_map": parents, "child_map": children}))
    project, digests, _f, schema, _s = _load(tmp_path / "target")
    return project, digests, schema


def build(kind, tmp_path):
    project, digests, schema = make(tmp_path)
    return subjects.build(kind, subjects.SubjectSource(project, digests, schema, None))


def test_defaults_are_the_coalesce_onto_a_literal(tmp_path):
    got = {s.state["column"]: s.state for s in build("default", tmp_path)}
    assert got["analysis_status"]["default"] == "'not looked up'"
    assert got["wells"]["default"] == "0"


def test_an_in_list_of_three_or_more_is_an_enumerated_filter(tmp_path):
    subs = build("enumerated_filter", tmp_path)
    assert len(subs) == 1 and subs[0].state["negated"] and subs[0].state["count_listed"] == 4


def test_two_models_computing_one_unit_name_are_a_pair(tmp_path):
    subs = build("same_name_measure", tmp_path)
    assert [s.state["column"] for s in subs] == ["area_acres"]


def test_a_dedupe_is_a_ranking_window(tmp_path):
    subs = build("ranking_window", tmp_path)
    assert subs and subs[0].state["order_by"] and subs[0].state["partition_by"]


def test_sentinels_are_found_in_expressions(tmp_path):
    lits = {x for s in build("sentinel", tmp_path) for x in s.state["sentinel_literals"]}
    assert "-9999" in lits and any("9999-12-31" in x for x in lits)


def test_a_join_on_a_date_is_a_time_join(tmp_path):
    subs = build("time_join", tmp_path)
    assert subs and "day" in subs[0].state["time_columns"]


def test_every_new_family_declares_a_subject_its_builder_produces(tmp_path):
    from dbt_assay.contracts import QUESTIONS
    for fam, kind in (("default_is_a_measurement_or_an_absence", "default"),
                      ("what_would_break_silently", "column_risk"),
                      ("filter_is_complete", "enumerated_filter"),
                      ("units_agree_across_models", "same_name_measure"),
                      ("time_grain", "time_join"), ("tie_break_is_total", "ranking_window"),
                      ("sentinel_is_not_a_value", "sentinel")):
        assert QUESTIONS[fam]["subject"] == kind
        assert QUESTIONS[fam]["finding_when"]


def test_a_defect_answer_is_a_finding_that_names_its_subject(tmp_path):
    from dbt_assay.judged import declared_findings
    e = SimpleNamespace(uid="model.p.stg_status", name="stg_status", path="x.sql", judged={
        "dflt": {"answer": "an_absence_marker", "probabilities": {"an_absence_marker": 0.9},
                 "context": "stg_status.analysis_status"},
        "dflt__1": {"answer": "an_absence_marker", "probabilities": {"an_absence_marker": 0.8},
                    "context": "stg_status.wells"}})
    fs = [f for f in declared_findings(None, [e])
          if f.check == "default_is_a_measurement_or_an_absence"]
    assert len(fs) == 2 and len({f.id for f in fs}) == 2
    assert all("analysis_status" in f.summary or "wells" in f.summary for f in fs)


def test_the_monitoring_bank_sorts_each_pile_and_refuses_to_guess(tmp_path):
    from dbt_assay import monitoring_bank as mb
    from dbt_assay.elementary import Report, TestState, Volume
    project, _d, _s = make(tmp_path)
    rep = Report(volumes=[Volume("stg_status", "P.MAIN.STG_STATUS", 5, 300.0, 100.0, None, 1.0)],
                 tests=[TestState("stg_status", "wells", "anomaly_detection", "volume", "fail",
                                  None, 90.0)])
    subs = mb.subjects(rep, project, ran_test_ids=None)
    assert [s[0] for s in subs["movement_is_expected_for_this_kind_of_table"]] == \
        ["model.p.stg_status"]
    assert subs["stale_monitor_still_matters"]
    assert subs["test_never_ran_is_a_gap_or_a_leftover"] == [], \
        "an unreadable results table is not 'nothing ran'"


def test_patch_worth_testing_ranks_the_judged_risks(project_dir, tmp_path):
    import json as _json

    from typer.testing import CliRunner

    from dbt_assay.cli import app
    from dbt_assay.store import Store
    s = Store(str(tmp_path / "s.duckdb"))
    s.con.execute("""insert into model_decisions (decision_key, question, answer, confidence,
                     probabilities, prompt_version, model_version, decided_at, state_hash)
                     values ('model.p.stg_bad_notnull::risk::amount', 'risk',
                             'a_default_would_hide_missing_data', 0.8, '{}', 'risk.v1', 'm',
                             now(), 'h')""")
    s.close()
    r = CliRunner().invoke(app, ["patch", "--worth-testing", "-t", str(project_dir),
                                 "--store", str(tmp_path / "s.duckdb"), "--json"])
    assert r.exit_code == 0, r.output
    got = _json.loads(r.output)["worth_testing"]
    assert got[0]["column"] == "amount" and "catches it" in got[0]["test_that_catches_it"]
