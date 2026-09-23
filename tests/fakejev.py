"""A judged client for CLI tests: answers every question it is sent, with no network.

*** THE CLI PATHS WERE ONLY EVER TESTED BELOW `decide`. ***
`FakeClient` in test_cost exercises the cache and the ledger, and nothing drove a judged COMMAND
end to end -- which is how `claims --extract` shipped a crash in its reporting tail: every piece
below the print was tested and the print was not. Patch `dbt_assay.cli.Client` with this and the
command runs exactly as it does against the provider.
"""
from __future__ import annotations

from typing import ClassVar


class FakeJev:
    """`pick(question_id, question) -> option` chooses; the default takes the first option.

    `confidence` is what every choice reports, and the probabilities put that mass on the pick
    and spread the rest evenly, so a test can put an answer on either side of a floor.
    """

    instances: ClassVar[list] = []

    def __init__(self, *_a, pick=None, confidence: float = 0.9, tokens: int = 100, **_k):
        self.model = "fake/jev-1"
        self.available = True
        self.calls = 0
        self.input_tokens = 0
        self.spent_usd = 0.0
        self.provider_rejected = ""
        self.pick = pick
        self.confidence = confidence
        self.tokens = tokens
        self.sent: list = []
        FakeJev.instances.append(self)

    def _conn(self):
        return "fake", {"model": self.model}, ""

    def ask(self, state, questions: dict, *, caller: str = "assay") -> dict:
        self.calls += 1
        self.input_tokens += self.tokens
        self.sent.append((state, questions, caller))
        answers = {}
        for qid, q in questions.items():
            kind = q.get("type")
            if kind == "noul":
                answers[qid] = {"type": "noul", "noul": self.confidence}
                continue
            opts = list((q.get("criteria") or {}).keys()) or ["yes"]
            chosen = self.pick(qid, q) if self.pick else opts[0]
            rest = [o for o in opts if o != chosen]
            share = (1 - self.confidence) / len(rest) if rest else 0.0
            answers[qid] = {"type": kind or "choice", "choice": chosen,
                            "confidence": self.confidence,
                            "probabilities": {chosen: self.confidence,
                                              **{o: share for o in rest}}}
        return {"answers": answers, "usage": {"input_tokens": self.tokens},
                "id": f"fake-{self.calls}-{len(self.sent)}", "model": self.model}


def install(monkeypatch, **kw) -> type:
    """Patch every place a command builds a client. Returns the class so a test can read
    `FakeJev.instances[-1].sent`."""
    FakeJev.instances = []

    def make(*a, **k):
        return FakeJev(*a, **{**k, **kw})
    from dbt_assay import cli
    monkeypatch.setattr(cli, "Client", make)
    return FakeJev
