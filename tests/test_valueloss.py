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
    # the data moved: counted again, and the run says why (tester, 5c8fdd4: "unchanged" could
    # not be trusted while a recount did not name its reason)
    rows_now["n"] = 11
    sent.clear()
    said: list = []
    valueloss.measure(cands, p, probe, ".", None, "dbt", store=s, say=said.append)
    assert any("sum(case when" in x for x in sent)
    assert "1 source(s) counted (1 changed)" in said[0], said
    assert said[1].endswith(": changed: 10 -> 11"), said
    # a count that fails is not reported as counted, and it is tried again next run
    ok_many = probe.run_many
    probe.run_many = lambda stmts, *a, **k: [Result(failed=True, why="timeout after 5s")
                                             if st.kind == "count" else ok_many([st])[0]
                                             for st in stmts]
    rows_now["n"] = 13
    said = []
    valueloss.measure(cands, p, probe, ".", None, "dbt", store=s, say=said.append)
    assert "0 source(s) counted" in said[0] and "1 failed and tried again next run" in said[0]
    assert said[1].endswith(": failed: timeout after 5s"), said
    assert s.con.execute("select outcome from value_loss_tries").fetchone()[0].startswith("failed")
    probe.run_many = ok_many
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


def test_a_blank_or_a_null_token_is_absent_not_lost():
    """RC box, b571fc9: `TRY_CAST(const_year AS INT)` "lost" 118,145 values and every one was ''.
    The SQL runs on DuckDB here, so the rule is tested where it is counted."""
    import duckdb
    con = duckdb.connect()
    con.execute("create table t (v varchar)")
    con.execute("insert into t values ('1990'), (''), ('   '), ('NA'), ('n/a'), ('Null'), "
                "(null), ('abc'), ('19x')")
    c = valueloss.Candidate(model="model.p.m", model_name="m", column="y", relation="t",
                            source_column="v", expression="TRY_CAST(v AS INT)", cause="transform")
    lost, present = con.execute(f"select {valueloss._lost_expr(c)}, {valueloss._present_expr(c)} "
                                f"from t").fetchone()
    assert (lost, present) == (2, 3)                      # 'abc' and '19x' lost, of three values
    got = sorted(r[0] for r in con.execute(valueloss._sample_sql(c)).fetchall())
    assert got == ["19x", "abc"]
