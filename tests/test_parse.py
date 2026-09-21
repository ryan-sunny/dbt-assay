from dbt_assay.parse import case_branch_values, digest


def test_output_roots_classify_expressions():
    d = digest("""
        select id,
               coalesce(a, 0)  as with_literal,
               coalesce(a, b)  as with_column,
               row_number() over (partition by id order by ts) as rn,
               case when s = 'x' then 'X' else 'Y' end as flag,
               count(*) as n
        from t group by id, a, b, s, ts
    """)
    assert d.ok
    assert d.output_roots["with_literal"] == "coalesce:literal"
    assert d.output_roots["with_column"] == "coalesce"
    assert d.output_roots["rn"] == "window:row_number"
    assert d.output_roots["flag"] == "case"
    assert d.output_roots["n"] == "agg:count"


def test_case_branches_are_enumerated_only_when_all_literal():
    assert case_branch_values("case when a then 'X' else 'Y' end") == ["X", "Y"]
    # a non-literal branch means the value set is open, so the answer is "unknown", not a guess
    assert case_branch_values("case when a then col else 'Y' end") is None
    assert case_branch_values("coalesce(a, 1)") is None


def test_join_facts_capture_kind_and_keys():
    d = digest("select 1 from a inner join b using (k) left join c on c.id = a.id cross join d")
    kinds = {j.kind for j in d.joins}
    assert "LEFT" in kinds and "CROSS" in kinds
    using = [j.using for j in d.joins if j.using]
    assert using == [["k"]]
    on = next(j.on_columns for j in d.joins if j.on_columns)
    assert "c.id" in on and "a.id" in on


def test_window_position_distinguishes_qualify_from_projection():
    proj = digest("select id, row_number() over (partition by id order by ts) rn from t")
    assert proj.windows[0].position == "projection"
    qual = digest("select id from t qualify row_number() over (partition by id order by ts) = 1")
    assert qual.windows[0].position == "qualify"
    assert qual.has_qualify


def test_order_key_root_is_resolved_through_nesting():
    d = digest("select row_number() over (order by ST_Distance_Sphere(ST_ClosestPoint(g, p), p)) rn from t")
    assert d.windows[0].order_roots == ["ST_DISTANCE_SPHERE"]


def test_function_position_is_recorded():
    d = digest("select ST_Distance(a, b) as d from t join u on ST_MakeEnvelope(1,2,3,4) where x > 1")
    assert "ST_DISTANCE" in d.functions_at("projection")
    assert "ST_MAKEENVELOPE" in d.functions_at("join_condition")


def test_function_names_are_always_upper_case():
    """sqlglot spells a native function and an anonymous one differently; assay does not."""
    d = digest("select ST_Distance(a, b), ST_MakeEnvelope(1, 2, 3, 4), coalesce(x, 1) from t")
    assert all(n == n.upper() for n, _ in d.functions), d.functions


def test_a_parse_failure_is_reported_not_raised():
    d = digest("this is not sql at all ((((", "broken")
    assert d.ok is False and d.error


def test_candidates_are_reported_in_the_models_output_namespace():
    """`group by name` where the select says `name as discovered_name` has a grain of
    discovered_name to everyone downstream. Declared keys and tests live in that namespace."""
    d = digest("select building_key, name as discovered_name, count(*) n "
               "from t group by building_key, name")
    assert d.alias_of["name"] == "discovered_name"
    assert d.group_by_columns == ["building_key", "discovered_name"]


def test_a_partition_key_is_translated_the_same_way():
    d = digest("select k, raw_id as id from t "
               "qualify row_number() over (partition by raw_id order by ts) = 1")
    assert d.windows[0].partition_columns == ["id"]


def test_an_output_column_is_resolved_one_hop_back_through_its_cte():
    """`c.first_year` says nothing; `min(year)` settles the question without a judgment."""
    d = digest("""
        with c as (select wdid, min(year) as first_year, count(*) as n from raw group by wdid)
        select c.wdid, c.first_year as record_first_year, c.n as record_n from c
    """)
    assert d.resolved_roots["record_first_year"] == "agg:min"
    assert d.resolved_roots["record_n"] == "agg:count"
    assert d.resolved_roots["wdid"] == "column"


def test_one_pathological_model_does_not_abort_the_run():
    """parse_one was guarded; rendering the tree afterwards was not. sqlglot can raise deep inside
    a dialect transform -- a BigQuery DATETIME() with one argument crashed an entire 1,632-model
    project. A model assay cannot read is one it REPORTS."""
    import dbt_assay.parse as parse_mod

    def boom(*_a, **_k):
        raise AttributeError("'NoneType' object has no attribute 'name'")

    real = parse_mod._extract
    parse_mod._extract = boom
    try:
        d = digest("select 1 from t", "pathological")
    finally:
        parse_mod._extract = real
    assert d.ok is False and "could not read the parsed SQL" in d.error


def test_a_filter_clause_resolves_to_the_aggregate_it_decorates():
    """`FILTER (WHERE ...)` is the aggregate's own clause, so the root is the aggregate.

    Read as `filter`, the root says an aggregate happened and hides WHICH, and which is the whole
    question: `count(*) filter (...)` is never NULL, `min(x) filter (...)` is NULL whenever the
    filter keeps nothing.
    """
    import sqlglot

    from dbt_assay.parse import _classify
    for sql, want in (("count(*) filter (where x > 1)", "agg:count"),
                      ("min(x) filter (where y)", "agg:min")):
        assert _classify(sqlglot.parse_one(sql, dialect="duckdb")) == want, sql
