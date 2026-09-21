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


def shipped_action(check: str) -> str:
    """The action `assay init` would write for this check today, or "" if it writes none.

    *** THE SHIPPED FILE IS NOT A FALLBACK, AND IT SAID IT WAS. ***
    `DEFAULT_YML` is only ever WRITTEN, by `assay init`. It is never parsed, so a check added
    after somebody ran `init` never gets the action it ships with -- it falls back to severity
    instead. That is the right behavior: a release must not turn a green build red because it
    added a `fail` check. But the file's first line claimed the opposite for every release up to
    0.30.0, which is documentation promising behavior the code does not have, in the one place a
    person reads to find out what the tool does.

    So the opinion is READABLE now, without being applied. `assay config` shows what assay would
    suggest next to what is actually happening, and adopting it stays a deliberate edit.
    """
    # *** PARSE IT, DO NOT PATTERN-MATCH IT. ***
    # The first version regexed for `{action: x}` and silently missed every check written in the
    # block form -- `duckdb_full_match` among them -- and every one configured with `act:`
    # thresholds instead of a flat action. A reader that handles one spelling of a fact is the
    # defect this whole file is about, and it appeared inside the fix for it.
    import yaml
    try:
        q = (yaml.safe_load(DEFAULT_YML) or {}).get("questions") or {}
    except Exception:                                            # noqa: BLE001
        return ""
    cfg = q.get(check)
    if not isinstance(cfg, dict):
        return ""
    if cfg.get("action"):
        return str(cfg["action"])
    if cfg.get("act"):
        # A thresholded opinion is still an opinion. Name the strongest action it can reach.
        order = ["annotate", "queue", "fail"]
        got = [a for a in order if a in cfg["act"]]
        return f"{got[-1]} above a threshold" if got else ""
    return ""


def shipped_block(check: str) -> str:
    """The `questions:` entry `assay init` ships for this check, as YAML text, or "".

    *** `shipped_action` RETURNS A TOKEN SOMETIMES AND A SENTENCE OTHER TIMES. ***
    A flat opinion comes back as `queue`, which is a value `action:` accepts. A thresholded one
    comes back as `queue above a threshold`, which is English -- correct for a console line, and
    it was being pasted straight into a draft `action:` field by `assay suggest`. That block does
    not load: `ThresholdError: unknown action`. One field carrying two kinds of thing is the
    defect this file exists to find, and it was in this file.

    So a caller that needs YAML asks for YAML and gets the real shipped entry, thresholds and
    all, rather than reconstructing it from a summary of itself.
    """
    import yaml
    try:
        q = (yaml.safe_load(DEFAULT_YML) or {}).get("questions") or {}
    except Exception:                                            # noqa: BLE001
        return ""
    cfg = q.get(check)
    if not isinstance(cfg, dict) or not cfg:
        return ""
    return yaml.safe_dump({check: cfg}, sort_keys=True, default_flow_style=False).rstrip()


