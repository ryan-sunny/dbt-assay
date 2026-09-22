"""What a statement cost the warehouse, and the difference between failing and finding nothing.

*** assay IS ABOUT TO BE SHOWN TO PEOPLE WHO DO NOT RUN DUCKDB. ***
For a BigQuery user every probe statement is a line item, and the first question they ask is what
a sweep will cost. Until `warehouse_calls` existed assay could not answer it -- not before the run
and not after it.

*** AND `[]` MEANT BOTH "IT FAILED" AND "NO ROWS". ***
Reported from the field: `data_monitoring_metrics` holds 27 distinct days of writes on the box and
`assay volume` said "only 0 writes recorded". The cadence statement had failed, `run_sql` returned
an empty list, and nothing between there and the output could tell the two apart.
"""
from __future__ import annotations

import json

from dbt_assay import cost, probe
from dbt_assay.store import NEVER_PRUNED, PRUNABLE, Store

BQ = cost.RateCard(engine="bigquery", name="bigquery.on_demand.2026", usd_per_tb_scanned=6.25)


# ------------------------------------------------------------------ a Result is not a truth value

def test_a_result_refuses_to_be_used_as_a_boolean():
    """*** `if not got:` IS THE BUG, SO IT RAISES RATHER THAN QUIETLY MEANING SOMETHING ELSE. ***

    An object is truthy. A call site left as `if not run_sql(...)` would silently stop firing and
    the failure branch would disappear -- the fix shipping as a no-op, which is worse than not
    shipping it. Thirteen call sites had to change and this is what proved each one had.
    """
    for res in (probe.Result(), probe.Result(rows=[{"n": 1}]), probe.Result(failed=True)):
        try:
            bool(res)
        except TypeError as e:
            assert "not a truth value" in str(e)
        else:
            raise AssertionError("a Result must not be usable as a condition")


def test_empty_and_failed_are_different_results():
    assert probe.Result(rows=[]).failed is False
    assert probe.Result(failed=True, why="no such table").rows == []


# ------------------------------------------------------------------------------ what it would cost

def test_bytes_are_rows_times_the_declared_widths():
    """BigQuery prices a scan as rows x sum(width(type)), which is why an estimate is possible at
    all without a credential: assay builds the SQL and the manifest declares the types."""
    got, basis = cost.estimate(["id", "name"], {"id": "INT64", "name": "STRING"}, 1_000_000, BQ)
    assert basis == "declared_types"
    # 8 for INT64, 2 + 32 nominal for a STRING whose real width nothing here can know.
    assert got == (8 + 34) * 1_000_000


def test_one_unknown_column_makes_the_whole_estimate_unknown():
    """*** A PARTIAL ESTIMATE UNDERSTATES THE BILL BY EXACTLY THE PART IT COULD NOT SEE. ***

    And nothing on the row would say so. Same failure as a column mixing measured and guessed
    numbers, which is the one `estimate_basis` exists to prevent.
    """
    got, basis = cost.estimate(["id", "mystery"], {"id": "INT64"}, 1_000_000, BQ)
    assert got is None and basis == "unknown"


def test_a_nested_type_is_not_sized_from_its_name():
    assert cost.width_of("ARRAY<STRING>", "bigquery") is None
    assert cost.width_of("STRUCT<a INT64>", "bigquery") is None


def test_no_configured_rate_means_no_dollar_figure_at_all():
    """*** A NUMBER assay CANNOT JUSTIFY IS WORSE THAN NO NUMBER. ***
    None is not zero: zero would read as "this query is free" on a warehouse that bills."""
    unpriced = cost.RateCard.from_config({"engine": "bigquery"}, "bigquery")
    assert unpriced.price(42_000_000, 900) is None
    assert BQ.price(42_000_000, 900) == (42_000_000 / cost.USD_PER_TB) * 6.25


def test_local_duckdb_is_free_and_the_label_carries_the_claim():
    """MotherDuck speaks the same dialect and does bill, so the zero is NAMED rather than
    anonymous and `cost.engine` can override it."""
    duck = cost.RateCard.from_config({}, "duckdb")
    assert duck.name == "duckdb.local"
    assert duck.price(10**12, 1000) == 0.0


