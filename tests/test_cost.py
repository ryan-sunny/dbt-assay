"""*** THE LEDGER'S FIRST JOB IS NOT TO REPEAT THE BUG IT WAS BUILT TO FIX. ***

`model_decisions` is one row per ANSWER and carries its CALL's token count on every one of them,
so a batch of eight questions about one state counts eight times. On the field store that reads
101,163,351 tokens against 31,426,560 spent, and $4.25 against $1.32 -- and the $4.25 was written
into a build spec as the number to check the first implementation against.

So the first test here is a batched call, and it asserts the total is the call's.
"""
from __future__ import annotations

from typing import ClassVar

import pytest

from dbt_assay import cost
from dbt_assay.jev import USD_PER_INPUT_TOKEN, decide
from dbt_assay.store import Store

RATE = USD_PER_INPUT_TOKEN


class FakeClient:
    """One canned response. `decide` reads `.model` and never opens a connection."""

    NO_USAGE: ClassVar[dict] = {}   # distinct from None, which means "the default 1,000 tokens"

    def __init__(self, n_answers: int = 8, usage: dict | None = None,
                 call_id: str | None = "gen-1", model: str = "fake/jev-1"):
        self.model = model
        self.n = n_answers
        self.call_id = call_id
        self.usage = ({"input_tokens": 1000} if usage is None
                      else (None if usage == self.NO_USAGE else usage))
        self.asked = 0

    def ask(self, _state, questions, *, caller: str = "assay"):
        self.asked += 1
        out = {"answers": {q: {"type": "noul", "noul": 0.9} for q in questions}}
        if self.usage is not None:
            out["usage"] = dict(self.usage)
        if self.call_id is not None:
            out["id"] = self.call_id
        out["model"] = self.model
        return out


def _questions(n: int) -> dict:
    """Real question ids: `decide` refuses a prefix no bank claims, and rightly."""
    return {f"sentence__{i}": {"type": "noul", "instructions": {"question": "?"}}
            for i in range(n)}


def _ask(store, client, key="model.p.stg_bad_notnull", n=8, version="v1"):
    return decide(store, client, {"sql": "select 1"}, _questions(n),
                  decision_key=key, prompt_version=version, caller="assay.claims")


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "s.duckdb"))
    yield s
    s.close()


def test_a_batched_call_is_counted_once(store):
    """*** EIGHT ANSWERS, ONE CALL, ONE PRICE. ***

    This is the whole reason `model_calls` exists. Summing `model_decisions.input_tokens` would
    return 8,000 tokens for a call that used 1,000, which is exactly how a spec came to carry
    $4.24 for a store that had spent $1.32.
    """
    _ask(store, FakeClient(n_answers=8, usage={"input_tokens": 1000}))
    rows = store.con.execute("select count(*) from model_decisions").fetchone()[0]
    assert rows == 8, "eight answers should be eight decision rows"
    assert store.con.execute("select count(*) from model_calls").fetchone()[0] == 1

    led = cost.ledger(store)
    assert led["calls"] == 1
    assert led["input_tokens"] == 1000
    assert led["usd"] == pytest.approx(1000 * RATE)
    naive = store.con.execute("select sum(input_tokens) from model_decisions").fetchone()[0]
    assert naive == 8000, "the row-level column still holds the call's number, per row"
    assert led["input_tokens"] != naive


def test_a_call_with_no_usage_is_excluded_and_counted(store):
    """*** AN ABSENT MEASUREMENT IS NOT A ZERO AND NEVER AN AVERAGE. ***"""
    _ask(store, FakeClient(usage=FakeClient.NO_USAGE, call_id="no-usage"), key="model.p.stg_ok_notnull")
    led = cost.ledger(store)
    assert led["calls"] == 1
    assert led["calls_without_usage"] == 1
    assert led["usd"] == 0.0 and led["input_tokens"] == 0
    tok, usd = store.con.execute(
        "select input_tokens, usd from model_calls").fetchone()
    assert tok is None and usd is None, "not a zero, which would read as a free call"


