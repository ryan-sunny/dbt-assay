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


def test_counted_once_then_read_from_the_last_count_until_the_data_moves(tmp_path):
    """RC box: 441s, then 524s for the same counts. A source is counted again only when its
    fingerprint (row count, newest load id) moved; the rest come from the store."""
    from dbt_assay.store import Store
    p = _project(SQL)
    cands = valueloss.candidates(p, {"model.p.stg": digest(SQL, "stg")}, None)
    s = Store(str(tmp_path / "s.duckdb"))
    sent: list = []
    rows_now = {"n": 10}

    def run_many(stmts, *a, **k):
        out = []
        for st in stmts:
            sent.append(st.sql)
            if "duckdb_tables()" in st.sql and "estimated_size" in st.sql:
                out.append(Result(rows=[{"s": "raw", "t": "permits", "v": str(rows_now["n"])}]))
            elif "duckdb_tables()" in st.sql:                 # no dlt here
                out.append(Result(rows=[]))
            elif st.kind == "count":
                out.append(Result(rows=[{"l0": 2, "p0": 3, "l1": 0, "p1": 4}]))
            else:
                out.append(Result(rows=[{"v": "abc"}, {"v": "n/a"}]))
        return out
    probe = SimpleNamespace(Statement=Statement, run_many=run_many)
    got = valueloss.measure(cands, p, probe, ".", None, "dbt", store=s)
    assert [f.evidence["column"] for f in got] == ["valuation"]
    assert got[0].evidence["lost"] == 2 and got[0].evidence["sample"] == ["abc", "n/a"]
    assert any("sum(case when" in x for x in sent)          # one statement per relation
    # the fingerprint is table metadata, never a column scan
    assert not any("max(" in x for x in sent), sent
    # same data: only the fingerprint is read, and the finding comes from the store
    sent.clear()
    got = valueloss.measure(cands, p, probe, ".", None, "dbt", store=s)
    assert not any("sum(case when" in x for x in sent), sent
    assert got[0].evidence["lost"] == 2 and got[0].evidence["sample"] == ["abc", "n/a"]
    # the data moved: counted again
    rows_now["n"] = 11
    sent.clear()
    valueloss.measure(cands, p, probe, ".", None, "dbt", store=s)
    assert any("sum(case when" in x for x in sent)
    # no budget left: nothing is counted, and the last count still stands
    rows_now["n"] = 12
    sent.clear()
    said: list = []
    got = valueloss.measure(cands, p, probe, ".", None, "dbt", store=s, max_seconds=0,
                            say=said.append)
    assert not any("sum(case when" in x for x in sent)
    assert "left for the next run" in said[0]
    assert got[0].evidence.get("data_changed_since") is True
    s.close()
