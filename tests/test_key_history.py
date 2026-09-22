"""The store's one measurement of the actual data was the one that forgot.

`model_decisions` keys on version so `effectiveness` works. `findings` and `edge_facts` key on
`run_id`. `observed_keys` keyed on (relation, column) with `insert or replace`, so there was
exactly one observation per column, ever -- and assay could say a key holds TODAY and could never
say a key that held last week has stopped.

That second sentence is the one that matters. A key silently ceasing to be a key is how a
warehouse goes wrong: every count past the join inflates, nothing errors, and the tests still pass
because they were written while it was true.
"""
from __future__ import annotations

import time

import duckdb

from dbt_assay import probe
from dbt_assay.probe import Result
from dbt_assay.store import Store


def _two_passes(tmp_path, first, second):
    s = Store(str(tmp_path / "assay.duckdb"))
    probe.write(s, first)
    time.sleep(0.005)
    probe.write(s, second)
    return s


def test_a_key_that_stops_holding_is_reported(tmp_path):
    """*** THE FAILURE THAT CORRUPTS A WAREHOUSE, AND NOTHING COULD SEE IT. ***"""
    s = _two_passes(
        tmp_path,
        [probe.Observation("main.t", "id", 100, 100, 100, "unique")],
        [probe.Observation("main.t", "id", 140, 140, 100, "has_duplicates")])
    try:
        got = probe.changes(s)
        assert [f.check for f in got] == ["key_stopped_holding"], got
        assert "100 distinct over 140 rows" in got[0].summary
        assert got[0].base == 3, "a key that stopped holding is not a footnote"
        # it must say WHEN, and must not claim to know when between the two it happened
        assert "held at" in got[0].detail and "only that it did" in got[0].detail
    finally:
        s.close()


def test_one_observation_produces_nothing_and_that_is_not_a_clean_bill(tmp_path):
    """*** AN ABSENT COMPARISON IS NOT A PASS. ***

    Two observations is the minimum for a change to exist. A column probed once must be absent
    from this rather than reported stable, which is the same rule `hop_drops_most_rows` follows
    for an uncounted hop.
    """
    s = Store(str(tmp_path / "assay.duckdb"))
    try:
        probe.write(s, [probe.Observation("main.t", "id", 100, 100, 100, "unique")])
        assert probe.changes(s) == []
    finally:
        s.close()


def test_an_unchanged_column_is_silent(tmp_path):
    """A check that fires on everything is a check nobody reads."""
    s = _two_passes(
        tmp_path,
        [probe.Observation("main.t", "id", 100, 100, 100, "unique")],
        [probe.Observation("main.t", "id", 120, 120, 120, "unique")])
    try:
        assert probe.changes(s) == []
    finally:
        s.close()


def test_both_minimality_directions_are_reported(tmp_path):
    """A column that STARTS adding identifying power has grown the minimal key, and a uniqueness
    test written without it is now testing the wrong thing. The other direction is real and
    smaller: a superset of a key is still unique, so nothing fails, the key is just too wide."""
    s = _two_passes(
        tmp_path,
        [probe.Observation("main.t", "a", 10, 10, 4, "has_duplicates", "", "carried"),
         probe.Observation("main.t", "b", 10, 10, 9, "has_duplicates", "", "adds")],
        [probe.Observation("main.t", "a", 12, 12, 6, "has_duplicates", "", "adds"),
         probe.Observation("main.t", "b", 12, 12, 9, "has_duplicates", "", "carried")])
    try:
        got = {f.check: f for f in probe.changes(s)}
        assert set(got) == {"key_column_started_mattering", "key_column_stopped_mattering"}
        assert got["key_column_started_mattering"].base > got["key_column_stopped_mattering"].base
    finally:
        s.close()


def test_write_appends_rather_than_replacing(tmp_path):
    """The whole point. `insert or replace` on (relation, column) is what erased the series."""
    s = _two_passes(
        tmp_path,
        [probe.Observation("main.t", "id", 1, 1, 1, "unique")],
        [probe.Observation("main.t", "id", 2, 2, 2, "unique")])
    try:
        assert len(probe.history(s, "main.t", "id")) == 2
        # ...and `read` hands back only the LATEST, or a caller gets several and no way to tell
        # which is live -- the defect `traversal` had when it returned twelve verdicts for four.
        latest = probe.read(s)["main.t"]["id"]
        assert latest.row_count == 2
    finally:
        s.close()


