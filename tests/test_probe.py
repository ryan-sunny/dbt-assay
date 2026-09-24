from dbt_assay import contracts, probe, relate
from dbt_assay.infer import Schema, derive_columns
from dbt_assay.manifest import Project
from dbt_assay.parse import digest
from dbt_assay.probe import Observation, Target, build_sql, interpret, parse_dbt_show

T = Target(relation="db.sch.orders", uid="source.p.orders", columns=["order_id", "customer_id"])


def test_one_statement_one_scan_three_numbers_per_column():
    sql = build_sql(T)
    assert sql.count("from") == 1
    for frag in ("count(*) as row_count", "count(order_id) as nn_0",
                 "count(distinct order_id) as dc_0", "count(customer_id) as nn_1"):
        assert frag in sql


def test_the_statement_transpiles_to_other_warehouses():
    assert "db.sch.orders" in build_sql(T, "snowflake")
    assert "db.sch.orders" in build_sql(T, "bigquery")


def test_the_json_is_found_after_dbts_log_preamble():
    out = '01:14 Found 357 models\n01:14 Concurrency: 1\n{\n "show": [{"row_count": 3}]\n}'
    assert parse_dbt_show(out)["show"][0]["row_count"] == 3


def test_a_trailing_banner_after_the_json_is_tolerated():
    out = '01:14 log\n{"show": [{"row_count": 3}]}\n01:14 Done.\n'
    assert parse_dbt_show(out)["show"][0]["row_count"] == 3


def test_output_with_no_json_at_all_is_none_not_a_crash():
    assert parse_dbt_show("01:14 Database Error\n") is None


def test_nulls_are_reported_before_duplicates():
    """count(distinct) ignores NULLs, so a mostly-null column can look unique."""
    o = interpret(T, {"row_count": 100, "nn_0": 60, "dc_0": 60, "nn_1": 100, "dc_1": 100})
    assert o[0].status == "has_nulls" and "40" in o[0].detail
    assert o[1].status == "unique"


def test_duplicates_are_named_with_their_count():
    o = interpret(T, {"row_count": 100, "nn_0": 100, "dc_0": 40, "nn_1": 100, "dc_1": 100})
    assert o[0].status == "has_duplicates" and "60 duplicates" in o[0].detail


def test_a_missing_count_is_unknown_never_a_verdict():
    o = interpret(T, {"row_count": 100})
    assert [x.status for x in o] == ["unknown", "unknown"]
    assert not any(x.is_unique_key for x in o)


def test_a_failed_probe_records_unknown_not_not_unique(tmp_path, monkeypatch):
    """A read-only role that cannot see a schema, read as a duplicate key, is a guard that cannot
    see with the sign flipped. The warehouse answers; this one statement does not."""
    monkeypatch.setattr(probe, "_reach", lambda *_a, **_k: None)
    obs, sql = probe.run_via_dbt(T, str(tmp_path), dbt_bin="definitely-not-a-real-binary")
    assert sql
    assert all(o.status == "unknown" for o in obs)
    assert all(not o.is_unique_key for o in obs)


def test_emit_produces_a_statement_per_relation():
    text = probe.emit([T])
    assert "-- assay probe :: db.sch.orders" in text
    assert text.rstrip().endswith(";")


def _load(project_dir):
    p = Project.load(project_dir)
    d = {uid: digest(m.compiled, m.name) for uid, m in p.models.items() if m.readable}
    sch = Schema.load(p, project_dir)
    derive_columns(p, d, sch)
    return p, d, sch, relate.declared_keys(p)


def test_targeting_skips_relations_whose_grain_is_already_settled(project_dir):
    p, d, sch, decl = _load(project_dir)
    tg = probe.targets(p, d, sch, decl, {})
    assert all(t.uid not in decl for t in tg)


def test_targeting_skips_leaves_because_settling_one_unblocks_nothing(project_dir):
    p, d, sch, decl = _load(project_dir)
    for t in probe.targets(p, d, sch, decl, {}):
        node = p.models.get(t.uid) or p.sources.get(t.uid)
        assert node.children