def known_checks() -> set:
    """Every `check` a Finding can carry, read from the source that constructs them.

    Parsed rather than listed, because a list is a second copy of a fact and the second copy is
    what drifts. A reader that matches nothing would pass this wrongly, so it asserts a floor.
    """
    import ast
    from pathlib import Path as _Path

    # *** IT WALKS THE PACKAGE. A LIST OF MODULES IS A SECOND COPY OF A FACT. ***
    # This named five modules by hand, with a comment explaining that `practices` and
    # `checks.sources` had been missing from an earlier hand list and five checks had come back
    # UNKNOWN because of it. Then `probe` started constructing findings and four more went
    # unknown for exactly the same reason, one layer on.
    #
    # The TEST for this already walked the package to catch it. Now the reader does too, so
    # adding a check to any module IS registering it, and the list cannot drift because there
    # is no list.
    out = set()
    root = _Path(__file__).parent
    for f in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(f.read_text())
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Finding"):
                continue
            c = {k.arg: k.value for k in node.keywords}.get("check")
            if isinstance(c, ast.Constant):
                out.add(c.value)
    if len(out) < 10:                       # a scanner that finds nothing must not report "clean"
        raise RuntimeError(f"only found {len(out)} check names; the reader is broken")
    return out


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
                   min_adjudications: int, agreement: float | None = None,
                   min_agreement: float = 0.0) -> str | None:
        """The strongest action this answer earns. `fail` is refused while unmeasured.

        An answer of None is a structural finding: exact, no probability, so the configured plain
        `action` stands and no calibration is waited for.

        *** COUNT WAS THE ONLY FLOOR, AND A COUNT OF WRONG ANSWERS IS STILL TWENTY. ***
        `min_adjudications` asks whether enough people have looked. It does not ask whether they
        AGREED. A question twenty-five people read and disagreed with twelve times has satisfied
        the gate and earned nothing, and it can fail somebody's build.

        `min_agreement` is the other half. It is measured on the version SHIPPING NOW, because a
        verdict about v1 says nothing about v4, and an unmeasured rate arrives as None and cannot
        refuse anything -- an absent measurement must never read as a failing one.
        """
        if answer is None:
            return self.action
        for act in ("fail", "queue", "annotate"):
            t = self.act.get(act)
            if t is None or not t.holds(answer):
                continue
            if act == "fail":
                if adjudications < min_adjudications:
                    return "queue"
                if min_agreement and agreement is not None and agreement < min_agreement:
                    # Same sentence as the count floor: not trusted enough to fail. It is queued
                    # rather than dropped, because a question people argue with is exactly the one
                    # somebody should be looking at.
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
    unknown_questions: list = field(default_factory=list)   # keys that configure nothing
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
    # *** DEFAULT OFF, BECAUSE A FLOOR SET BEFORE ANYTHING WAS MEASURED IS A GUESS. ***
    # `assay effectiveness` prints the real rates. Pick a number from those, not from this file.
    min_agreement: float = 0.0
    # How much of a parent a hop may LOSE before it is reported, when `--verify` counted it.
    # 0.8 catches all three enrichment gaps on the warehouse this was built against (82%, 82%,
    # 94% lost); 0.9 catches one of three. It is a number somebody acts on, so it is theirs.
    row_loss_threshold: float = 0.8
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
        gating = data.get("gating") or {}
        cfg.min_adjudications = int(gating.get("min_adjudications", 20))
        cfg.min_agreement = float(gating.get("min_agreement", 0.0))
        comp = data.get("completeness") or {}
        cfg.row_loss_threshold = float(comp.get("row_loss_threshold", 0.8))
        if not 0.0 < cfg.row_loss_threshold < 1.0:
            raise ValueError(f"completeness.row_loss_threshold must be between 0 and 1 "
                             f"(exclusive), got {cfg.row_loss_threshold}. It is the share of the "
                             f"parent LOST, so 0.8 means 'kept less than a fifth'.")
        if not 0.0 <= cfg.min_agreement <= 1.0:
            raise ValueError(f"gating.min_agreement must be between 0 and 1, "
                             f"got {cfg.min_agreement}. It is a rate, not a percentage.")
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

        # *** A CONFIG KEY THAT MATCHES NO CHECK CONFIGURES NOTHING, SILENTLY. ***
        # `questions:` is keyed by the CHECK a finding carries, and the verdict floor is counted
        # by the QUESTION FAMILY a finding rests on. Two namespaces with confusable names, and
        # assay's own shipped default used a family name where a check belongs -- so the example
        # everyone copies had never done anything. Collected rather than raised: a renamed check
        # must not break someone's build on upgrade, but it must not pass unmentioned either.
        checks = known_checks()
        cfg.unknown_questions = sorted(set(cfg.questions) - checks)

        for model, meta in (data.get("waivers") or {}).items():
            out = []
            for w in meta or []:
                if not w.get("reason"):
                    raise ThresholdError(
                        f"waiver on `{model}` for `{w.get('question')}` has no reason. A waiver "
                        f"without one is where findings go to die.")
                out.append(Waiver(w["question"], w["reason"], w.get("until")))
            cfg.waivers[model] = out
        # A waiver naming no real check silences nothing, and a waiver is exactly the place
        # someone believes a finding has been dealt with. assay's own example waived
        # `figure_is_plausible`, which is neither a check nor a question.
        cfg.unknown_questions = sorted(set(cfg.unknown_questions) | {
            w.question for ws in cfg.waivers.values() for w in ws
            if w.question and w.question not in checks})
        return cfg

    def for_question(self, name: str) -> QuestionConfig:
        return self.questions.get(name) or QuestionConfig(name=name)

    def unconfigured(self, firing: set) -> list:
        """Checks that FIRED and this config does not name. The other direction of the same set.

        *** `unknown_questions` CATCHES A CONFIG NAMING A CHECK THAT DOES NOT EXIST. ***
        Nothing caught a check that exists and the config does not name, which is the direction
        that grows on its own: every release that adds a check silently widens the gap, and this
        file's own opening line is the promise it breaks --

            "A check absent from this file is a check nobody can find to tune, and 'it reported
             nothing' and 'it is not configured' read identically from the outside."

        Reported from the field on a warehouse where FOUR checks were firing unnamed, including
        the largest family at 115 findings. Each returns the SHIPPED opinion alongside, so the
        difference between what assay would suggest and what is actually happening is visible
        rather than inferred.
        """
        return [(c, shipped_action(c)) for c in sorted(firing - set(self.questions))]

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
# assay configuration. Every field is optional.
#
# *** WITHOUT THIS FILE, A CHECK FALLS BACK TO ITS SEVERITY, NOT TO WHAT IS WRITTEN BELOW. ***
# This line used to say "the defaults below are what runs without this file", and that was false:
# nothing parses this template, `assay init` only WRITES it. An unconfigured check gets `queue` if
# its severity is high and `annotate` otherwise, and it can never `fail` -- which is deliberate,
# because a release that adds a gating check must not turn a green build red on upgrade.
#
# So the actions below are an OPINION you adopt by editing this file, not a default you inherit.
# `assay check` names every check that is firing and is not in here, with the action assay would
# suggest for it, because "it reported nothing" and "it is not configured" read identically from
# the outside.

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

  # And the other half: how often those people had to AGREE. A count of wrong answers is still a
  # count, so a question twenty-five people read and disagreed with twelve times satisfies the
  # line above and has earned nothing. Measured only on verdicts given against the version of the
  # question shipping now, and `unclear` is never in the denominator.
  #
  # 0 is off. Run `assay effectiveness` and pick a number from the rates you actually have.
  min_agreement: 0.0

