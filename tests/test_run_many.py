"""`probe.run_many`: batching at the door every warehouse statement goes through.

*** 0.49.0 BATCHED ONE CALLER AND EVERY OTHER CALLER KEPT PAYING. ***
`feeds` measured 47 seconds per source, of which the warehouse saw under a tenth of one. These
tests run the wrapped SQL through a real DuckDB, because a wrapping that only a mock accepts is a
wrapping no engine has been asked about.
"""
import duckdb
import pytest

from dbt_assay import probe
from dbt_assay.probe import Result, Statement, run_many, unwrap_many, wrap_many


@pytest.fixture
def engine(monkeypatch):
    con = duckdb.connect()
    con.execute("create table a as select * from (values (1, 'x'), (2, 'y'), (3, 'z')) t(id, v)")
    con.execute("create table b as select 10 as n")
    calls = []

    def fake(sql, project_dir, profiles_dir=None, dbt_bin="dbt", limit=50, timeout=300,
             measure=False):
        calls.append(sql)
        try:
            cur = con.execute(f"select * from ({sql}) limit {limit}")
        except Exception as e:                                   # noqa: BLE001
            return Result(failed=True, why=str(e)[:200])
        cols = [d[0] for d in cur.description]
        return Result(rows=[dict(zip(cols, r)) for r in cur.fetchall()])

    monkeypatch.setattr(probe, "_execute", fake)
    return calls


def test_statements_of_different_shapes_come_back_as_their_own_rows(engine):
    got = run_many([Statement("select id, v from a order by id desc", limit=10),
                    Statement("select n from b", limit=1),
                    Statement("select v from a where id = 2", limit=5)], ".", dialect="duckdb")
    assert len(engine) == 1, "three statements, one dbt invocation"
    assert [r["id"] for r in got[0].rows] == [3, 2, 1], "each statement keeps its own order"
    assert got[1].rows == [{"n": 10}]
    assert got[2].rows == [{"v": "y"}]
    assert all(r.batched == 3 for r in got)


def test_each_statement_keeps_its_own_limit(engine):
    got = run_many([Statement("select id from a", limit=2),
                    Statement("select id from a", limit=1)], ".", dialect="duckdb")
    assert (len(got[0].rows), len(got[1].rows)) == (2, 1)


def test_one_bad_statement_fails_alone_with_its_own_reason(engine):
    got = run_many([Statement("select count(*) as n from a", limit=1),
                    Statement("select * from no_such_table", limit=1),
                    Statement("select n from b", limit=1)], ".", dialect="duckdb")
    assert not got[0].failed and got[0].rows == [{"n": 3}]
    assert got[1].failed and "no_such_table" in got[1].why
    assert not got[2].failed and got[2].rows == [{"n": 10}]


def test_an_empty_answer_is_not_a_failure(engine):
    got = run_many([Statement("select id from a where false", limit=5),
                    Statement("select n from b", limit=1)], ".", dialect="duckdb")
    assert not got[0].failed and got[0].rows == []


def test_an_engine_with_no_row_to_json_runs_one_at_a_time(engine):
    got = run_many([Statement("select n from b", limit=1),
                    Statement("select count(*) as n from a", limit=1)], ".", dialect="trino")
    assert len(engine) == 2
    assert got[1].rows == [{"n": 3}]


def test_batches_are_bounded(engine, monkeypatch):
    monkeypatch.setattr(probe, "BATCH_STATEMENTS", 4)
    run_many([Statement("select n from b", limit=1) for _ in range(10)], ".", dialect="duckdb")
    assert len(engine) == 3


def test_the_label_not_the_position_decides_where_a_row_goes():
    rows = [{"assay_stmt": "s1", "assay_ord": 1, "assay_row": '{"x": 2}'},
            {"assay_stmt": "s0", "assay_ord": 2, "assay_row": '{"x": 1}'},
            {"assay_stmt": "s0", "assay_ord": 1, "assay_row": '{"x": 0}'}]
    assert unwrap_many(rows, 2) == [[{"x": 0}, {"x": 1}], [{"x": 2}]]


def test_the_wrapped_statement_parses_on_every_engine_it_claims():
    import sqlglot
    for dialect in ("duckdb", "postgres", "bigquery", "snowflake", "databricks"):
        sql = wrap_many([Statement("select 1 as a"), Statement("with c as (select 2 as b) "
                                                               "select b from c")], dialect)
        sqlglot.parse_one(sql, read=dialect)


def test_a_batch_is_filed_under_the_command_that_sent_it():
    from dbt_assay.probe import _shared_caller
    assert _shared_caller(["assay.feeds.profile", "assay.feeds.sample"]) == "assay.feeds"
    assert _shared_caller(["assay.elementary", "assay.elementary"]) == "assay.elementary"
    assert _shared_caller(["assay.feeds.x", "assay.rows.y"]) == "assay.batch"