def test_an_old_store_keeps_its_one_observation_as_the_first_of_the_series(tmp_path):
    """*** duckdb CANNOT ALTER A PRIMARY KEY, SO THE MIGRATION REBUILDS. ***

    Every existing row is CARRIED, not dropped: a store written before history holds one real
    observation per column, and discarding it would mean the first comparison could not happen
    until two MORE probes had run. A row predating `observed_at` entirely has a NULL there, which
    cannot sit in a primary key, so it is dated as the oldest thing present rather than lost.
    """
    p = str(tmp_path / "old.duckdb")
    db = duckdb.connect(p)
    db.execute("""create table observed_keys (
        relation varchar, column_name varchar, row_count bigint, non_null bigint,
        distinct_ct bigint, status varchar, detail varchar, observed_at timestamp, via varchar,
        primary key (relation, column_name))""")
    db.execute("insert into observed_keys values "
               "('main.t','id',100,100,100,'unique','',timestamp '2026-01-01','dbt-show')")
    db.execute("insert into observed_keys values "
               "('main.t','city',100,100,4,'has_duplicates','',NULL,'dbt-show')")
    db.close()

    s = Store(p)
    try:
        assert s.observations_kept == 2, "the migration dropped an observation"
        rows = dict(s.con.execute(
            "select column_name, observed_at from observed_keys").fetchall())
        assert rows["id"].year == 2026
        assert rows["city"].year == 1970, "a NULL timestamp was not dated as the oldest"
        key = s.con.execute(
            "select constraint_column_names from duckdb_constraints() "
            "where table_name = 'observed_keys' and constraint_type = 'PRIMARY KEY'").fetchone()
        assert "observed_at" in list(key[0]), "the key still cannot hold a series"
    finally:
        s.close()
    s = Store(p)
    try:
        assert s.observations_kept == 0, "the migration is not idempotent"
    finally:
        s.close()


def test_minimality_is_counted_and_equality_is_the_whole_test():
    """*** THIS WAS A JUDGED QUESTION AND IT IS ARITHMETIC. ***

    `column_is_part_of_the_key` asks a model whether a column is part of the MINIMAL set or is
    carried along because the others determine it. Drop it, recount, and if the number does not
    move it was carried. Two counts.

    Asked 109 times on a real warehouse, load-bearing for six models, listed weak in
    VERIFICATION.md, and its confidence measured worst exactly where it was most sure.
    """
    from dbt_assay.practices import verify_minimality

    d = duckdb.connect()
    d.execute("""create table main_t as select * from (values
        ('S1',1,'Weld'),('S1',2,'Weld'),('S2',1,'Mesa'),('S2',2,'Mesa'),('S3',1,'Weld')
    ) as v(section_id, party_ordinal, county)""")

    class _P:
        @staticmethod
        def run_sql(sql, *a, **k):
            return Result(rows=[dict(zip([c[0] for c in d.description], r, strict=True))
                                for r in d.execute(sql).fetchall()])

    got = verify_minimality({"main_t": ["section_id", "party_ordinal", "county"]},
                            _P, ".", None, "dbt")
    assert got["main_t"] == {"section_id": "adds", "party_ordinal": "adds", "county": "carried"}

    # A single-column key has no minimality question: there is nothing to drop.
    assert verify_minimality({"main_t": ["section_id"]}, _P, ".", None, "dbt") == {}

    # *** A RELATION THAT COULD NOT BE COUNTED IS ABSENT, NEVER GUESSED. ***
    class _Dead:
        @staticmethod
        def run_sql(*a, **k):
            return Result(failed=True, why="could not reach the warehouse")

    assert verify_minimality({"main_t": ["a", "b"]}, _Dead, ".", None, "dbt") == {}


# ---------------------------------------------- a bounded probe must WALK the project, not restart

class _T:
    """A probe target, thin enough to order."""

    def __init__(self, relation):
        self.relation = relation
        self.columns = ["id"]
        self.why = ""


def _observe(s, rel, when, status="unique"):
    s.con.execute(
        """insert or replace into observed_keys
           (relation, column_name, row_count, non_null, distinct_ct, status, detail,
            observed_at, via, minimality)
           values (?, 'id', 1, 1, 1, ?, '', ?, 'test', '')""", [rel, status, when])


