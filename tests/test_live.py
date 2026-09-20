from dbt_assay import diff, live
from dbt_assay.inventory import ColumnEntry, Fact, ModelEntry


def _e(name, grain=None, derived=None, cols=()):
    m = ModelEntry(uid=f"model.p.{name}", name=name, path=f"{name}.sql", layer="marts",
                   materialized="table")
    m.grain = Fact(grain, "derived") if grain else None
    m.derived_grain = derived
    m.columns = [ColumnEntry(name=c, provenance=Fact("carried", "derived")) for c in cols]
    return m


def test_a_reformat_says_nothing(project_dir):
    """A renamed CTE, a rewritten join, a comment: the contract is identical, so the pane stays
    quiet. That silence is the feature."""
    base = live.Snapshot.of([_e("m", ["id"], ["id"], ["id", "amount"])])

    current = [_e("m", ["id"], ["id"], ["id", "amount"])]
    assert diff.compare(base.entries, current) == []


def test_a_baseline_is_a_snapshot_not_the_previous_save():
    """Break it and fix it and nothing should have been reported at all."""
    base = live.Snapshot.of([_e("m", ["a"], ["a"])])
    broken = [_e("m", ["a", "b"], ["a", "b"])]
    fixed = [_e("m", ["a"], ["a"])]
    assert diff.compare(base.entries, broken)          # would have fired mid-edit
    assert diff.compare(base.entries, fixed) == []     # net effect: nothing


def test_one_grain_event_is_reported_once():
    """The resolved grain and the SQL's grain move together; saying both says it twice."""
    before = [_e("m", None, None)]
    after = [_e("m", ["customer_id"], ["customer_id"])]
    kinds = [c.kind for c in diff.compare(before, after)]
    assert kinds.count("grain") + kinds.count("grain_in_sql") == 1


def test_a_snapshot_indexes_by_name():
    s = live.Snapshot.of([_e("a"), _e("b")])
    assert set(s.by_name) == {"a", "b"} and s.taken_at > 0


def test_sql_files_ignores_build_output(tmp_path):
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "a.sql").write_text("select 1")
    (tmp_path / "target" / "compiled").mkdir(parents=True)
    (tmp_path / "target" / "compiled" / "a.sql").write_text("select 1")
    (tmp_path / "dbt_packages").mkdir()
    (tmp_path / "dbt_packages" / "p.sql").write_text("select 1")
    found = live.sql_files(tmp_path / "target", tmp_path)
    assert len(found) == 1 and found.popitem()[0].endswith("models/a.sql")


def test_a_contract_is_small_enough_to_be_worth_asking_for(project_dir):
    """The whole argument for the MCP server: fifteen lines where the SQL is two hundred."""
    st = live.read(project_dir)
    c = live.contract_of(st, "int_bad_unique")
    assert set(c) >= {"model", "grain", "grain_source", "columns", "description", "descendants"}
    assert c["grain"] == ["section_id"]
    assert live.contract_of(st, "nope") is None
