"""A warehouse's `inf` reaches Jev as null, and an encoding failure is never a provider failure.

*** "jev failed after 3 attempts" FOR A FLOAT IN THE USER'S OWN DATA. ***
Reported from the field (25.17): `feeds` sampled an `inf`, the body could not be encoded inside
the retry loop, and after three attempts and nine seconds the error blamed the provider.
"""
from __future__ import annotations

import json

import httpx
import pytest

from dbt_assay import jev


class _Resp:
    status_code = 200

    def json(self):
        return {"answers": {"q": {"type": "noul", "noul": 0.5}},
                "usage": {"input_tokens": 10}, "id": "x"}


def _client():
    c = jev.Client(provider="openrouter", model="m", max_spend_usd=1.0)
    c._resolved = ("fake", {"url": "http://fake", "model": "m"}, "key")
    return c


def test_non_finite_values_are_sent_as_null_and_counted(monkeypatch):
    sent = []
    monkeypatch.setattr(httpx, "post", lambda url, content=None, **k: (sent.append(content),
                                                                         _Resp())[1])
    jev.NONFINITE.clear()
    _client().ask({"rows": [{"x": float("inf")}, {"x": float("nan")}, {"x": 1.5}]},
                  {"q": {"type": "noul"}}, caller="assay.feeds")
    body = json.loads(sent[0])
    assert body["state"]["rows"] == [{"x": None}, {"x": None}, {"x": 1.5}]
    assert jev.NONFINITE == [("assay.feeds", 2)]
    jev.NONFINITE.clear()


def test_an_encoding_failure_is_raised_once_and_names_assay(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(jev.time, "sleep", lambda s: pytest.fail("an encoding error was retried"))
    with pytest.raises(RuntimeError) as e:
        # a NaN where the coercion does not reach: the question itself
        _client().ask({}, {"q": {"type": "noul", "weight": float("nan")}})
    assert not calls, "nothing should have been sent"
    assert "not in your key or your network" in str(e.value)
    assert "attempts" not in str(e.value)
