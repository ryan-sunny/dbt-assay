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
            return [dict(zip([c[0] for c in d.description], r, strict=True))
                    for r in d.execute(sql).fetchall()]

    got = verify_minimality({"main_t": ["section_id", "party_ordinal", "county"]},
                            _P, ".", None, "dbt")
    assert got["main_t"] == {"section_id": "adds", "party_ordinal": "adds", "county": "carried"}

    # A single-column key has no minimality question: there is nothing to drop.
    assert verify_minimality({"main_t": ["section_id"]}, _P, ".", None, "dbt") == {}

    # *** A RELATION THAT COULD NOT BE COUNTED IS ABSENT, NEVER GUESSED. ***
    class _Dead:
        @staticmethod
        def run_sql(*a, **k):
            return []

    assert verify_minimality({"main_t": ["a", "b"]}, _Dead, ".", None, "dbt") == {}
