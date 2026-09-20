from dbt_assay import relate
from dbt_assay.manifest import Project
from dbt_assay.parse import digest


def _load(project_dir):
    p = Project.load(project_dir)
    d = {uid: digest(m.compiled, m.name) for uid, m in p.models.items() if m.readable}
    return p, d


def test_edge_facts_record_what_was_offered_and_what_was_taken(project_dir):
    p, d = _load(project_dir)
    facts = relate.edge_facts(p, d)
    f = next(x for x in facts if x.parent_name == "stg_bad_notnull")
    assert set(f.available) == {"id", "amount"}
    # int_bad_unique reads section_id only, so both parent columns are dropped at this boundary
    assert "amount" in f.dropped


def test_an_edge_with_no_declared_parent_columns_records_nothing(project_dir):
    """Silence is not a fact. A parent whose columns are unknown yields no edge fact at all."""
    p, d = _load(project_dir)
    for uid in d:
        d[uid].output_columns = []
    assert relate.edge_facts(p, d) == []


def test_declared_keys_come_from_the_projects_own_tests(project_dir):
    p, _ = _load(project_dir)
    keys = relate.declared_keys(p)
    # the fixture declares `unique` on int_bad_unique.section_id
    assert keys["model.p.int_bad_unique"] == ["section_id"]


def test_partial_key_join_is_not_in_the_default_run(project_dir):
    """It produced three false positives for three different reasons. See its docstring."""
    p, d = _load(project_dir)
    _, findings = relate.run_all(p, d)
    assert all(f.check != "partial_key_join" for f in findings)
