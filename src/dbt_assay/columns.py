"""Column role and null meaning: the two families that need judgment, after code takes its share.

*** QUESTIONS BATCH, SUBJECTS DO NOT -- BUT CRITERIA ARE NOT FREE. ***
Every question in a request carries its OWN criteria, so forty choice questions with ten richly
described options each would spend most of a 64k budget restating option definitions. Columns are
therefore chunked, and both families are asked about the same chunk in one call because they share
one state. Sixteen questions over eight columns costs one state and two criteria blocks per column.

*** CODE GOES FIRST, AND WHAT IT ESTABLISHED IS SENT AS FACT. ***
`provenance` already knows a column is an aggregate, a literal, a window rank or a coalesce over a
literal. That is not a hint for the model to re-derive; it is evidence, and it settles a good share
of null_meaning on its own.

*** THIS PROJECT ALREADY CONTAINS LABELS FOR BOTH FAMILIES. ***
A `unique` test declares an identifier. A `not_null` test declares that NULL is impossible. Neither
covers the whole answer space, but both are free, and a question that disagrees with a test the team
wrote is either a bad question or a stale test. Either is worth knowing on day one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import QUESTIONS
from .jev import choice
from .provenance import classify

ROLE_Q = QUESTIONS["column_role"]
NULL_Q = QUESTIONS["null_meaning"]
ROLE_VERSION = ROLE_Q["prompt_version"]
NULL_VERSION = NULL_Q["prompt_version"]

# Eight columns per call keeps two criteria blocks per column well inside the 64k shared budget
# while still amortising the state across a useful number of subjects.
CHUNK = 8

# *** CAN IT BE NULL IS CODE. WHAT A NULL MEANS IS A JUDGMENT. ***
# Provenance classes that guarantee a value. Asking a judgment about a NULL that cannot occur is
# how the first version of null_meaning answered `unknown_value` 25 times out of 25.
NEVER_NULL_PROVENANCE = {"constant", "defaulted"}


@dataclass
class ColumnFacts:
    name: str
    expression: str = ""
    provenance: str = ""
    provenance_note: str = ""
    has_not_null_test: bool = False
    has_unique_test: bool = False
    in_declared_key: bool = False
    can_be_null: bool = True
    why_not_null: str = ""


@dataclass
class Labels:
    """What the project itself already asserts. Free, partial, and WEAKER THAN IT LOOKS.

    *** A `unique` TEST DECLARES UNIQUENESS, NOT IDENTITY. ***
    Measured: the role family agreed with 57 of 61 of these labels, and reading the four
    disagreements showed THREE were the label being wrong. `geom_json` carries a unique test and was
    answered `geometry` at 0.96-0.99, which is correct; a polygon that happens not to repeat is not
    the entity's identifier. So this set calibrates a question cheaply and must never be treated as
    ground truth. Where they disagree, read the case.
    """
    role: dict = field(default_factory=dict)          # (uid, col) -> "identifier"
    null_meaning: dict = field(default_factory=dict)  # (uid, col) -> "impossible"


def free_labels(project) -> Labels:
    out = Labels()
    for t in project.tests:
        if not t.tests_model or not t.column:
            continue
        col = t.column.lower()
        if t.kind == "not_null":
            out.null_meaning[(t.tests_model, col)] = "impossible"
        elif t.kind == "unique":
            out.role[(t.tests_model, col)] = "identifier"
    return out


def facts_for(uid: str, project, digests, schema, declared: dict) -> dict[str, ColumnFacts]:
    d = digests.get(uid)
    prov = classify(uid, project, digests, schema)
    key = {c.lower() for c in (declared.get(uid) or [])}
    tested_nn, tested_u = set(), set()
    for t in project.tests:
        if t.tests_model == uid and t.column:
            (tested_nn if t.kind == "not_null" else tested_u if t.kind == "unique"
             else set()).add(t.column.lower())

    out = {}
    for col in schema.columns(uid).names:
        c = col.lower()
        p = prov.get(c)
        kind = p.kind if p else "unknown"
        why = ""
        if c in tested_nn:
            why = "a not_null test declares it"
        elif kind in NEVER_NULL_PROVENANCE:
            why = f"provenance is `{kind}`, which always yields a value"
        out[c] = ColumnFacts(
            name=c,
            expression=(d.output_exprs.get(c, "") if d else "")[:140],
            provenance=kind,
            provenance_note=p.evidence if p else "",
            has_not_null_test=c in tested_nn,
            has_unique_test=c in tested_u,
            in_declared_key=c in key,
            can_be_null=not why,
            why_not_null=why,
        )
    return out


def chunks(names: list[str], size: int = CHUNK) -> list[list[str]]:
    return [names[i:i + size] for i in range(0, len(names), size)]


def build_state(uid: str, project, schema, facts: dict[str, ColumnFacts], cols: list[str],
                grain: list[str] | None = None, vocab: dict | None = None) -> dict:
    m = project.models[uid]
    state = {
        "model": {"name": m.name, "materialized": m.materialized},
        "grain": grain or None,
        "columns_under_judgment": [
            {
                "name": c,
                "expression": facts[c].expression or None,
                # Fact, established by a parser. Not a hint.
                "provenance": facts[c].provenance,
                "provenance_means": facts[c].provenance_note,
                "part_of_the_declared_key": facts[c].in_declared_key or None,
                "can_be_null": facts[c].can_be_null,
                "never_null_because": facts[c].why_not_null or None,
            }
            for c in cols if c in facts
        ],
        "other_columns_in_this_model": [c for c in schema.columns(uid).names
                                        if c.lower() not in set(cols)][:40],
    }
    if _meaningful(m.description):
        state["model"]["description"] = m.description.strip()[:400]
    if vocab:
        state["vocabulary"] = vocab
    return _prune(state)


def questions_for(cols: list[str], facts: dict[str, ColumnFacts] | None = None,
                  ask_null: bool = False) -> dict:
    """Both families about the same chunk, in ONE call: they share a state, so the state is paid
    for once and each extra question costs only its own tokens.

    A column code has already shown cannot be NULL gets no null question. Asking one spends tokens
    to receive a confident answer to a hypothetical.

    `ask_null` defaults to FALSE. Measured: without a null rate from the probe and without the
    column's role, the family answers at median confidence 0.49 over six options. See the note
    beside it in questions/columns.yml.
    """
    qs = {}
    for c in cols:
        qs[f"role__{c}"] = choice({"column": c, **ROLE_Q["instructions"]}, ROLE_Q["criteria"])
        f = (facts or {}).get(c)
        if ask_null and (f is None or f.can_be_null):
            qs[f"null__{c}"] = choice({"column": c, **NULL_Q["instructions"]}, NULL_Q["criteria"])
    return qs


def _meaningful(desc: str | None) -> bool:
    if not desc:
        return False
    t = desc.strip().lower().rstrip(".")
    return len(t) > 24 and t not in {"mart model", "staging model", "intermediate model",
                                     "todo", "tbd", "model"}


def _prune(obj):
    if isinstance(obj, dict):
        return {k: _prune(v) for k, v in obj.items() if v not in (None, [], {}, "")}
    if isinstance(obj, list):
        return [_prune(x) for x in obj]
    return obj