def test_snowflake_prices_the_wall_clock_because_that_is_what_it_bills():
    sf = cost.RateCard.from_config(
        {"engine": "snowflake", "rate_card": "sf", "usd_per_credit": 3.0,
         "credits_per_hour": 1}, "snowflake")
    assert sf.price(None, 3_600_000) == 3.0


def test_a_rate_that_is_not_a_number_is_refused_rather_than_ignored():
    from dbt_assay.config import Config
    for bad in ({"usd_per_tb_scanned": "six dollars"}, {"usd_per_credit": -1}):
        try:
            Config.from_dict({"cost": bad})
        except ValueError:
            continue
        raise AssertionError(f"{bad} was accepted")


# ---------------------------------------------------------------------------------- the dry run

def _target(rel="db.main.orders", cols=("id", "name")):
    return probe.Target(relation=rel, uid="model.p.orders", columns=list(cols),
                        why="2 models read it; grain unknown")


def test_dry_run_prices_a_sweep_without_touching_a_warehouse(tmp_path):
    """The feature that answers "what will this cost me" BEFORE a stranger points assay at a
    warehouse they pay for. No credential, no connection -- which also makes it a test."""
    s = Store(str(tmp_path / "s.duckdb"))
    probe.write(s, [probe.Observation("db.main.orders", "id", row_count=1_000_000,
                                      non_null=1_000_000, distinct_ct=1_000_000,
                                      status="unique")])
    est = probe.dry_run([_target()], s, dialect="bigquery", rate=BQ,
                        types={"db.main.orders": {"id": "INT64", "name": "STRING"}})
    assert est["bytes"] == (8 + 34) * 1_000_000
    assert est["usd"] > 0 and est["unpriced"] == 0
    assert est["statements"][0]["sql"].startswith("select count(*)")
    s.close()


def test_a_statement_it_cannot_price_is_reported_and_not_averaged_in(tmp_path):
    s = Store(str(tmp_path / "s.duckdb"))
    est = probe.dry_run([_target()], s, dialect="bigquery", rate=BQ, types={})
    assert est["bytes"] is None and est["unpriced"] == 1
    assert est["statements"][0]["basis"] == "unknown"
    s.close()


# ------------------------------------------------------------------------------- the ledger itself

def _ran(store, sql="select count(*) from db.main.orders", **kw):
    """Record one statement as though it had been issued."""
    res = probe.Result(rows=kw.pop("rows", [{"n": 1}]), failed=kw.pop("failed", False),
                       why=kw.pop("why", ""), wall_ms=kw.pop("wall_ms", 120))
    probe._record(sql, res, caller=kw.pop("caller", "assay.probe.keys"),
                  kind=kw.pop("kind", "key_scan"), **kw)
    return res


def test_a_store_records_what_it_spent_without_being_asked(tmp_path):
    """*** COMPLETE BY CONSTRUCTION, NOT BY EVERY COMMAND REMEMBERING TO OPT IN. ***
    Eleven commands can reach a warehouse and more will exist; a list of them goes stale and the
    symptom is money quietly missing. A statement can only be issued by a process holding a store.
    """
    s = Store(str(tmp_path / "s.duckdb"))
    assert probe.ledger() is not None, "opening a store starts the ledger"
    _ran(s)
    rows = s.con.execute("select caller, statement_kind, wall_ms, failed from "
                         "warehouse_calls").fetchall()
    assert rows == [("assay.probe.keys", "key_scan", 120, False)]
    s.close()
    assert probe.ledger() is None, "closing the store stops it"


def test_the_ledger_records_the_failure_too(tmp_path):
    s = Store(str(tmp_path / "s.duckdb"))
    _ran(s, rows=[], failed=True, why="Relation does not exist")
    failed, detail = s.con.execute("select failed, detail from warehouse_calls").fetchone()
    assert failed is True and "does not exist" in detail
    led = cost.warehouse_ledger(s)
    assert led["failed"] == 1
    assert led["usd"] is None or led["usd"] == 0.0, "a failed statement is out of the money"
    s.close()