def test_an_observation_becomes_the_base_case_for_grain(project_dir):
    """Propagation has no base case at a source. This is the whole reason the probe exists."""
    p, d, sch, _decl = _load(project_dir)
    uid = "model.p.int_bad_unique"
    drv = p.models[uid].parents[0]
    rel = sch.relation[drv].lower()
    observed = {rel: {"section_id": Observation(rel, "section_id", 10, 10, 10, "unique", "ok")}}
    # Strip the group_by route and point the driver at the declared parent, so what is under test
    # is the base-case lookup rather than the fixture's relation spelling.
    d[uid].group_by_columns = []
    d[uid].from_relations = [sch.relation[drv]]
    assert contracts.candidates(uid, p, d, sch, {}, {}, None) is None
    got = contracts.candidates(uid, p, d, sch, {}, {}, observed)
    assert got and got.route == "from_probe" and got.columns == ["section_id"]


def test_a_relation_with_several_observed_unique_columns_is_not_guessed_at(project_dir):
    """Two candidate keys is an ambiguity, and picking one arbitrarily is the bug class this
    repo keeps hitting."""
    p, d, sch, _decl = _load(project_dir)
    uid = "model.p.int_bad_unique"
    drv = p.models[uid].parents[0]
    rel = sch.relation[drv].lower()
    observed = {rel: {
        "a": Observation(rel, "a", 10, 10, 10, "unique", "ok"),
        "b": Observation(rel, "b", 10, 10, 10, "unique", "ok"),
    }}
    d[uid].group_by_columns = []
    d[uid].from_relations = [sch.relation[drv]]
    assert contracts.candidates(uid, p, d, sch, {}, {}, observed) is None


def test_a_warehouse_that_cannot_answer_select_1_stops_the_command(tmp_path, monkeypatch):
    """*** BLIND IS NOT CLEAN. *** `practices` without a connection reported "23 of 23 NOT
    LOOKED AT" in the shape of a finding. Now the first statement is preceded by `select 1`, and a
    warehouse that cannot answer it stops the command with dbt's own words."""
    import pytest
    monkeypatch.setattr(probe, "_REACHED", {})
    with pytest.raises(probe.WarehouseUnreachable, match="--project-dir"):
        probe.run_sql("select 1", str(tmp_path), dbt_bin="definitely-not-a-real-binary")


def test_the_practices_command_exits_2_without_a_connection(project_dir, tmp_path, monkeypatch):
    from dbt_assay.cli import main
    monkeypatch.setattr(probe, "_REACHED", {})
    monkeypatch.setattr("sys.argv", ["assay", "practices", "-t", str(project_dir),
                                     "--dbt", "definitely-not-a-real-binary",
                                     "--store", str(tmp_path / "s.duckdb")])
    import pytest
    with pytest.raises(SystemExit) as e:
        main()
    assert e.value.code == 2


def test_a_relative_profiles_dir_is_read_from_where_assay_was_run(tmp_path, monkeypatch):
    """*** `--profiles-dir transform` REACHED dbt AS `transform/transform`. ***

    Every dbt call runs with the project as its working directory, so a relative path typed from
    the directory above it was resolved from inside it, and dbt said only that it could not find
    a profile. It is resolved from where it was typed, once, for every call.
    """
    (tmp_path / "transform").mkdir()
    monkeypatch.chdir(tmp_path)
    assert probe.profiles_args("transform") == ["--profiles-dir", str(tmp_path / "transform")]
    assert probe.profiles_args(None) == []


def test_a_profiles_dir_with_no_profiles_yml_says_so(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(probe, "_REACHED", {})
    monkeypatch.chdir(tmp_path)
    with pytest.raises(probe.WarehouseUnreachable, match="no profiles.yml there"):
        probe.run_sql("select 1", str(tmp_path), profiles_dir="nowhere", dbt_bin="dbt")


def test_dbts_error_survives_a_warning_on_stderr():
    """*** `stderr or stdout` KEPT THE WARNING AND DROPPED THE ERROR. ***

    dbt prints its error on stdout; `uv run` from inside another environment prints a VIRTUAL_ENV
    warning on stderr. Measured on the field project: the message a person saw was uv's warning.
    """
    from types import SimpleNamespace
    p = SimpleNamespace(stdout="Encountered an error:\nCould not find profile named 'x'",
                        stderr="warning: `VIRTUAL_ENV=/a/.venv` does not match")
    assert "Could not find profile named 'x'" in probe.failure_text(p)

    from dbt_assay.elementary import dbt_error
    said = dbt_error("preamble" + probe.DBT_OUTPUT + probe.failure_text(p))
    assert said == "dbt said: Could not find profile named 'x'", said
    # assay's own reason, when dbt never ran, is passed through without a second preamble
    assert dbt_error("could not reach the warehouse: no profiles.yml") == "no profiles.yml"
