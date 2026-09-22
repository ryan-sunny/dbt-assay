"""*** HALF A REAL STORE COULD NOT SAY WHAT A CALL WAS. ***

`call_id` was whatever the provider returned, and one provider returned nothing for a whole day:
9,762 of 19,707 decisions on the field store carry an empty one. Those rows describe real calls --
a batch of eight answers about one state, or eight separate calls -- and nothing on the row said
which, so every cost total over them was a guess.

The reconstruction is not a guess either. Rows written by one `decide()` share
(decision_key, state_hash, prompt_version, model_version, input_tokens) and differ only by
question, because that is exactly what the writer does. On the half of the field store that HAS
provider ids, that tuple returns the same 4,946 calls and the same 14,567,698 tokens as the ids
themselves. So it is CHECKED against the provider before it is trusted, and refused when they
disagree -- a wrong call boundary is a wrong total presented as measured, which is worse than an
absent one.
"""
from __future__ import annotations

from dbt_assay import cost
from dbt_assay.store import CALLS_RECONSTRUCTED, Store

COLS = ("decision_key, question, kind, answer, probabilities, state_hash, prompt_version, "
        "model_version, call_id, caller, input_tokens, decided_at")


def _old_rows(path, rows):
    """A store in the shape assay wrote before `model_calls` existed."""
    s = Store(str(path))
    s.con.execute("delete from model_calls")
    s.con.executemany(
        f"insert or replace into model_decisions ({COLS}) "
        f"values (?,?,'noul','0.9','{{}}',?,?,?,?,?,?, current_timestamp)", rows)
    s.close()


def test_a_batched_call_the_provider_named_becomes_one_row(tmp_path):
    p = tmp_path / "s.duckdb"
    _old_rows(p, [["model.p.a", f"sentence__{i}", "h1", "v1", "m1", "gen-1", "assay.claims", 1000]
                  for i in range(8)])
    s = Store(str(p))
    try:
        got = s.con.execute(
            "select call_id, id_source, input_tokens from model_calls").fetchall()
        assert got == [("gen-1", "provider", 1000)]
        led = cost.ledger(s)
        assert led["calls"] == 1 and led["input_tokens"] == 1000
    finally:
        s.close()


def test_rows_the_provider_never_named_are_reconstructed_into_their_calls(tmp_path):
    """Two batches of four, no ids at all. The tuple the writer uses puts them back."""
    p = tmp_path / "s.duckdb"
    rows = [["model.p.a", f"sentence__{i}", "h1", "v1", "m1", "", "assay.claims", 800]
            for i in range(4)]
    rows += [["model.p.b", f"sentence__{i}", "h2", "v1", "m1", "", "assay.claims", 600]
             for i in range(4)]
    _old_rows(p, rows)
    s = Store(str(p))
    try:
        got = s.con.execute(
            "select id_source, count(*), sum(input_tokens) from model_calls group by 1").fetchall()
        assert got == [("reconstructed", 2, 1400)]
        assert s.con.execute(
            "select count(*) from model_decisions where call_id = ''").fetchone()[0] == 0
        assert cost.ledger(s)["input_tokens"] == 1400
    finally:
        s.close()


def test_the_reconstruction_is_idempotent(tmp_path):
    """A second open must not mint a second name for the same call."""
    p = tmp_path / "s.duckdb"
    _old_rows(p, [["model.p.a", f"sentence__{i}", "h1", "v1", "m1", "", "assay.claims", 800]
                  for i in range(4)])
    first = Store(str(p))
    ids = first.con.execute("select call_id from model_calls").fetchall()
    first.close()
    again = Store(str(p))
    try:
        assert again.con.execute("select call_id from model_calls").fetchall() == ids
        assert again.con.execute("select count(*) from model_calls").fetchone()[0] == 1
    finally:
        again.close()


def test_it_refuses_when_the_proxy_disagrees_with_the_provider(tmp_path):
    """*** A GUARD NOBODY HAS WATCHED FAIL IS A GUARD NOBODY HAS TESTED. ***

    Two rows the provider gave DIFFERENT ids sit under one proxy tuple, so the tuple is not the
    call boundary on this store. The blind rows are left alone and counted rather than grouped on
    a rule that has just been shown wrong here.
    """
    p = tmp_path / "s.duckdb"
    rows = [["model.p.a", "sentence__0", "h1", "v1", "m1", "gen-1", "assay.claims", 500],
            ["model.p.a", "sentence__1", "h1", "v1", "m1", "gen-2", "assay.claims", 500],
            ["model.p.b", "sentence__0", "h2", "v1", "m1", "", "assay.claims", 700]]
    _old_rows(p, rows)
    before = len(CALLS_RECONSTRUCTED)
    s = Store(str(p))
    try:
        assert ("refused", 1) in CALLS_RECONSTRUCTED[before:]
        assert s.con.execute(
            "select count(*) from model_decisions where call_id = ''").fetchone()[0] == 1
        # the named ones are still recorded; only the guess was refused
        assert s.con.execute("select count(*) from model_calls").fetchone()[0] == 0
    finally:
        s.close()


def test_zero_tokens_do_not_become_a_free_call(tmp_path):
    """The old writer spelled "the provider returned no usage" as 0, because it did `or 0`. It is
    not a measurement of a free call and does not become one by being copied across."""
    p = tmp_path / "s.duckdb"
    _old_rows(p, [["model.p.a", "sentence__0", "h1", "v1", "m1", "gen-1", "assay.claims", 0]])
    s = Store(str(p))
    try:
        tok, usd = s.con.execute("select input_tokens, usd from model_calls").fetchone()
        assert tok is None and usd is None
        assert cost.ledger(s)["calls_without_usage"] == 1
    finally:
        s.close()
