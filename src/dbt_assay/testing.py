"""Is this test's severity right, and what is this model exposed to that nothing tests?

*** COVERAGE IS CODE; SEVERITY IS A JUDGMENT. ***
assay already knows which defect classes a model's SQL exposes it to, because those are the
structural checks. Whether a test exists for each is a lookup. Neither needs a model.

What does need one is severity: a violation that silently corrupts a figure reaching a mart is not
the same event as one nobody would notice, and `warn` versus `error` is where that gets decided.
The team's current setting is a WEAK LABEL, so a disagreement is the finding rather than the
answer.

*** ORDERED LEVELS MEAN THIS IS A SCORE. ***
A choice over ordered options throws the ordering away and cannot express "slightly too strong".
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import QUESTIONS
from .jev import score

SEV_Q = QUESTIONS["severity_fit"]
SEV_VERSION = SEV_Q["prompt_version"]

# What a model's SQL exposes it to, and the assertion that would catch it. Pure bookkeeping.
EXPOSURE = {
    "joins": ("a fan-out changing the grain", ("unique", "unique_combination_of_columns")),
    "aggregates": ("an aggregate over inflated rows", ("unique_combination_of_columns",)),
    "coalesce": ("a NULL replaced by a literal that hides missing data", ("not_null",)),
    "case": ("a value outside the branches anyone expected", ("accepted_values",)),
    "window": ("a window evaluated against the wrong partition", ("unique",)),
}

LEVELS = {0: "cosmetic", 1: "worth knowing", 2: "corrupts a mart"}
CHUNK = 8


@dataclass
class TestSubject:
    test_name: str
    kind: str | None
    column: str | None
    model: str
    model_uid: str
    severity: str
    descendants: int
    marts: int
    grain: list | None = None
    protects: str = ""


def subjects(project, entries) -> list[TestSubject]:
    by_uid = {e.uid: e for e in entries}
    out = []
    for t in project.tests:
        if not t.tests_model or t.tests_model not in project.models:
            continue
        e = by_uid.get(t.tests_model)
        b = project.blast_radius(t.tests_model)
        out.append(TestSubject(
            test_name=t.name, kind=t.kind, column=t.column,
            model=project.models[t.tests_model].name, model_uid=t.tests_model,
            severity=(t.severity or "error").lower(),
            descendants=b["descendants"], marts=b["marts"],
            grain=(e.grain.value if e and e.grain else None),
            protects=_protects(t, e),
        ))
    return out


def _protects(t, entry) -> str:
    col = t.column or ""
    if t.kind in ("unique", "unique_combination_of_columns"):
        return f"the grain: that one row means one {col or 'entity'}"
    if t.kind == "not_null":
        return f"that `{col}` is always present"
    if t.kind == "accepted_values":
        return f"that `{col}` only ever holds an agreed set of values"
    if t.kind == "relationships":
        return f"that every `{col}` points at a row that exists"
    return t.kind or "something the test's own name describes"


def build_state(subs: list[TestSubject], vocab: dict | None = None) -> dict:
    state = {"tests": [
        {"id": i, "test": s.test_name, "kind": s.kind, "column": s.column,
         "model": s.model, "model_grain": s.grain, "protects": s.protects,
         "current_severity": s.severity,
         "blast_radius": {"models_downstream": s.descendants, "marts_downstream": s.marts}}
        for i, s in enumerate(subs)]}
    if vocab:
        state["vocabulary"] = vocab
    return state


def questions_for(subs: list[TestSubject]) -> dict:
    return {f"sev__{i}": score({"test": s.test_name, "model": s.model,
                                "blast_radius": {"models_downstream": s.descendants,
                                                 "marts_downstream": s.marts},
                                **SEV_Q["instructions"]},
                               SEV_Q["criteria"])
            for i, s in enumerate(subs)}


def mismatch(sub: TestSubject, answer: dict) -> tuple[str, str] | None:
    """(judged_level, why) where the judgement and the configured severity disagree."""
    try:
        lvl = round(float(answer["answer"]))
    except (TypeError, ValueError, KeyError):
        return None
    want_error = lvl >= 2
    is_error = sub.severity == "error"
    if want_error and not is_error:
        return (LEVELS[lvl], f"set to {sub.severity}, but a violation reaches {sub.marts} mart(s)")
    if not want_error and is_error and lvl == 0:
        return (LEVELS[lvl], "set to error, but a violation would change nothing anyone reads")
    return None


@dataclass
class Gap:
    model: str
    exposure: str
    would_catch: tuple
    marts: int = 0
    signals: list = field(default_factory=list)


def coverage_gaps(project, digests, entries) -> list[Gap]:
    """*** ENTIRELY CODE. ***

    What the SQL exposes a model to, minus what its tests assert. Percentage test coverage is a
    number everyone knows is meaningless; this names the specific thing nothing is watching.
    """
    have: dict = {}
    for t in project.tests:
        if t.tests_model:
            have.setdefault(t.tests_model, set()).add(t.kind)

    out = []
    for e in entries:
        d = digests.get(e.uid)
        if not d or not d.ok or e.unreadable:
            continue
        kinds = have.get(e.uid, set())
        signals = {
            "joins": bool(d.joins),
            "aggregates": any(n.startswith(("SUM", "COUNT", "AVG", "MIN", "MAX"))
                              for n, _p in d.functions),
            "coalesce": any(r == "coalesce:literal" for r in d.output_roots.values()),
            "case": any(r == "case" for r in d.output_roots.values()),
            "window": bool(d.windows),
        }
        for sig, present in signals.items():
            if not present:
                continue
            why, catchers = EXPOSURE[sig]
            if kinds & set(catchers):
                continue
            out.append(Gap(model=e.name, exposure=why, would_catch=catchers, marts=e.marts))
    return sorted(out, key=lambda g: -g.marts)
