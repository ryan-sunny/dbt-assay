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
            rests_on="column_is_part_of_the_key",
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


def _derived_from_grain(entry, column, digests) -> bool:
    """*** A NAMESPACED ALIAS OF THE KEY IS NOT A SECOND IDENTIFIER. ***

    `parcel_pk` built as `'denver-' || schednum` beside `parcel_id` built as `schednum` identifies
    exactly the same row. Reporting it as an identifier outside the grain is true and useless. If
    the column's own expression mentions a grain column, it is the grain wearing a different name.
    """
    d = digests.get(entry.uid) if digests else None
    if not d:
        return False
    expr = (d.output_exprs.get(column.name) or "").lower()
    if not expr:
        return False
    grain = [g.lower() for g in (entry.grain.value or [])] if entry.grain else []
    for g in grain:
        if g and g in expr:
            return True
        # the grain column is itself an alias: compare what IT rests on
        gexpr = (d.output_exprs.get(g) or "").lower()
        if gexpr and len(gexpr) > 3 and gexpr in expr:
            return True
    return False


def identifier_outside_the_grain(project, entries, digests=None) -> list[Finding]:
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
            if _derived_from_grain(e, c, digests):
                continue
            out.append(Finding(
                check="identifier_outside_grain",
                rests_on="column_role",
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
                rests_on="column_role",
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
                rests_on="column_is_part_of_the_key",
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


def description_contradicts_the_code(project, entries) -> list[Finding]:
    """The prose claims something the code does not do.

    *** THE ONE FAMILY WHOSE FINDING NEEDS NO assay VOCABULARY TO READ. ***
    Everything else here is about grain, provenance and keys, and lands on someone who already
    thinks in those words. This one says: your description says X, your SQL does Y. It is what a
    first run leads with, so it has to reach the same places every other finding reaches.

    Gated at 0.6 because below that the family is declining to commit, and a noul near 0.5 means
    similar probability either way rather than a weak yes.
    """
    out = []
    for e in entries:
        f = e.doc_conflict
        if not f or (f.confidence or 0) < 0.6:
            continue
        out.append(Finding(
            check="description_contradicts_the_code",
            rests_on="description_contradicts_the_code",
            subject=e.uid, subject_name=e.name, file=e.path,
            summary="the description claims something the code does not do",
            detail=("A description is written once and the SQL changes around it. Nothing in a "
                    "warehouse tests prose, so this drifts silently and is read as true by "
                    "everyone downstream. Re-read both, and either fix the code or fix the "
                    "sentence."),
            base=2,
            # *** SEVENTEEN UNCLEAR RULINGS WERE ALL ONE PROBLEM: THE FINDING DID NOT CARRY
            # ENOUGH TO SETTLE IT, AND IN EVERY CASE THE MISSING PIECE WAS ALREADY COMPUTED. ***
            # This family judges the description and the in-file comments TOGETHER, so a reader
            # cannot tell which one the contradiction is in. `water_address_sections` is the clear
            # case: its description and its SQL header are the same sentence verbatim.
            evidence={"probability": f.confidence, "downstream": e.descendants,
                      "marts": e.marts,
                      "it_judged": _prose_judged(e),
                      "where_to_look": ("the contradiction is in one of these. Both the "
                                        "schema.yml description and the model's own comment block "
                                        "were sent. `assay claims --extract` splits prose into "
                                        "atomic claims and `assay verify` names the one that "
                                        "fails.")},
        ))
    return out


# *** QUESTIONS WORTH ASKING THAT NO FINDING RESTS ON YET. ***
# Their answers reach the inventory, the HTML and `trace`, which is why they are asked at all. But
# no finding is derived from them, so ruling on them moves no gate. Written down rather than left
# to be discovered after an afternoon of keypresses, and a test asserts this set is exactly right.
FAMILIES_WITHOUT_FINDINGS = frozenset({
    "null_meaning",                        # reaches the inventory; no finding derives from it
    "predicate_intent",                    # `semantics` reports it directly
    "practice_exception",                  # applied in `practices`, which has its own path
    "same_concept",                        # `align` reports pairs directly
    "severity_fit",                        # adjudicates a finding rather than producing one
    "field_matches_its_name",              # feeds tier: needs the probe
    "units_are_what_the_column_claims",    # feeds tier: needs the probe
    "row_explanation",                     # rows tier: needs warehouse rows
    "row_is_internally_coherent",          # rows tier: needs warehouse rows
    "sentence_is_a_claim",                 # extraction: it decides what to ASK
    "options_overlap",                     # lints a question, not a project
    "same_defect",                         # groups RULINGS: about the tool, not about a project
})

def code_contradicts_a_claim(project, entries) -> list[Finding]:
    """The model's own documentation asserts something its code does not do.

    *** THE CLAIM IS ATOMIC, WHICH IS WHY THIS CAN BE A FINDING AT ALL. ***
    Judged as whole prose, "Boulder commercial building permits, residential filtered out" split
    0.51 supports / 0.47 contradicts and flipped between runs, because one half is true and the
    other is not. A finding cannot be built on a coin flip. `assay claims` breaks prose into
    atomic claims first, and this rests on those.
    """
    out = []
    for e in entries:
        for ctx, p_ in e.claim_conflicts:
            text = (ctx or "").split(": ", 1)[-1] if ctx else ""
            out.append(Finding(
                check="code_contradicts_a_claim",
                rests_on="claim_alignment",
                subject=e.uid, subject_name=e.name, file=e.path,
                summary=f"the code contradicts a claim this project makes: {text[:90]}",
                detail=("Someone wrote this down about this model and the code does something "
                        "else. Nothing in a warehouse tests a sentence, so it stays written and "
                        "stays believed. `assay claims --write claims.yml` shows where it was "
                        "written; either the code or the sentence has to move."),
                base=2,
                evidence={"claim": text, "probability": round(p_, 3),
                          "downstream": e.descendants, "marts": e.marts},
            ))
    return out


def _prose_judged(entry) -> dict:
    """What prose this model actually has, so a reader knows where to look."""
    out: dict = {}
    desc = (getattr(entry, "description", "") or "").strip()
    if desc:
        out["schema_yml_description"] = desc[:300]
    out["and_the_models_own_comment_block"] = "sent too; see the top of " + (entry.path or "the file")
    return out


def _collapse_note(entry, hop: str) -> str:
    """Whether a group by or distinct was found on this hop, said either way."""
    parent = (hop or "").split(" -> ")[0].strip()
    if parent and parent in (getattr(entry, "union_parents", None) or set()):
        return f"{parent} is read as a UNION arm, which cannot multiply"
    pre = getattr(entry, "pre_aggregated_parents", None) or {}
    if parent in pre:
        return f"{parent} was collapsed to one row per {pre[parent]} before the join"
    return ("no group by, distinct or union was found on THIS path. The child may still collapse "
            "elsewhere, which is why the ruling needs the file")


def hop_multiplies_rows(project, entries) -> list[Finding]:
    """A join that turns one parent row into several, where nothing says it should.

    *** NO SINGLE-MODEL CHECK CAN SEE THIS. ***
    Every other question here reads one model. A fan-out introduced at one hop and consumed three
    models downstream is invisible to all of them, and every count past it is inflated while
    nothing fails. It is the defect a person finds by chasing a number by hand, months later.
    """
    out = []
    for e in entries:
        for ctx, p_ in e.fanout_hops:
            # *** A UNION MEMBER CANNOT MULTIPLY, AND CODE KNOWS IT. ***
            # Ten of twelve disagreements were this. The judgment is allowed to be wrong here;
            # the finding is not, because a parser settles it exactly and for free.
            if e.union_parents and any(
                    f" {p_name} " in f" {ctx} " for p_name in e.union_parents):
                continue
            # *** A JOIN ONTO A UNIQUE KEY CANNOT FAN OUT EITHER. ***
            # The other two of the twelve, and the ruling named the blind spot itself: "these
            # parents carry no declared uniqueness test, which is why assay cannot see it".
            # `int_water_streamflow_summary` is 2,387 rows over 2,387 distinct `abbrev` and
            # nothing in the project says so. Declared uniqueness is free and counted uniqueness
            # comes from `--verify`; both land here, and both settle exactly what the judgment
            # was allowed to be wrong about.
            if e.unique_key_parents and any(
                    f" {p_name} " in f" {ctx} " for p_name in e.unique_key_parents):
                continue
            out.append(Finding(
                check="hop_multiplies_rows",
                rests_on="edge_preserves_the_grain",
                subject=e.uid, subject_name=e.name, file=e.path,
                summary=f"this hop appears to multiply rows without declaring it: {ctx[:70]}",
                detail=("One parent row becomes several child rows here, and no group by, "
                        "distinct or aggregate says that was intended. Every count downstream of "
                        "this edge is inflated by the same factor, and no test fails, because "
                        "each individual row is valid. Check the join key against the parent's "
                        "own declared key."),
                base=3,
                evidence={"hop": ctx, "probability": round(p_, 3),
                          "downstream": e.descendants, "marts": e.marts,
                          # Which hop the collapse sits on is the thing a ruling stalls for, and
                          # the parser already knows. Saying "no collapse found on this path" is
                          # as useful as naming one.
                          "collapse_on_this_path": _collapse_note(e, ctx)},
            ))
    return out


CHECKS = (measure_inside_the_grain, unresolved_judgment, description_contradicts_the_code,
          code_contradicts_a_claim, hop_multiplies_rows)


def run_all(project, entries, declared, digests=None) -> list[Finding]:
    out = grain_contradicts_declared_key(project, entries, declared)
    out += identifier_outside_the_grain(project, entries, digests)
    for fn in CHECKS:
        out.extend(fn(project, entries))
    return sorted((_weightless(f, project) for f in out), key=lambda f: -f.weight)


def apply_policy(findings, cfg, store, project=None) -> tuple[list, list]:
    """(kept, waived). Each kept item is (finding, action, why).

    Runs over BOTH streams. A waiver removes a finding and is recorded so the count is visible; an
    expired waiver is not a waiver. `fail` is refused for a JUDGED question without enough recorded
    verdicts and downgraded to `queue`, in code, not in documentation.
    """
    from .selector import resolve

    counts = store.adjudication_counts() if store else {}
    # *** THE RATE IS MEASURED ON THE VERSION SHIPPING NOW. ***
    # A verdict recorded against v1 of a question is evidence about v1. Letting it authorise v4 to
    # fail a build is the same error as letting an agent's ruling count as a person's: the number
    # is real and it is about something else.
    rates: dict = {}
    if store is not None and cfg.min_agreement:
        from . import __version__
        from .contracts import QUESTIONS
        shipping = {n: (q or {}).get("prompt_version", "") for n, q in QUESTIONS.items()}
        rates = store.accuracy_by_family(shipping, default=f"assay.{__version__}")
    kept, waived = [], []
    scope_cache: dict = {}

    for f in findings:
        q = cfg.for_question(f.check)
        if not q.enabled:
            waived.append((f, "disabled in audit.yml"))
            continue
        w = cfg.waived(f.subject_name, f.check)
        if w:
            waived.append((f, f"waived: {w.reason}"))
            continue
        if q.select and project is not None:
            if q.select not in scope_cache:
                scope_cache[q.select] = resolve(project, q.select)
            scope = scope_cache[q.select]
            if scope is not None and f.subject not in scope:
                waived.append((f, f"out of scope for `{q.select}`"))
                continue

        conf = f.evidence.get("confidence")
        judged_answer = {"kind": "noul", "answer": str(conf)} if conf is not None else None
        # *** COUNT ON THE QUESTION, NOT ON THE FINDING. ***
        # Verdicts are recorded per question family and a finding is not a question: several
        # findings rest on one family, and a structural finding rests on none. Looking up
        # `f.check` here meant nine of ten families could never satisfy the gate floor.
        fam = f.rests_on or f.check
        rate = rates.get(fam)
        act = q.action_for(judged_answer if q.act else None,
                           counts.get(fam, 0), cfg.min_adjudications,
                           agreement=rate[0] if rate else None,
                           min_agreement=cfg.min_agreement)
        why = "audit.yml" if act else "default by severity"
        kept.append((f, act or ("queue" if f.base >= 3 else "annotate"), why))
    return kept, waived