def test_the_rate_comes_off_the_row_and_is_not_recomputed(store):
    """*** A PRICE CHANGE MUST NOT SILENTLY REWRITE WHAT WAS ALREADY SPENT. ***

    The same argument `prompt_version` already makes. A row priced at yesterday's rate keeps
    yesterday's rate, and the report multiplies by what is stored rather than by the constant
    shipping now.
    """
    _ask(store, FakeClient(usage={"input_tokens": 1000}, call_id="old-rate"))
    store.con.execute("update model_calls set usd_per_input_token = ?", [RATE * 10])
    led = cost.ledger(store)
    assert led["usd"] == pytest.approx(1000 * RATE * 10)
    assert led["rates"] == [pytest.approx(RATE * 10)]


def test_two_runs_over_one_store_print_the_same_total(store):
    """*** A DOUBLE SUM MOVES WITH ROW ORDER, SO THE TOTAL IS BUILT FROM INTEGERS. ***"""
    for i in range(40):
        _ask(store, FakeClient(usage={"input_tokens": 1000 + i}, call_id=f"c{i}"),
             key=f"model.p.m{i}")
    a, b = cost.ledger(store)["usd"], cost.ledger(store)["usd"]
    assert a == b
    tok = store.con.execute("select sum(input_tokens) from model_calls").fetchone()[0]
    assert a == tok * RATE, "one multiply over an exact integer sum"


def test_output_tokens_are_recorded_and_never_priced(store):
    """Jev does not bill output. Counting it is useful; pricing it would be inventing a rate."""
    _ask(store, FakeClient(usage={"input_tokens": 1000, "output_tokens": 250}))
    led = cost.ledger(store)
    assert led["output_tokens"] == 250 and led["output_calls"] == 1
    assert led["usd"] == pytest.approx(1000 * RATE), "output must not be in the dollars"


def test_a_call_the_provider_did_not_name_still_has_an_id(store):
    """*** THE PROVIDER RETURNED NO ID FOR 9,762 OF 19,707 DECISIONS. ***

    Half a real store could not say which rows shared a call, so nothing could total it. assay
    mints its own and `id_source` says which happened, rather than letting a minted id pass as
    the provider's.
    """
    _ask(store, FakeClient(call_id=None, usage={"input_tokens": 500}))
    call_id, src = store.con.execute("select call_id, id_source from model_calls").fetchone()
    assert call_id and call_id.startswith("assay-")
    assert src == "minted"
    assert store.con.execute(
        "select count(*) from model_decisions where call_id is null or call_id = ''"
    ).fetchone()[0] == 0
    assert cost.ledger(store)["id_source"] == {"minted": 1}


def test_since_reads_decided_at(store):
    """`--since` filters on the clock the store already had. There is no second timestamp."""
    _ask(store, FakeClient(usage={"input_tokens": 1000}, call_id="c1"))
    assert cost.ledger(store, since="1999-01-01")["calls"] == 1
    assert cost.ledger(store, since="2999-01-01")["calls"] == 0


def test_a_batch_spanning_families_is_not_attributed_to_one_of_them(store):
    """A call's cost is one number and cannot be filed under two families. `(mixed)` is the
    honest answer, said out loud, rather than a split nobody measured."""
    decide(store, FakeClient(usage={"input_tokens": 900}, call_id="mixed-1"),
           {"sql": "select 1"},
           {"sentence__0": {"type": "noul", "instructions": {"question": "?"}},
            "desc": {"type": "noul", "instructions": {"question": "?"}}},
           decision_key="model.p.stg_bad_notnull", prompt_version="v1", caller="assay.claims")
    fams = [row[0] for row in cost.ledger(store)["by_family"]]
    assert fams == ["(mixed)"], fams
