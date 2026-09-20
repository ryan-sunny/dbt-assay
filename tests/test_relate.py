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


def test_fanout_check_is_skipped_without_a_schema(project_dir):
    """Without resolved join targets the check cannot tell a join from a FROM relation."""
    p, d = _load(project_dir)
    _, findings = relate.run_all(p, d, None)
    assert all(f.check != "join_fans_out" for f in findings)


def test_fanout_is_reported_only_for_an_actual_join_target(project_dir, tmp_path):
    """The three false positives that killed the first version, as fixtures."""
    from dbt_assay.parse import digest as dg
    # FROM relation, not a join: must not fire
    d1 = dg("select a.k, a.v from sunny.main.parent a")
    assert not any(j.target_relation for j in d1.joins)
    # join to an aggregating subquery: the target relation is not a table at all
    d2 = dg("select x.k from sunny.main.driver d "
            "join (select k, any_value(v) v from sunny.main.parent group by 1) x on x.k = d.k")
    assert d2.joins[0].target_is_subquery and d2.joins[0].target_aggregates
    assert d2.joins[0].target_relation is None
    # a real join to a real table resolves, and only TARGET-side keys count
    d3 = dg("select 1 from sunny.main.driver d join sunny.main.parent p on p.k = d.k and p.q = d.q")
    j = d3.joins[0]
    assert j.target_relation == "sunny.main.parent" and j.target_keys == ["k", "q"]


def test_distinct_anywhere_marks_a_fanout_as_absorbed():
    from dbt_assay.parse import digest as dg
    assert dg("select distinct a from t").absorbs_fanout
    assert dg("with c as (select distinct a from t) select a from c").absorbs_fanout
    assert dg("select count(distinct a) from t").absorbs_fanout
    assert dg("select string_agg(distinct a, ',') from t").absorbs_fanout
    assert not dg("select a from t").absorbs_fanout
