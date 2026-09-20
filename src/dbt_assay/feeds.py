"""The input layer: has a feed changed its mind while its schema held still?

*** THE DEFECT THIS CATCHES IS INVISIBLE TO EVERY OTHER CHECK. ***
A column whose name, type and row count all held steady while its CONTENT changed kind passes
schema tests and volume monitors alike. A `county` column that starts carrying city names, a
`water_source` that switches from names to codes, a feed that quietly changes unit.

*** SAMPLED PER LOAD, NOT PER ROW. ***
The defect is uniform across a load, so twenty rows answer it. Running this per row would cost
thousands of times more to learn the same thing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import QUESTIONS
from .jev import choice

NAME_Q = QUESTIONS["field_matches_its_name"]
UNIT_Q = QUESTIONS["units_are_what_the_column_claims"]
NAME_VERSION = NAME_Q["prompt_version"]
UNIT_VERSION = UNIT_Q["prompt_version"]

CHUNK = 6

# *** THE FEED LAYER WANTS EVERY COLUMN, NOT THE KEY CANDIDATES. ***
# Targeting for the PROBE picks columns that might be a key, because that is what counting a
# distinct cardinality answers. A drifting feed hides in the OTHER columns: the `county` that
# started carrying city names, the amount that changed unit. Reusing the probe's selection sampled
# two id columns and found nothing, twice.
MAX_COLUMNS = 16


def columns_to_sample(schema, uid, fallback: list) -> list:
    known = [c for c in schema.columns(uid).names]
    return (known or fallback)[:MAX_COLUMNS]


@dataclass
class FeedSubject:
    relation: str
    uid: str
    columns: list
    sample: list = field(default_factory=list)      # rows, as dicts
    profile: dict = field(default_factory=dict)
    sentinels: list = field(default_factory=list)


def column_samples(subject: FeedSubject, col: str, n: int = 12) -> list:
    return [r.get(col) for r in subject.sample if r.get(col) is not None][:n]


def build_state(subject: FeedSubject, cols: list, vocab: dict | None = None) -> dict:
    state = {
        "relation": subject.relation,
        "columns_under_judgement": [
            {"column": c,
             "sample": column_samples(subject, c),
             "profile": _profile_for(subject, c)}
            for c in cols
        ],
        "other_columns_in_this_relation": [c for c in subject.columns if c not in cols][:20],
    }
    if subject.sentinels:
        # Code already found these. They are fact, not a hint.
        state["placeholders_code_already_found"] = [
            {"column": c, "value": v} for c, v, _w in subject.sentinels]
    if vocab:
        state["vocabulary"] = vocab
    return {k: v for k, v in state.items() if v not in (None, [], {})}


def _profile_for(subject: FeedSubject, col: str) -> dict | None:
    i = subject.columns.index(col) if col in subject.columns else None
    if i is None or not subject.profile:
        return None
    lo, hi = subject.profile.get(f"min_{i}"), subject.profile.get(f"max_{i}")
    numeric = subject.profile.get(f"num_{i}")
    rows = subject.profile.get("row_count")
    if lo is None and hi is None:
        return None
    return {"min": lo, "max": hi, "rows_that_are_numeric": numeric, "rows": rows}


def questions_for(cols: list, subject: FeedSubject) -> dict:
    qs = {}
    for c in cols:
        qs[f"name__{c}"] = choice(
            {"column": c, "relation": subject.relation, **NAME_Q["instructions"]},
            NAME_Q["criteria"])
        # A unit question only makes sense where there are numbers to judge.
        if (_profile_for(subject, c) or {}).get("rows_that_are_numeric"):
            qs[f"unit__{c}"] = choice(
                {"column": c, "relation": subject.relation, **UNIT_Q["instructions"]},
                UNIT_Q["criteria"])
    return qs


def chunks(items: list, size: int = CHUNK) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]
