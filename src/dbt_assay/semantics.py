"""Predicate intent and description rot: two families nothing else in a warehouse can answer.

*** A FILTER IS THREE DIFFERENT OBJECTS AND THE SQL IS IDENTICAL FOR ALL THREE. ***
Domain logic that must be preserved, a patch over a bad feed that should be fixed upstream, or the
thing that makes the model mean what it means. Confusing them costs either a deleted rule or a hack
nobody dares remove for eighteen months.

*** A DESCRIPTION IS WRITTEN ONCE AND THE SQL CHANGES FORTY TIMES. ***
Asked as the DEFECT, so a high probability means the prose is wrong. And asked only where there IS
prose: a placeholder is not evidence, and a description that is merely brief is not a contradiction.
"""
from __future__ import annotations

from dataclasses import dataclass

from .contracts import QUESTIONS, _meaningful
from .jev import choice, noul

PRED_Q = QUESTIONS["predicate_intent"]
DESC_Q = QUESTIONS["description_contradicts_the_code"]
PRED_VERSION = PRED_Q["prompt_version"] + "+scoped"
# *** THE VERSION MOVES WHEN WHAT IS SENT MOVES. ***
# A verdict is evidence about a question AND the state it was given. Dropping the comment block
# changes the second, so every verdict recorded under `+comments` is about a different question
# from this one -- which is exactly what `effectiveness` reports per version and exactly what it
# would get wrong if the string stayed put.
DESC_VERSION = DESC_Q["prompt_version"] + "+scoped+description_only"

# Each choice carries its own criteria, so predicates are chunked for the same reason columns are.
CHUNK = 6

# A filter this trivially structural is not worth a judgment: a bare NOT NULL on a join key is
# almost always plumbing, and asking spends tokens to be told so.
_SKIP = ("1 = 1", "TRUE", "true")


# *** THE REAL DOCUMENTATION IS OFTEN IN THE FILE, NOT IN schema.yml. ***
# Judged against only a one-line yaml description, this family flagged 11 of 25 models -- and
# reading the top one showed the description was accurate and the header comment above the SQL
# explained the rest. A summary is not a contradiction of the thing it summarizes. The file's own
# leading comment block is documentation too, so it is sent.
#
# Note this is the OPPOSITE call from the defect checks, where comments measurably hurt: there a
# header about case numbers made a clean model look guilty. Here the prose IS the subject.
def header_comment(sql: str, max_lines: int = 60) -> str:
    out = []
    for line in (sql or "").splitlines():
        t = line.strip()
        if not t:
            if out:
                continue
            continue
        if t.startswith("--"):
            out.append(t.lstrip("-").strip())
            if len(out) >= max_lines:
                break
            continue
        break
    return "\n".join(out)


def all_comments(sql: str, max_lines: int = 140) -> str:
    """Comment lines, for the DESCRIPTION family only, up to `max_lines`.

    Measured: with just the leading header, int_water_diversions read 0.75 as contradicting its
    code. The header says "windowed to the same ten years" and the model also emits full-history
    bounds -- and the comment explaining exactly that sits INLINE beside those columns, not at the
    top. The judgment was reasoning correctly from evidence it had not been given.

    This is deliberately the opposite call from the defect checks, where comments measurably hurt.
    There a header about case numbers made a clean model look guilty; here the prose IS the thing
    under judgment.
    """
    out = []
    for line in (sql or "").splitlines():
        t = line.strip()
        if t.startswith("--"):
            body = t.lstrip("-").strip()
            if body:
                out.append(body)
        elif "--" in line:
            body = line.split("--", 1)[1].strip()
            if body:
                out.append(body)
        if len(out) >= max_lines:
            break
    return "\n".join(out)


@dataclass
class Subject:
    uid: str
    name: str
    path: str
    purpose: str | None
    predicates: list
    nested: list
    contract: dict
    header: str | None = None
    comments: str | None = None


def boilerplate(project) -> set[str]:
    """*** A DESCRIPTION 32 MODELS SHARE IS NOT A CLAIM ABOUT ANY OF THEM. ***

    Measured on a 265-model warehouse: 84 of 343 descriptions were shared by two or more models,
    and they produced 8 of the 16 description findings on a first judged run. Every one was the
    same shape -- "Staging model: light cleanup of one raw source" against a model that also
    filters to commercial permits -- which reads as a contradiction because generic prose never
    mentions what the model does. The finding is true and worthless: the fix is to write a
    description, not to change the code.

    `_meaningful` already carries a hardcoded list of placeholders, and a hardcoded list only ever
    catches the ones whoever wrote it thought of. Repetition is the general form, it needs no
    vocabulary, and it holds in any warehouse: prose applied by a template says nothing specific
    BECAUSE it was applied by a template.
    """
    from collections import Counter
    c = Counter((m.description or "").strip() for m in project.models.values()
                if (m.description or "").strip())
    return {text for text, n in c.items() if n > 1}