def test_the_same_statement_twice_is_two_rows_and_one_call_id(tmp_path):
    """*** THE ONE TABLE HERE WITH NO PRIMARY KEY, DELIBERATELY. ***
    `call_id` hashes the STATEMENT, so it repeats. A key would mean `insert or replace` and two
    identical statements in one pass would collapse into one row -- money spent, unrecorded.
    """
    s = Store(str(tmp_path / "s.duckdb"))
    _ran(s)
    _ran(s)
    ids = [r[0] for r in s.con.execute("select call_id from warehouse_calls").fetchall()]
    assert len(ids) == 2 and len(set(ids)) == 1
    s.close()


def test_a_recorded_statement_carries_its_columns_for_later_attribution(tmp_path):
    s = Store(str(tmp_path / "s.duckdb"))
    probe.enrich(dialect="bigquery", rate=BQ,
                 types={"db.main.orders": {"id": "INT64", "name": "STRING"}})
    probe.write(s, [probe.Observation("db.main.orders", "id", row_count=1_000,
                                      non_null=1_000, distinct_ct=1_000, status="unique")])
    _ran(s, relation="db.main.orders", columns=["id", "name"])
    row = s.con.execute("select columns_touched, column_names, rows_scanned, bytes_estimated, "
                        "estimate_basis, rate_card from warehouse_calls").fetchone()
    assert row[0] == 2
    assert json.loads(row[1]) == ["id", "name"]
    assert row[2] == 1_000
    assert row[3] == (8 + 34) * 1_000
    assert row[4] == "declared_types" and row[5] == "bigquery.on_demand.2026"
    s.close()


def test_bytes_measured_stays_null_rather_than_holding_the_estimate(tmp_path):
    """A column silently mixing measured and guessed numbers is the varchar-declared-INTEGER-
    stored defect again: two facts under one name, and no reader able to tell which they have."""
    s = Store(str(tmp_path / "s.duckdb"))
    probe.enrich(dialect="bigquery", rate=BQ,
                 types={"db.main.orders": {"id": "INT64"}})
    _ran(s, relation="db.main.orders", columns=["id"])
    measured, = s.con.execute("select bytes_measured from warehouse_calls").fetchone()
    assert measured is None
    s.close()


def test_estimate_basis_is_never_empty(tmp_path):
    s = Store(str(tmp_path / "s.duckdb"))
    _ran(s)                                       # nothing known about the relation at all
    basis, = s.con.execute("select estimate_basis from warehouse_calls").fetchone()
    assert basis == "unknown", "an unset basis is the whole failure this column prevents"
    s.close()


def test_a_ledger_write_that_fails_never_breaks_the_command(tmp_path):
    """The ledger must not be able to break the thing it is measuring."""
    s = Store(str(tmp_path / "s.duckdb"))
    led = probe.ledger()
    s.con.execute("drop table warehouse_calls")
    s.con.execute("create table warehouse_calls (nope varchar)")
    _ran(s)
    assert led.unrecorded == 1, "a failure to record is counted, not raised and not silent"
    s.close()


def test_two_runs_over_one_store_print_the_same_dollar_total(tmp_path):
    """`sum(usd)` over a DOUBLE depends on row order, so the same store can print two different
    lifetime totals. The terms are sorted before they are added."""
    s = Store(str(tmp_path / "s.duckdb"))
    probe.enrich(dialect="bigquery", rate=BQ,
                 types={"db.main.orders": {"id": "INT64"}})
    probe.write(s, [probe.Observation("db.main.orders", "id", row_count=n, non_null=n,
                                      distinct_ct=n, status="unique") for n in (7,)])
    for i in range(25):
        _ran(s, sql=f"select {i} from db.main.orders", relation="db.main.orders", columns=["id"])
    assert cost.warehouse_ledger(s)["usd"] == cost.warehouse_ledger(s)["usd"]
    s.close()


def test_the_warehouse_ledger_is_never_pruned():
    assert "warehouse_calls" in NEVER_PRUNED
    assert "warehouse_calls" not in PRUNABLE
