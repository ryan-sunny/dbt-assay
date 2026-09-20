"""`audit.yml`: what runs where, and what an answer is allowed to DO.

*** A THRESHOLD IS AN EXPRESSION, NEVER A BARE NUMBER. ***
`p > 0.85` and `p < 0.30` read differently at a glance, which is how a polarity bug stops shipping.
Most questions are phrased as the DEFECT so high means the defect is present, but some are phrased
positively (`belongs_to_this_entity`) because inverting them produces a double negative -- and those
gate on a LOW value. Writing the comparison at the call site makes the direction impossible to
misread, and forces the author to name WHICH quantity is being thresholded: a noul's probability and
a choice's confidence are different things and must never share a number by accident.

*** A THRESHOLD IS PER ACTION, NEVER PER QUESTION. ***
The same answer annotates at one level, opens a review item at another, and fails a build at a
third, because the cost of being wrong differs at each. One number per question cannot express that.

*** NOTHING GATES UNTIL IT HAS BEEN MEASURED. ***
`fail` is refused for a question with fewer than `min_adjudications` recorded human verdicts. Not a
warning in the docs -- an actual refusal. Otherwise someone sets 0.7 on day one because it sounds
right, it fails a build on a false positive in week two, and the whole tool gets switched off.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ACTIONS = ("annotate", "queue", "fail")
DEFAULT_FILENAMES = ("audit.yml", "audit.yaml", ".assay.yml")


class ThresholdError(ValueError):
    pass


_ALLOWED_NAMES = {"p", "confidence", "answer", "score", "true", "false", "None"}


def _validate(node: ast.AST) -> None:
    """Only comparisons and boolean combinations over the answer's own fields."""
    if isinstance(node, ast.Expression):
        return _validate(node.body)
    if isinstance(node, ast.BoolOp):
        for v in node.values:
            _validate(v)
        return
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.USub)):
        return _validate(node.operand)
    if isinstance(node, ast.Compare):
        _validate(node.left)
        for c in node.comparators:
            _validate(c)
        return
    if isinstance(node, ast.Name):
        if node.id not in _ALLOWED_NAMES:
            raise ThresholdError(
                f"`{node.id}` is not available in a threshold. Use one of: "
                f"p, confidence, answer, score.")
        return
    if isinstance(node, ast.Constant):
        return
    raise ThresholdError(f"unsupported expression in a threshold: {type(node).__name__}")


@dataclass(frozen=True)
class Threshold:
    expr: str

    def __post_init__(self) -> None:
        try:
            tree = ast.parse(self.expr, mode="eval")
        except SyntaxError as e:
            raise ThresholdError(f"cannot parse threshold {self.expr!r}: {e}") from e
        _validate(tree)

    def holds(self, answer: dict) -> bool:
        """`answer` is a stored judgment: {kind, answer, confidence, probabilities}."""
        kind = answer.get("kind")
        env = {
            # A noul has NO separate confidence. Exposing one would invite a gate to read
            # "0.91 confident" off a question that answered "91% likely yes".
            "p": float(answer["answer"]) if kind == "noul" else None,
            "confidence": answer.get("confidence") if kind in ("choice", "score") else None,
            "answer": answer.get("answer"),
            "score": float(answer["answer"]) if kind == "score" else None,
        }
        try:
            return bool(eval(compile(ast.parse(self.expr, mode="eval"), "<threshold>", "eval"),
                             {"__builtins__": {}}, env))
        except TypeError:
            # `p > 0.8` against a choice answer leaves p as None. That is a mis-wired gate, not a
            # False: reporting it as "did not fire" would hide the mistake forever.
            raise ThresholdError(
                f"threshold {self.expr!r} does not apply to a {kind} answer. A noul has `p`; a "
                f"choice has `answer` and `confidence`; a score has `score` and `confidence`."
            ) from None


@dataclass
class QuestionConfig:
    name: str
    enabled: bool = True
    select: str | None = None            # dbt selector syntax, applied to the subject
    act: dict = field(default_factory=dict)      # action -> Threshold, for JUDGED findings
    # *** AN EXACT CHECK MAY GATE WITHOUT ADJUDICATIONS. A JUDGED ONE MAY NOT. ***
    # A parser decided it; there is no probability to threshold and no calibration to wait for.
    # `min_adjudications` exists because a judgment's error rate is unknown until measured, which
    # is simply not true of "this test cannot fail".
    action: str | None = None            # annotate | queue | fail, for STRUCTURAL findings

    def action_for(self, answer: dict | None, adjudications: int,
                   min_adjudications: int) -> str | None:
        """The strongest action this answer earns. `fail` is refused while unmeasured.

        An answer of None is a structural finding: exact, no probability, so the configured plain
        `action` stands and no calibration is waited for.
        """
        if answer is None:
            return self.action
        for act in ("fail", "queue", "annotate"):
            t = self.act.get(act)
            if t is None or not t.holds(answer):
                continue
            if act == "fail" and adjudications < min_adjudications:
                return "queue"
            return act
        return None


@dataclass
class Waiver:
    question: str
    reason: str
    until: str | None = None