def subjects(project, digests, schema, entries, limit_predicates: int = 12) -> list[Subject]:
    by_uid = {e.uid: e for e in entries}
    shared = boilerplate(project)
    out = []
    for uid, d in digests.items():
        if not d.ok or uid not in project.models:
            continue
        m = project.models[uid]
        preds = [p for p in d.predicates_atomic if p.strip() not in _SKIP][:limit_predicates]
        e = by_uid.get(uid)
        head = header_comment(m.compiled or "")
        out.append(Subject(
            uid=uid, name=m.name, path=m.path,
            purpose=(m.description.strip()[:900]
                     if _meaningful(m.description) and m.description.strip() not in shared
                     else None),
            header=head[:2400] if len(head) > 40 else None,
            comments=all_comments(m.compiled or "")[:6000] or None,
            predicates=preds,
            nested=[p for p in d.predicates_nested if p.strip() not in _SKIP][:10],
            contract={
                "grain": (e.grain.value if e and e.grain else None),
                "columns": [c.name for c in e.columns][:50] if e else [],
                "reads": e.reads[:10] if e else [],
            },
        ))
    return out


def chunks(items: list, size: int = CHUNK) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def build_state(s: Subject, preds: list, vocab: dict | None = None) -> dict:
    state = {
        "model": s.name,
        "model_purpose": s.purpose,
        "contract": {k: v for k, v in s.contract.items() if v},
        "predicates_under_judgment": preds,
        "the_models_other_filters": [p for p in s.predicates if p not in preds][:12],
        "filters_inside_subqueries_not_under_judgment": s.nested[:8],
    }
    if vocab:
        state["vocabulary"] = vocab
    return {k: v for k, v in state.items() if v not in (None, [], {})}


def predicate_questions(preds: list) -> dict:
    return {
        f"pred__{i}": choice({"predicate": p, **PRED_Q["instructions"]}, PRED_Q["criteria"])
        for i, p in enumerate(preds)
    }


# *** A DESCRIPTION TOO SHORT TO SAY ANYTHING CANNOT BE CONTRADICTED. ***
# Measured on a 358-model warehouse: the project's descriptions run to a median of 26 words and a
# 25th percentile of 11, and NINE of the eighteen models this check flagged carry fewer than ten
# -- "Staging: Tempe AZ commercial permits." at p=0.77, "Staging: Gilbert AZ commercial building
# permits." at p=0.82. There is nothing in four words for SQL to contradict, and asking anyway
# produces a confident answer to a question that was never asked.
#
# Ten words is where the finding count stops falling: twelve removes no further findings and takes
# 24 more models out of scope. 279 of 344 described models are still judged.
MIN_DESCRIPTION_WORDS = 10


def description_state(s: Subject, vocab: dict | None = None) -> dict | None:
    """None where there is no prose to judge. A placeholder is not evidence.

    *** THE COMMENT BLOCK IS NOT THE DESCRIPTION, AND SENDING BOTH MADE THE FINDING UNREADABLE. ***
    This used to send `documentation_in_the_file` beside the description and then file the result
    as "the description contradicts the code". The finding's own evidence had to say "the
    contradiction is in one of these", because the check genuinely could not tell you which -- and
    on 72% of the models it fired on, `code_contradicts_a_claim` was already firing too, quoting
    the exact sentence. A check that cannot name what it is about is a check that gets muted.

    So this judges the DESCRIPTION. The comment block is prose too, and it is handled where it can
    be handled properly: `assay claims --extract` splits it into atomic claims and judges each one
    against the code, one sentence at a time, with the sentence quoted in the finding.
    """
    if not s.purpose:
        return None
    if len(s.purpose.split()) < MIN_DESCRIPTION_WORDS:
        return None
    state = {
        "model": s.name,
        "description": s.purpose,
        "contract": {k: v for k, v in s.contract.items() if v},
        "filters_this_model_applies": s.predicates[:12],
        "filters_inside_its_subqueries": s.nested[:10],
    }
    state = {k: v for k, v in state.items() if v not in (None, [], {})}
    if vocab:
        state["vocabulary"] = vocab
    return state


def description_question() -> dict:
    return {"desc": noul(DESC_Q["instructions"],
                         DESC_Q["criteria"]["true"], DESC_Q["criteria"]["false"])}