def test_a_bounded_probe_walks_the_project_instead_of_re_reading_the_front(tmp_path):
    """*** `-n 8` ON 277 RELATIONS READ THE SAME EIGHT FOREVER. ***

    The order was whatever the manifest yielded, so coverage could not grow and the drift checks
    -- which need TWO observations of one relation before they can say anything -- could never
    reach a second one on anything past the first eight.

    This is the measurement that found it, as a test: three passes of -n 8 should touch 24
    distinct relations once each, not 8 relations three times.
    """
    import datetime as dt

    from dbt_assay import probe
    from dbt_assay.store import Store

    s = Store(str(tmp_path / "assay.duckdb"))
    try:
        all_rels = [f"main.r{i:03d}" for i in range(24)]
        t0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        touched = []
        for pass_no in range(3):
            chosen = probe.order_by_staleness([_T(r) for r in all_rels], s)[:8]
            touched += [t.relation for t in chosen]
            # one timestamp for the whole batch, exactly as `write` does
            for t in chosen:
                _observe(s, t.relation, t0 + dt.timedelta(hours=pass_no))
        assert len(set(touched)) == 24, f"only reached {len(set(touched))} relations"
        counts = dict(s.con.execute(
            "select relation, count(*) from observed_keys group by relation").fetchall())
        assert set(counts.values()) == {1}, f"a relation was re-probed: {counts}"
    finally:
        s.close()


def test_a_relation_that_cannot_be_counted_does_not_starve_the_cycle(tmp_path):
    """*** THE REQUIREMENT THAT WOULD BITE IF IT WERE MISSED. ***

    If a failed count wrote no row, that relation's `max(observed_at)` stays NULL, it sorts first
    forever, and it is re-probed on every run while nothing else advances. One unreadable relation
    starves the whole cycle -- silently, and the symptom is "probe seems to work but coverage
    never grows".

    `observe` records `unknown` rather than nothing, so an ATTEMPT is always logged. This asserts
    the ordering honours that.
    """
    import datetime as dt

    from dbt_assay import probe
    from dbt_assay.store import Store

    s = Store(str(tmp_path / "assay.duckdb"))
    try:
        rels = [f"main.r{i}" for i in range(6)]
        t0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        seen = []
        for pass_no in range(3):
            chosen = probe.order_by_staleness([_T(r) for r in rels], s)[:2]
            seen += [t.relation for t in chosen]
            for t in chosen:
                # main.r0 can never be counted. It still records an attempt.
                _observe(s, t.relation, t0 + dt.timedelta(hours=pass_no),
                         "unknown" if t.relation == "main.r0" else "unique")
        assert seen.count("main.r0") == 1, "the uncountable relation was probed repeatedly"
        assert len(set(seen)) == 6, f"the cycle stalled: {seen}"
    finally:
        s.close()


def test_the_same_store_picks_the_same_relations(tmp_path):
    """*** A BATCH SHARES ONE TIMESTAMP, SO TIES ARE THE NORMAL CASE HERE. ***

    Without a total order two runs against an unchanged store choose different subsets, which is
    `arbitrary_pick` -- the check this tool runs against other people's SQL -- and it is the same
    defect already fixed twice here: the `count(*) desc` run selection, and the page writing
    different bytes on identical input.
    """
    import datetime as dt

    from dbt_assay import probe
    from dbt_assay.store import Store

    s = Store(str(tmp_path / "assay.duckdb"))
    try:
        rels = [f"main.r{i}" for i in range(10)]
        one = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        for r in rels[:6]:
            _observe(s, r, one)                      # six share a timestamp exactly
        picks = {tuple(t.relation for t in probe.order_by_staleness([_T(r) for r in rels], s)[:5])
                 for _ in range(30)}
        assert len(picks) == 1, f"the choice is not deterministic: {picks}"
        # unobserved sort first, whatever order they arrive in
        shuffled = [_T(r) for r in reversed(rels)]
        first4 = [t.relation for t in probe.order_by_staleness(shuffled, s)[:4]]
        assert set(first4) == {"main.r6", "main.r7", "main.r8", "main.r9"}, first4
    finally:
        s.close()


def test_no_store_still_orders_totally(tmp_path):
    """A first run has nothing to compare against and must still be deterministic."""
    from dbt_assay import probe

    got = [t.relation for t in probe.order_by_staleness([_T("b"), _T("a"), _T("c")], None)]
    assert got == ["a", "b", "c"]
