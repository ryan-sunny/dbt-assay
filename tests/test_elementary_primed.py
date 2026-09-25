"""`elementary.primed`: the same readings from two batches instead of eight calls."""
import duckdb

from dbt_assay import elementary as elem
from dbt_assay.probe import Result

SCHEMA = "el"


def _warehouse():
    c = duckdb.connect()
    c.execute(f"create schema {SCHEMA}")
    c.execute(f"""create table {SCHEMA}.elementary_test_results as select
        'test.p.t' || (i % 7) as test_unique_id, 'dbt_test' as test_type,
        case when i % 5 = 0 then 'fail' when i % 11 = 0 then 'skipped' else 'pass' end as status,
        timestamp '2026-09-01' + to_days(i % 20) as detected_at, 'm' || (i % 3) as table_name,
        'unique' as test_name, 'id' as column_name, i as id, timestamp '2026-09-01' as created_at
        from range(60) t(i)""")
    c.execute(f"""create table {SCHEMA}.dbt_invocations as select
        timestamp '2026-09-01' + to_days(i) as run_started_at from range(10) t(i)""")
    c.execute(f"create table {SCHEMA}.dbt_tests as select 'test.p.t' || i as unique_id "
              f"from range(9) t(i)")
    return c


def _runner(con, calls):
    """A runner on `con`; `calls` gets one entry per call the real one would send."""
    def run(sql, n, _single=True):
        if _single:
            calls.append(sql)
        try:
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description]
            return Result(rows=[dict(zip(cols, r)) for r in cur.fetchmany(n)])
        except Exception as e:                                   # noqa: BLE001
            return Result(failed=True, why=str(e)[:200])

    def many(pairs, max_rows=None):
        if pairs:
            calls.append(f"batch of {len(pairs)}")
        return [run(s, n, _single=False) for s, n in pairs]
    run.many = many
    return run


def _read(runner):
    cad = elem.build_cadence(runner, SCHEMA)
    rep = elem.read(runner, SCHEMA, fallback=cad)
    return (repr(cad), [(r.relation, r.state, r.rows) for r in rep.readings],
            repr(rep.tests), elem.test_coverage(runner, SCHEMA),
            elem.latest_test_results(runner, SCHEMA))


def test_the_same_readings_from_two_batches():
    con = _warehouse()
    plain_calls, primed_calls = [], []
    plain = _read(_runner(con, plain_calls))
    runner = elem.primed(_runner(con, primed_calls), SCHEMA)
    batches = len(primed_calls)
    got = _read(runner)
    assert got == plain
    assert batches == 2, primed_calls               # counts, then everything else
    assert len(primed_calls) == 2, primed_calls     # and the readers sent nothing more
    assert len(plain_calls) > 2


def test_an_absent_package_never_shares_a_batch_with_the_rest():
    con = duckdb.connect()
    con.execute(f"create schema {SCHEMA}")
    sent = []
    base = _runner(con, [])
    inner = base.many

    def many(pairs, max_rows=None):
        sent.append([s for s, _n in pairs])
        return inner(pairs)
    base.many = many
    runner = elem.primed(base, SCHEMA)
    assert len(sent) == 2 and sent[1] == []          # nothing exists, so pass 2 is empty
    rep = elem.read(runner, SCHEMA)
    assert {r.state for r in rep.readings} == {elem.ABSENT}
