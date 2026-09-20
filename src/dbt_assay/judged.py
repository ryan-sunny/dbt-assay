"""Findings from judgments. The contradictions INSIDE the inventory.

*** A FINDING IS A DISAGREEMENT BETWEEN TWO THINGS THAT SHOULD AGREE. ***
The inventory is the artifact; findings fall out of it. A column judged an identifier that is not
part of the grain, a measure that IS, a judged key that contradicts the test the team wrote -- each
is two facts about one model that cannot both be right, and that is a much sharper thing to report
than "a model said 0.8".

*** STRUCTURAL AND JUDGED FINDINGS LIVE IN ONE PLACE. ***
They were in separate worlds: `check` saw only the parser's findings and nothing from `infer` or
`columns` ever reached the store. One stream, one table, one severity scale, so "what is wrong with
this model" has a single answer.

*** NOTHING HERE GATES. ***
Every finding carries the action its configured thresholds earn it, and `fail` is refused for any
question without enough recorded verdicts. A judged finding is a question for a person until a
measurement says otherwise.
"""
from __future__ import annotations

from .checks.structural import Finding

# A judgment this confident is worth contradicting a human over; below it, the disagreement is
# more likely the model's than the team's.
STRONG = 0.80


def _weightless(f: Finding, project) -> Finding:
    b = project.blast_radius(f.subject)
    f.descendants, f.marts = b["descendants"], b["marts"]
    return f


def grain_contradicts_declared_key(project, entries, declared) -> list[Finding]:
    """The judgment and the project's own `unique` test disagree about what one row is.

    One of them is wrong and it matters which: a stale test passes forever while the grain has
    moved under it, and a wrong judgment poisons everything downstream that reads the inventory.
    """
    out = []
    for e in entries:
        if e.uid not in declared or not e.grain or e.grain.source != "judged":
            continue
        got, want = {c.lower() for c in e.grain.value}, set(declared[e.uid])
        if got == want:
            continue
        conf = e.grain.confidence
        out.append(Finding(
            check="grain_contradicts_test",
            subject=e.uid, subject_name=e.name, file=e.path,
            summary=f"inferred grain {sorted(got)} differs from the declared key {sorted(want)}",
            detail=("A test in this project declares one row per "
                    f"{sorted(want)}, and the model's own SQL reads as one row per {sorted(got)}. "
                    "Either the test has gone stale while the model moved, or the inference is "
                    "wrong. A stale uniqueness test passes forever and protects nothing."),
            base=3 if (conf or 0) >= STRONG else 2,
            evidence={"inferred": sorted(got), "declared": sorted(want),
                      "confidence": conf, "route": e.grain.note[:120]},
        ))
    return out


def identifier_outside_the_grain(project, entries) -> list[Finding]:
    """A column judged to name the row, that the grain does not include."""
    out = []
    for e in entries:
        if not e.grain:
            continue
        for c in e.columns:
            if not c.role or c.role.value != "identifier" or c.in_key:
                continue
            if (c.role.confidence or 0) < STRONG:
                continue
            out.append(Finding(
                check="identifier_outside_grain",
                subject=e.uid, subject_name=e.name, file=e.path,
                summary=f"`{c.name}` reads as an identifier but is not part of the grain",
                detail=(f"The grain is {e.grain.value}, which does not include `{c.name}`. Either "
                        f"the grain is narrower than it should be, or this column identifies a "
                        f"DIFFERENT entity and is a foreign key rather than an identifier."),
                base=2,
                evidence={"column": c.name, "confidence": c.role.confidence,
                          "grain": e.grain.value, "provenance": c.provenance.value},
            ))
    return out


def measure_inside_the_grain(project, entries) -> list[Finding]:
    """A quantity being used as part of the identity.

    This is the shape three real models had: a grain of (wdid, record_first_year, record_last_year)
    where the years are summaries of the entity, not part of which entity it is. Grouping by a
    measure silently splits one entity into several rows.
    """
    out = []
    for e in entries:
        if not e.grain:
            continue
        for c in e.columns:
            if not c.role or c.role.value != "measure" or not c.in_key:
                continue
            if (c.role.confidence or 0) < STRONG:
                continue
            out.append(Finding(
                check="measure_inside_grain",
                subject=e.uid, subject_name=e.name, file=e.path,
                summary=f"`{c.name}` is a measure but sits inside the grain {e.grain.value}",
                detail=("Grouping by a quantity splits one entity into a row per distinct value "
                        "of it. If this column is a summary OF the entity rather than part of "
                        "which entity it is, the grain is wider than intended and every count "
                        "over this model is inflated."),
                base=3,
                evidence={"column": c.name, "confidence": c.role.confidence,
                          "grain": e.grain.value, "provenance": c.provenance.value},
            ))
    return out


def unresolved_judgment(project, entries) -> list[Finding]:
    """The model said it could not tell, and that is a queue item, not a silent default.

    A noul near 0.5 means similar probability either way. Turning that into a confident value is
    the worst of the three available answers, so it surfaces as something for a person to settle.
    """
    out = []
    for e in entries:
        if e.grain and e.grain.resting_on:
            out.append(Finding(
                check="grain_unresolved",
                subject=e.uid, subject_name=e.name, file=e.path,
                summary=f"grain is unresolved for {len(e.grain.resting_on)} column(s)",
                detail=("The judgment could not tell whether these columns identify a row. They "
                        "are kept in the grain, because narrowing a key on absent evidence is "
                        "worse than leaving it wide, but the grain is not settled until a person "
                        "or a probe says so."),
                base=1,
                evidence={"unresolved": e.grain.resting_on, "grain": e.grain.value,
                          "confidence": e.grain.confidence},
            ))
    return out


CHECKS = (identifier_outside_the_grain, measure_inside_the_grain, unresolved_judgment)


def run_all(project, entries, declared) -> list[Finding]:
    out = grain_contradicts_declared_key(project, entries, declared)
    for fn in CHECKS:
        out.extend(fn(project, entries))
    return sorted((_weightless(f, project) for f in out), key=lambda f: -f.weight)


def apply_policy(findings, cfg, store) -> list[tuple]:
    """(finding, action) for each, with `fail` refused while the question is unmeasured."""
    counts = store.adjudication_counts() if store else {}
    out = []
    for f in findings:
        q = cfg.for_question(f.check)
        if cfg.waived(f.subject_name, f.check):
            continue
        # A judged finding carries a probability; a structural one does not, and its configured
        # action stands on the check's own base severity instead.
        conf = f.evidence.get("confidence")
        answer = {"kind": "noul", "answer": str(conf)} if conf is not None else None
        act = None
        if answer and q.act:
            act = q.action_for(answer, counts.get(f.check, 0), cfg.min_adjudications)
        out.append((f, act or ("queue" if f.base >= 3 else "annotate")))
    return out