completeness:
  # The share of a parent a hop may LOSE before it is reported. Counted only under `--verify`,
  # and only for a child that declares no filter, no group by and no union -- a hop that drops
  # rows on purpose is not a finding. 0.8 means "kept less than a fifth".
  row_loss_threshold: 0.8

questions:
  # An EXACT check has no probability to threshold, so it takes a plain action and may gate
  # immediately: a parser decided it, and there is no error rate to measure first.
  duckdb_full_match:
    action: queue

  # *** COMPLETENESS: COVERAGE OF WHAT THIS PROJECT ITSELF DECLARES. ***
  # Named here so `assay config` shows them and you can see what to change. All annotate: they
  # are true and cheap, and none is worth failing a build over until somebody has read a few and
  # said which ones matter here.
  source_reaches_nothing:      {action: annotate}
  seed_reaches_nothing:        {action: annotate}
  # *** WHAT CHANGED, WHICH NEEDS TWO `assay probe` RUNS TO EXIST AT ALL. ***
  # A key that held last week and does not now is the failure that corrupts a warehouse: every
  # count past the join inflates, nothing errors, and the tests pass because they were written
  # while it was true. Counted, not judged, so it may gate immediately.
  key_stopped_holding:         {action: fail}
  key_column_started_mattering: {action: queue}
  key_started_holding:         {action: annotate}
  key_column_stopped_mattering: {action: annotate}
  source_only_a_test_reads:    {action: annotate}
  source_freshness_undeclared: {action: annotate}
  source_freshness_stale:      {action: annotate}
  # Needs `--verify`: it counts parent and child rows through your own dbt. An uncounted hop
  # produces nothing at all, which is correct -- an absent measurement is not a pass.
  hop_drops_most_rows:         {action: annotate}
  # `when.select` scopes a question. assay errors on syntax it does not understand rather than
  # silently matching everything.
  #  when:
  #    select: "path:models/water+"

  # Keyed by the CHECK a finding carries, which is NOT the question family a verdict is filed
  # under. `assay check --json` prints every check name; `assay config` warns about a key here
  # that matches none of them. This example said `column_is_part_of_the_key` for months, which is
  # a question family, so it configured nothing at all.
  grain_unresolved:
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
#    - question: description_contradicts_the_code   # a CHECK name, as in `questions:` above
#      reason: "the summary is deliberately short; the file comment carries the detail"
#      until: 2027-01-01
"""
