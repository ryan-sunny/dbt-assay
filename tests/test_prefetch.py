"""Judged requests several at once. (sunny-data box: `ask` sent 608 requests in 488s, one at a
time.) The answers must be exactly what the one-at-a-time loop writes, the cap must hold across
workers, answers are written in chunks as they arrive, and a 429 waits instead of failing."""
import threading
import time
from types import SimpleNamespace

import pytest
from fakejev import FakeJev

from dbt_assay import jev
from dbt_assay.states import Recipe
from dbt_assay.store import Store

Q = {"desc": {"type": "choice", "criteria": {"agrees": {"what": "x"}, "contradicts": {"what": "y"}}}}


class SlowJev(FakeJev):
    """FakeJev with a round trip, safe to call from several threads."""

    def __init__(self, *a, delay: float = 0.05, **k):
        super().__init__(*a, **k)
        self.delay, self._l, self.in_flight, self.most = delay, threading.Lock(), 0, 0

    def ask(self, state, questions, *, caller="assay"):
        with self._l:
            self.in_flight += 1
            self.most = max(self.most, self.in_flight)
        time.sleep(self.delay)
        with self._l:
            self.in_flight -= 1
            return super().ask(state, questions, caller=caller)


def _recipes(n):
    return [Recipe("subject", f"model.p.m{i}", {"uid": f"model.p.m{i}"},
                   {"model": f"m{i}", "sql": f"select {i}"}) for i in range(n)]


def _decisions(store):
    return sorted(store.con.execute(
        "select decision_key, question, kind, answer, confidence, probabilities, state_hash, "
        "prompt_version, caller, context from model_decisions").fetchall())


def test_prefetched_answers_are_the_ones_the_loop_would_have_written(tmp_path):
    recs = _recipes(16)
    one = Store(str(tmp_path / "one.duckdb"))
    seq = SlowJev(delay=0.01)
    for r in recs:
        jev.decide(one, seq, r, Q, prompt_version="d.v1", caller="assay.t",
                   contexts={"desc": r.key})
    many = Store(str(tmp_path / "many.duckdb"))
    par = SlowJev(delay=0.05)
    t0 = time.monotonic()
    out = jev.prefetch(many, par, [(r, Q, "d.v1", {"desc": r.key}) for r in recs],
                       caller="assay.t", workers=8)
    took = time.monotonic() - t0
    got = [jev.decide(many, par, r, Q, prompt_version="d.v1", caller="assay.t",
                      contexts={"desc": r.key}) for r in recs]
    assert out["sent"] == 16 and par.calls == 16          # the loop sent nothing more
    assert all(not g["desc"]["cached"] for g in got)       # served as this command's calls
    assert _decisions(one) == _decisions(many)
    assert par.most > 1 and took < 16 * 0.05 * 0.6        # they overlapped
    # a second run is all hits, and prefetch sends nothing
    assert jev.prefetch(many, par, [(r, Q, "d.v1", None) for r in recs], workers=8)["sent"] == 0
    one.close()
    many.close()


def test_answers_are_written_in_chunks_as_they_arrive(tmp_path, monkeypatch):
    monkeypatch.setattr(jev, "FLUSH_EVERY", 4)
    s = Store(str(tmp_path / "s.duckdb"))
    writes = []
    real = jev._write
    monkeypatch.setattr(jev, "_write", lambda store, w: (writes.append(len(w)), real(store, w)))
    jev.prefetch(s, SlowJev(delay=0.01), [(r, Q, "d.v1", None) for r in _recipes(12)],
                 workers=4)
    assert sum(writes) == 12 and max(writes) <= 4 + 3 and len([w for w in writes if w]) >= 3
    assert s.con.execute("select count(*) from model_decisions").fetchone()[0] == 12
    s.close()


def test_the_cap_holds_across_workers(monkeypatch):
    """Each request reserves its estimate before it goes: eight in flight cannot each pass a
    check the eight together would fail."""
    c = jev.Client(max_spend_usd=0.0)
    c._resolved = ("fake", {"model": "m", "url": "x"}, "k")
    state = {"x": "y" * 4000}
    est = len(jev.json.dumps({"model": "m", "state": state, "questions": Q})) / 4 \
        * jev.USD_PER_INPUT_TOKEN
    c.max_spend_usd = est * 3.5                           # room for three requests
    gate = threading.Event()

    def slow_send(spec, key, body, e, caller):
        gate.wait(2)
        with c._lock:
            c.spent_usd += e
        return {"answers": {}}
    monkeypatch.setattr(c, "_send", slow_send)
    ok, refused = [], []

    def one():
        try:
            c.ask(state, Q)
            ok.append(1)
        except jev.BudgetExceeded:
            refused.append(1)
    ts = [threading.Thread(target=one) for _ in range(8)]
    for t in ts:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in ts:
        t.join()
    assert len(ok) == 3 and len(refused) == 5
    assert c.reserved == pytest.approx(0.0)


def test_a_429_waits_for_retry_after_and_goes_again(monkeypatch):
    import httpx
    c = jev.Client(max_spend_usd=1.0)
    c._resolved = ("fake", {"model": "m", "url": "http://x"}, "k")
    replies = [SimpleNamespace(status_code=429, headers={"retry-after": "2"}, json=dict),
               SimpleNamespace(status_code=503, headers={}, json=dict),
               SimpleNamespace(status_code=200, headers={},
                               json=lambda: {"answers": {"desc": {}}, "usage": {}})]
    monkeypatch.setattr(httpx, "post", lambda *a, **k: replies.pop(0))
    slept = []
    monkeypatch.setattr(jev.time, "sleep", slept.append)
    assert "answers" in c.ask({"a": 1}, Q)
    assert slept[0] == 2.0 and len(slept) == 2 and c.calls == 1
