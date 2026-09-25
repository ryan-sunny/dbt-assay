"""A day with nothing to ask must say so fast. (sunny-data: `ask` 3m31s and `verify` 3m13s to send
nothing, on a store that grows every day.)

Each cache lookup was its own query over every answer the store had ever held, two per subject;
now the latest answers are read once per connection and `decide` adds what it writes. `verify`
built a fresh state context per claim, re-reading all stored claims each time.
"""
from fakejev import FakeJev

from dbt_assay import contracts, jev
from dbt_assay.states import Recipe
from dbt_assay.store import Store

Q = {"desc": {"type": "choice", "criteria": {"agrees": {"what": "x"}, "contradicts": {"what": "y"}}}}


def test_one_read_then_lookups_and_a_written_answer_is_a_hit_at_once(tmp_path, monkeypatch):
    s = Store(str(tmp_path / "s.duckdb"))
    client = FakeJev()
    r = Recipe("subject", "model.p.a", {"uid": "model.p.a"}, {"model": "a", "sql": "select 1"})
    got = jev.decide(s, client, r, Q, prompt_version="desc.v1")
    assert client.calls == 1 and not got["desc"]["cached"]
    # the answer it just wrote is a hit, from the index, with no query
    queries = []
    real = s.con

    class Spy:
        def execute(self, q, *a):
            queries.append(q)
            return real.execute(q, *a)

        def __getattr__(self, name):
            return getattr(real, name)
    monkeypatch.setattr(s, "con", Spy())
    again = jev.decide(s, client, r, Q, prompt_version="desc.v1")
    assert client.calls == 1 and again["desc"]["cached"]
    assert not any("from model_decisions" in q for q in queries), queries
    # a changed state is a miss, as before
    r2 = Recipe("subject", "model.p.a", {"uid": "model.p.a"}, {"model": "a", "sql": "select 2"})
    jev.decide(s, client, r2, Q, prompt_version="desc.v1")
    assert client.calls == 2


def test_the_index_reads_what_another_connection_wrote_before_it(tmp_path):
    path = str(tmp_path / "s.duckdb")
    s = Store(path)
    r = Recipe("subject", "model.p.a", {"uid": "model.p.a"}, {"model": "a"})
    jev.decide(s, FakeJev(), r, Q, prompt_version="desc.v1")
    s.close()
    s2 = Store(path)
    hits, ask = jev.cache_split(s2, r, Q, "desc.v1")
    assert hits and not ask


def test_an_unknown_question_id_still_fails_after_others_were_checked():
    contracts.check_question_ids(["desc"])
    try:
        contracts.check_question_ids(["desc", "nosuchprefix__1"])
    except ValueError as e:
        assert "nosuchprefix__1" in str(e)
    else:
        raise AssertionError("an unknown prefix passed")


def test_a_locked_warehouse_says_so_not_to_pass_the_flags(monkeypatch):
    from types import SimpleNamespace

    import pytest

    from dbt_assay import probe
    monkeypatch.setattr(probe, "_REACHED", {})
    err = ('IO Error: Could not set lock on file "warehouse/sunny.duckdb": Conflicting lock is '
           'held in /usr/bin/python3 (PID 41)')
    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="", stderr=err))
    with pytest.raises(probe.WarehouseUnreachable) as e:
        probe._reach("transform", None, "dbt")
    msg = str(e.value)
    assert "locked by another process" in msg and "Pass --project-dir" not in msg, msg
