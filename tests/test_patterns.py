"""Six counted SQL shapes (sunny-data sweep, 2026-09-25). Each gets a positive control and the
correct-but-similar case beside it that must stay quiet."""
from types import SimpleNamespace

import pytest

from dbt_assay.checks.patterns import run_all
from dbt_assay.parse import digest


def _project(sql_by_name, mat="table", tests=()):
    models = {f"model.p.{n}": SimpleNamespace(name=n, path=f"{n}.sql", materialized=mat,
                                              is_installed_package=False)
              for n in sql_by_name}
    return SimpleNamespace(
        models=models, sources={}, tests=list(tests),
        exposures_of=lambda uid: [],
        blast_radius=lambda uid: {"descendants": 0, "marts": 0})


def _checks(sql, **kw):
    p = _project({"m": sql}, **kw)
    return {f.check: f for f in run_all(p, {"model.p.m": digest(sql, "m")})}


@pytest.mark.parametrize("sql,check,fires", [
    ("select id from t where d >= current_date - interval 5 year", "output_depends_on_the_clock",
     True),
    ("select id, now() as at from t", "output_depends_on_the_clock", True),
    ("select id from t where d >= date '2020-01-01'", "output_depends_on_the_clock", False),
    ("select id, string_agg(n, ',') from t group by id", "order_sensitive_aggregate", True),
    ("select id, string_agg(n, ',' order by n) from t group by id",
     "order_sensitive_aggregate", False),
    ("select a.id from a join b on lower(a.k) = b.k", "join_key_normalised_on_one_side", True),
    ("select a.id from a join b on lower(a.k) = lower(b.k)", "join_key_normalised_on_one_side",
     False),
    ("select id from a where id not in (select id from b)", "not_in_over_a_nullable_subquery",
     True),
    ("select id from a where not exists (select 1 from b where b.id = a.id)",
     "not_in_over_a_nullable_subquery", False),
    ("select a.id from a left join b on a.k = b.k where b.s = 'x'",
     "left_join_undone_by_where", True),
    ("select a.id from a left join b on a.k = b.k where b.k is null",
     "left_join_undone_by_where", False),
    ("select a.id from a left join b on a.k = b.k and b.s = 'x'",
     "left_join_undone_by_where", False),
    ("select id from t limit 10", "limit_in_a_model", True),
    ("select id, (select v from r order by v limit 1) as top from t", "limit_in_a_model", False),
])
def test_each_shape_fires_on_the_broken_case_only(sql, check, fires):
    assert (check in _checks(sql)) is fires


def test_the_clock_in_a_view_weighs_more_and_a_future_guard_is_not_reported():
    rolling = "select id from t where d >= current_date - interval 5 year"
    assert _checks(rolling, mat="view")["output_depends_on_the_clock"].base == 3
    assert _checks(rolling)["output_depends_on_the_clock"].base == 2
    # a bound against future-dated rows only changes anything when the data is from the future
    for g in ("select id from t where event_date <= current_date",
              "select case when d between date '1900-01-01' and now() then d end as d from t",
              "select id from t where y <= year(current_date)"):
        assert "output_depends_on_the_clock" not in _checks(g), g


def test_not_in_is_quiet_when_a_test_keeps_the_column_non_null():
    sql = "select id from a where id not in (select id from b)"
    p = _project({"m": sql, "b": "select 1 as id"},
                 tests=[SimpleNamespace(tests_model="model.p.b", kind="not_null", column="id")])
    got = run_all(p, {"model.p.m": digest(sql, "m")})
    assert not [f for f in got if f.check == "not_in_over_a_nullable_subquery"]


def test_names_that_do_not_state_their_type_and_a_view_many_models_read():
    from dbt_assay.checks.patterns import name_states_type, view_read_by_many
    m = SimpleNamespace(name="m", path="m.sql", is_installed_package=False, materialized="view",
                        children=[f"model.p.c{i}" for i in range(5)])
    models = {"model.p.m": m, **{f"model.p.c{i}": SimpleNamespace(name=f"c{i}")
                                 for i in range(5)}}
    project = SimpleNamespace(models=models,
                              blast_radius=lambda uid: {"descendants": 5, "marts": 0})
    schema = SimpleNamespace(catalog={"nodes": {"model.p.m": {"columns": {
        "active": {"type": "BOOLEAN"}, "is_open": {"type": "BOOLEAN"},
        "loaded": {"type": "TIMESTAMP"}, "created_at": {"type": "TIMESTAMP"},
        "date_issued": {"type": "DATE"}, "sold": {"type": "DATE"}}}}})
    got = name_states_type(project, schema)
    cols = got[0].evidence["columns"]
    assert [c.split(":")[0] for c in cols] == ["active", "loaded", "sold"]
    assert view_read_by_many(project)[0].evidence["count"] == 5
    m.materialized = "table"
    assert view_read_by_many(project) == []
