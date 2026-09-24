"""G-C: a measure summed in floating point is not reproducible."""
from types import SimpleNamespace

from dbt_assay.checks import floats
from dbt_assay.inventory import ColumnEntry, Fact, ModelEntry
from dbt_assay.parse import digest

UID = "model.p.mart_sales"


def _setup(sql, amount_type="DOUBLE", role="measure", layer="marts", catalog=None):
    src = {"columns": {"amount": {"name": "amount", "data_type": amount_type}}} \
        if amount_type else {"columns": {}}
    project = SimpleNamespace(
        models={UID: SimpleNamespace(parents=["source.p.raw_t"], name="mart_sales")},
        raw={"nodes": {}, "sources": {"source.p.raw_t": src}}, dialect="duckdb")
    schema = SimpleNamespace(relation={"source.p.raw_t": "raw.t", UID: "main.mart_sales"},
                             catalog=catalog or {}, catalog_present=bool(catalog))
    e = ModelEntry(uid=UID, name="mart_sales", path="m.sql", layer=layer, materialized="table")
    e.columns = [ColumnEntry(name="total", provenance=Fact("aggregated"),
                             role=Fact(role, "judged", 0.9) if role else None)]
    return project, {UID: digest(sql, "mart_sales")}, schema, [e]


def test_a_sum_over_a_double_measure_in_a_mart_fires():
    (f,) = floats.float_sum_is_not_reproducible(*_setup(
        "select region, sum(t.amount) as total from raw.t t group by region"))
    assert f.check == "float_sum_is_not_reproducible" and f.summary == \
        "`total` is a sum over a floating-point input"
    assert f.evidence["input_type"] == "DOUBLE"
    assert f.evidence["recommendation"] == "sum(cast(t.amount as decimal(18, 2)))"


def test_a_cast_to_decimal_before_the_sum_does_not():
    assert floats.float_sum_is_not_reproducible(*_setup(
        "select region, sum(cast(amount as decimal(18,2))) as total from raw.t group by region")
    ) == []


def test_a_cast_after_the_sum_still_fires_because_the_addition_was_float():
    assert len(floats.float_sum_is_not_reproducible(*_setup(
        "select region, cast(sum(amount) as decimal(18,2)) as total from raw.t group by region"
    ))) == 1


def test_an_avg_over_an_integer_does_not():
    assert floats.float_sum_is_not_reproducible(*_setup(
        "select region, avg(amount) as total from raw.t group by region",
        amount_type="INTEGER")) == []


def test_no_types_is_not_a_pass_it_is_said():
    got = floats.float_sum_is_not_reproducible(*_setup(
        "select region, sum(amount) as total from raw.t group by region", amount_type=None))
    assert got == []
    assert floats.UNREAD and "could not be read" in floats.UNREAD[0][0] and "catalog.json" in \
        floats.UNREAD[0][0]


def test_an_unjudged_role_is_counted_not_flagged():
    got = floats.float_sum_is_not_reproducible(*_setup(
        "select region, sum(amount) as total from raw.t group by region", role=None))
    assert got == [] and "never judged" in floats.UNREAD[0][0]


def test_a_staging_model_with_no_mart_downstream_does_not():
    assert floats.float_sum_is_not_reproducible(*_setup(
        "select region, sum(amount) as total from raw.t group by region", layer="staging")) == []
