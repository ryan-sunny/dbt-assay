"""values_lost_at_hop: source values a model turns into NULL (sunny-data: 51% of a WUI column
never reached a paid report). Candidates come from the parse; the count comes from the warehouse."""
from types import SimpleNamespace

from dbt_assay import valueloss
from dbt_assay.parse import digest
from dbt_assay.probe import Result, Statement


def _project(sql):
    return SimpleNamespace(
        dialect="duckdb",
        models={"model.p.stg": SimpleNamespace(name="stg", path="stg.sql",
                                               is_installed_package=False)},
        sources={"source.p.raw.permits": SimpleNamespace(name="permits", schema="raw",
                                                         source_name="raw")},
        blast_radius=lambda uid: {"descendants": 0, "marts": 0})


SQL = ("with s as (select * from raw.permits) "
       "select id, try_cast(valuation as double) as valuation, trim(name) as name, fee from s")


def test_candidates_are_single_column_transforms_and_unread_variants():
    p = _project(SQL)
    schema = SimpleNamespace(columns=lambda uid: SimpleNamespace(
        names=["id", "valuation", "name", "fee", "fee__v_double"]))
    got = valueloss.candidates(p, {"model.p.stg": digest(SQL, "stg")}, schema)
    kinds = {(c.column, c.cause) for c in got}
    assert kinds == {("valuation", "transform"), ("name", "transform"), ("fee", "variant")}
    # a bare column cannot lose values, and a coalesce fills NULLs on purpose
    assert not any(c.column in ("id",) for c in got)


def test_one_statement_per_relation_and_a_finding_only_where_values_were_lost():
    p = _project(SQL)
    cands = valueloss.candidates(p, {"model.p.stg": digest(SQL, "stg")}, None)
    sent: list = []

    def run_many(stmts, *a, **k):
        sent.append(stmts)
        if stmts[0].kind == "count":
            return [Result(rows=[{"l0": 2, "p0": 3, "l1": 0, "p1": 4}])]
        return [Result(rows=[{"v": "abc"}, {"v": "n/a"}])]
    probe = SimpleNamespace(Statement=Statement, run_many=run_many)
    got = valueloss.measure(cands, p, probe, ".", None, "dbt")
    assert len(sent[0]) == 1 and "sum(case when" in sent[0][0].sql      # one per relation
    assert [f.evidence["column"] for f in got] == ["valuation"]
    assert got[0].evidence["lost"] == 2 and got[0].evidence["sample"] == ["abc", "n/a"]
