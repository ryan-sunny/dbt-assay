"""A judged command says what it will do net of the store, and says it is alive while doing it.

*** 43 MINUTES OF SILENCE, AND A PLAN LINE WRONG BY 6x IN THE CHEAP DIRECTION. ***
Reported from the field (25.10, 25.2): `semantics` printed "301 calls" and nothing else for 43
minutes; the re-run printed "301 calls" again and made 48 in seventeen seconds. `read --dry-run`
quoted half of what the run cost.
"""
from __future__ import annotations

import time

from fakejev import install
from test_cost import FakeClient, _questions, recipe
from typer.testing import CliRunner

from dbt_assay import jev
from dbt_assay.cli import app
from dbt_assay.jev import decide, plan
from dbt_assay.store import Store

runner = CliRunner()


def test_the_plan_counts_what_the_store_already_answers(tmp_path):
    s = Store(str(tmp_path / "s.duckdb"))
    try:
        asked = recipe(s, "model.p.a")
        decide(s, FakeClient(n_answers=2), asked, _questions(2), prompt_version="v1",
               caller="assay.claims")
        fresh = recipe(s, "model.p.b")
        p = plan(s, [(asked, _questions(2), "v1"), (fresh, _questions(2), "v1")],
                 "assay.claims")
        assert (p.calls, p.cached) == (1, 1)
        assert "1 call(s) to make, 1 already answered" in p.line()
        # A new question version is a new question: nothing is cached for it.
        assert plan(s, [(asked, _questions(2), "v2")], "assay.claims").calls == 1
    finally:
        s.close()


def test_the_price_uses_this_stores_measured_tokens_per_state(tmp_path):
    s = Store(str(tmp_path / "s.duckdb"))
    try:
        for i in range(6):
            decide(s, FakeClient(n_answers=1, usage={"input_tokens": 4000}, call_id=f"c{i}"),
                   recipe(s, f"model.p.m{i}"), _questions(1), prompt_version="v1",
                   caller="assay.claims")
        ratio, basis = jev.token_ratio(s, "assay.claims")
        assert ratio and ratio > 1 and "6 calls of assay.claims" in basis
        p = plan(s, [(recipe(s, "model.p.new"), _questions(1), "v1")], "assay.claims")
        # one state, priced at the measured ratio: the same tokens the six measured calls billed
        assert abs(p.usd - 4000 * jev.USD_PER_INPUT_TOKEN) < 1e-9, p
        assert p.price_basis
    finally:
        s.close()


def test_the_rate_is_measured_from_the_ledger_and_ignores_the_gaps_between_runs(tmp_path):
    s = Store(str(tmp_path / "s.duckdb"))
    try:
        s.con.execute(jev.DDL)
        base = 1_700_000_000
        stamps = [base + 2 * i for i in range(10)] + [base + 10_000 + 2 * i for i in range(10)]
        for i, t in enumerate(stamps):
            s.con.execute("insert into model_calls (call_id, caller, called_at) "
                          "values (?, 'assay.read', to_timestamp(?))", [f"c{i}", t])
        rate, basis = jev.observed_rate(s, "assay.read")
        assert rate == 2.0 and "calls of assay.read" in basis
    finally:
        s.close()


def test_semantics_dry_run_plans_and_writes_no_store(project_dir, tmp_path):
    store = tmp_path / "never.duckdb"
    r = runner.invoke(app, ["semantics", "-t", str(project_dir), "--store", str(store),
                            "--config", str(tmp_path), "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "call(s) to make" in r.output and "calls ·" not in r.output
    assert not store.exists()


def test_a_second_run_is_planned_as_answered_and_progress_is_printed(project_dir, tmp_path,
                                                                        monkeypatch):
    install(monkeypatch)
    args = ["semantics", "-t", str(project_dir), "--store", str(tmp_path / "s.duckdb"),
            "--config", str(tmp_path)]
    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    # Off a terminal the line is printed; the last one says done of total.
    assert "semantics 3/3" in " ".join(first.output.split()), first.output
    second = runner.invoke(app, args)
    assert "0 call(s) to make, 3 already answered" in " ".join(second.output.split()), \
        second.output


def test_the_default_ticker_speaks_only_after_thirty_seconds(monkeypatch, capsys):
    from dbt_assay import cli
    now = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    tick = cli._default_ticker()
    client = FakeClient()
    client.spent_usd = 0.01
    with cli.console.capture() as cap:
        tick(True, client)
        now[0] = 10
        tick(True, client)
    assert cap.get() == ""
    with cli.console.capture() as cap:
        now[0] = 45
        tick(False, client)
    assert "judged 3 so far" in cap.get()
