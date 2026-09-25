"""`bulk.many` writes exactly what `executemany` writes, in one statement."""
from datetime import date, datetime, timezone

import duckdb
import pytest

from dbt_assay import bulk

DDL = """create table t (k varchar primary key, i integer, b bigint, d double, f boolean,
                        ts timestamp, tz timestamptz, dt date, j json, s varchar)"""

ROWS = [
    ["a", 1, 2**40, 0.1, True, datetime(2026, 9, 25, 10, 0, 0, 123456),  # noqa: DTZ001
     datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc), date(2026, 9, 25), '{"x": [1, 2]}',
     "it's \"quoted\"\nnew line é"],
    ["b", None, None, 1e-7, False, None, None, None, None, None],
    ["c", -3, 0, 12345.678901234, None, "2026-09-25 11:00:00", "2026-09-25T11:00:00+02:00",
     "2026-01-02", "[]", ""],
]


def _both(sql, rows, ddl=DDL):
    out = []
    for write in ((lambda c: c.executemany(sql, rows)), (lambda c: bulk.many(c, sql, rows))):
        c = duckdb.connect()
        c.execute("SET TimeZone = 'UTC'")
        c.execute(ddl)
        write(c)
        out.append(c.execute("select * replace (tz::varchar as tz) from t order by k").fetchall()
                   if "tz" in ddl else c.execute("select * from t order by k").fetchall())
    return out


def test_every_type_the_store_uses_lands_the_same():
    a, b = _both("insert or replace into t values (?,?,?,?,?,?,?,?,?,?)", ROWS)
    assert a == b and len(a) == 3


def test_a_column_list_and_an_expression_among_the_placeholders():
    a, b = _both("insert or replace into t (k, s, i, ts) values (?, ?, ?, now())",
                 [["x", "one", 1], ["y", None, 2]])
    assert [r[:3] for r in a] == [r[:3] for r in b] and all(r[5] is not None for r in b)
    a, b = _both("insert into t (k, tz) values (?, cast(? as timestamptz))",
                 [["x", "2026-09-25 10:00:00+00"], ["y", None]])
    assert a == b


class Spy:
    """A connection that records its statements and has no loop to fall back to."""

    def __init__(self, con, calls):
        self.con, self.calls = con, calls

    def execute(self, *a):
        self.calls.append(a[0])
        return self.con.execute(*a)

    def executemany(self, *a):
        raise AssertionError("fell back to the loop")


def test_it_is_one_statement():
    calls = []
    c = duckdb.connect()
    c.execute(DDL)

    bulk.many(Spy(c, calls), "insert into t (k, i) values (?, ?)",
              [[str(n), n] for n in range(5000)])
    assert len([s for s in calls if s.lstrip().startswith("insert")]) == 1
    assert c.execute("select count(*), sum(i) from t").fetchone() == (5000, sum(range(5000)))


@pytest.mark.parametrize("rows", [
    [["x", 1], ["x", 2], ["y", 3]],                 # one key twice: the loop's last-wins
    [["x", float("nan")], ["y", 1.0]],              # not JSON
    [["x", [1, 2]], ["y", [3]]],                    # a LIST, bound as one
])
def test_what_one_statement_cannot_do_goes_through_the_loop_unchanged(rows):
    ddl = "create table t (k varchar primary key, v varchar)" if isinstance(rows[0][1], list) \
        else "create table t (k varchar primary key, v double)"
    a, b = _both("insert or replace into t values (?, ?)", rows, ddl)
    assert str(a) == str(b)


def test_one_key_twice_keeps_the_row_the_loop_keeps_and_stays_one_statement():
    for verb, want in (("insert or replace", 2.0), ("insert or ignore", 1.0)):
        c = duckdb.connect()
        c.execute("create table t (k varchar primary key, v double)")
        bulk.many(Spy(c, []), f"{verb} into t values (?, ?)", [["x", 1], ["x", 2], ["y", 3]])
        assert c.execute("select v from t where k = 'x'").fetchone()[0] == want


@pytest.mark.parametrize("zone", ["UTC", "America/Denver"])
@pytest.mark.parametrize("hours", [0, -6])
def test_an_aware_datetime_is_the_same_instant_in_either_column(zone, hours):
    """Text with an offset cast to `timestamp` dropped the offset: 10:00-06:00 became 10:00."""
    from datetime import timedelta
    when = datetime(2026, 9, 25, 10, 0, tzinfo=timezone(timedelta(hours=hours)))
    got = []
    for write in ("loop", "one"):
        c = duckdb.connect()
        c.execute(f"SET TimeZone = '{zone}'")
        c.execute("create table t (k varchar primary key, ts timestamp, tz timestamptz)")
        rows = [["a", when, when], ["b", when, when]]
        (c.executemany if write == "loop" else (lambda s, r, c=c: bulk.many(c, s, r)))(
            "insert into t values (?, ?, ?)", rows)
        got.append(c.execute("select ts, epoch(tz) from t order by k").fetchall())
    assert got[0] == got[1]


def test_a_plain_insert_of_one_key_twice_still_fails():
    c = duckdb.connect()
    c.execute("create table t (k varchar primary key, v double)")
    with pytest.raises(duckdb.ConstraintException):
        bulk.many(c, "insert into t values (?, ?)", [["x", 1], ["x", 2]])
