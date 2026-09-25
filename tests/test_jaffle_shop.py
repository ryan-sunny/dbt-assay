"""dbt-labs/jaffle_shop_duckdb, compiled: what a new user runs first. (sunny-data J1-J3)"""
from pathlib import Path

from dbt_assay.checks.structural import unevaluable_tests
from dbt_assay.cli import _load

TARGET = Path(__file__).parent / "fixtures" / "jaffle_shop" / "target"


def test_every_test_on_a_select_star_model_can_be_evaluated():
    """J2: 20 of 20 tests read "unevaluable ... it ends in `select *`" while the columns were
    known: `select * from final` over the model's own CTE is that CTE's projection."""
    project, digests, _f, schema, _s = _load(TARGET)
    assert len(project.tests) == 20
    assert unevaluable_tests(project, digests) == []
    d = digests["model.jaffle_shop.orders"]
    assert "coupon_amount" in d.output_columns and "*" not in d.output_columns
    assert d.output_roots["order_id"] == "column"


def test_the_catalog_is_read_and_says_so():
    """J1: `catalog 0` read as "not read". It is read; the SQL speaks first for a model."""
    project, _d, _f, schema, _s = _load(TARGET)
    assert schema.catalog_present
    assert all(schema.columns(u).names for u in project.models)