@dataclass
class Config:
    questions: dict = field(default_factory=dict)
    waivers: dict = field(default_factory=dict)     # model name -> [Waiver]
    vocab: dict = field(default_factory=dict)
    # Per-mart options for the row-adjudication family. THE OPTIONS ARE THE DOMAIN KNOWLEDGE and
    # there is one set per mart; this is the part of the file worth maintaining.
    explanations: dict = field(default_factory=dict)
    # Per-check overrides for the standard-practice split: enforce | recommend | adjudicate | off.
    practices: dict = field(default_factory=dict)
    provider: str = "auto"
    model: str = "jev-latest"
    max_spend_usd: float = 1.0
    min_adjudications: int = 20
    path: Path | None = None

    @classmethod
    def load(cls, start: str | Path = ".") -> Config:
        start = Path(start)
        for name in DEFAULT_FILENAMES:
            p = start / name
            if p.exists():
                return cls.from_dict(yaml.safe_load(p.read_text()) or {}, p)
        return cls()

    @classmethod
    def from_dict(cls, data: dict, path: Path | None = None) -> Config:
        cfg = cls(path=path)
        j = data.get("jev") or {}
        cfg.provider = j.get("provider", "auto")
        cfg.model = j.get("model", "jev-latest")
        cfg.max_spend_usd = float(j.get("max_spend_usd", 1.0))
        cfg.min_adjudications = int((data.get("gating") or {}).get("min_adjudications", 20))
        cfg.vocab = data.get("vocab") or {}
        cfg.explanations = data.get("explanations") or {}
        cfg.practices = data.get("practices") or {}

        for name, q in (data.get("questions") or {}).items():
            q = q or {}
            act = {}
            for a, expr in (q.get("act") or {}).items():
                if a not in ACTIONS:
                    raise ThresholdError(
                        f"question `{name}`: unknown action `{a}`. Use one of {ACTIONS}.")
                act[a] = Threshold(str(expr))
            action = q.get("action")
            if action and action not in ACTIONS:
                raise ThresholdError(
                    f"question `{name}`: unknown action `{action}`. Use one of {ACTIONS}.")
            sel = (q.get("when") or {}).get("select")
            if sel:
                from .selector import validate as _validate
                _validate(sel)
            cfg.questions[name] = QuestionConfig(
                name=name, enabled=q.get("enabled", True),
                select=sel, act=act, action=action)

        for model, meta in (data.get("waivers") or {}).items():
            out = []
            for w in meta or []:
                if not w.get("reason"):
                    raise ThresholdError(
                        f"waiver on `{model}` for `{w.get('question')}` has no reason. A waiver "
                        f"without one is where findings go to die.")
                out.append(Waiver(w["question"], w["reason"], w.get("until")))
            cfg.waivers[model] = out
        return cfg

    def for_question(self, name: str) -> QuestionConfig:
        return self.questions.get(name) or QuestionConfig(name=name)

    def waived(self, model: str, question: str) -> Waiver | None:
        from datetime import datetime, timezone
        for w in self.waivers.get(model, []):
            if w.question != question:
                continue
            today = datetime.now(timezone.utc).date().isoformat()
            if w.until and str(w.until) < today:
                continue                        # an expired waiver is not a waiver
            return w
        return None


DEFAULT_YML = """\
# assay configuration. Every field is optional; the defaults below are what runs without this file.

jev:
  # THE KEY IS NEVER IN THIS FILE, because this file belongs in git. assay reads
  # TYPESAFE_API_KEY or OPENROUTER_API_KEY from the environment, or from a .env here or in any
  # parent directory. An exported variable always beats the file.
  #   `assay config`          shows what was resolved and where the key came from
  #   `assay config --check`  makes one real call to prove it works (about $0.00001)
  #
  # auto prefers TypeSafe direct and falls back to OpenRouter, so direct is used the moment
  # a TYPESAFE_API_KEY is present. Pin one explicitly with `typesafe` or `openrouter`.
  provider: auto
  model: jev-latest
  max_spend_usd: 1.0      # hard cap per invocation. ~80 full sweeps of a 300-model project.

gating:
  # `fail` is refused for a question with fewer recorded human verdicts than this, and downgraded
  # to `queue`. A threshold set before anything was measured is a guess wearing a number.
  min_adjudications: 20

questions:
  # An EXACT check has no probability to threshold, so it takes a plain action and may gate
  # immediately: a parser decided it, and there is no error rate to measure first.
  duckdb_full_match:
    action: queue
  # `when.select` scopes a question. assay errors on syntax it does not understand rather than
  # silently matching everything.
  #  when:
  #    select: "path:models/water+"

  column_is_part_of_the_key:
    # A threshold is an EXPRESSION, so its direction is readable at a glance, and per ACTION,
    # because the cost of being wrong differs between annotating and failing a build.
    act:
      annotate: "p > 0.50"
      queue:    "p > 0.75"

# practices: override how a standard dbt-project-evaluator check is treated.
#   enforce (exact, may gate) | recommend (informational) | adjudicate (ask) | off
practices: {}
#  fct_model_fanout: recommend

# explanations: options for the row-adjudication family, per model. The generic set is always
# available; these are added to it, and they are where the domain knowledge lives.
explanations: {}
#  water_rights:
#    conditional_right: >-
#      a claim on water not yet diverted, so its structure legitimately does not exist yet

# vocab: what the words mean here. Injected into state for EVERY question, which is why it improves
# answers to questions you never wrote.
vocab: {}
#  division:
#    means: "a Colorado water court region, 1 through 7"
#    implies: "a case number is unique only within one division"

# waivers: reason required, expiry optional but recommended.
waivers: {}
#  int_water_conditional:
#    - question: figure_is_plausible
#      reason: "conditional rights have no built structure by definition"
#      until: 2027-01-01
"""
