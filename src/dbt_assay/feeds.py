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
        "columns_under_judgment": [
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


# *** THE HALF THE MODEL MUST NOT DO. ***
# Verified: asked whether magnitudes were plausible for a unit, Jev passed 218,235 as "acres" at
# 0.82 and 4,073,925 as "acre-feet" at 0.54 -- wrong by 43,560x and 325,851x. Comparing a number
# against a range is arithmetic, it is exact, and it is free. The model says what the NAME claims;
# this says whether the values can be that.
#
# The bounds are deliberately WIDE. They exist to catch a unit confusion of three or more orders
# of magnitude, which is what a gallons/acre-feet or sqft/acres mix-up looks like. A merely large
# parcel must not be a finding.
# (max plausible, human name). Deliberately WIDE: these catch a unit confusion of three or more
# orders of magnitude -- a gallons/acre-feet or sqft/acres mix-up -- never a merely large value.
UNIT_MAX: dict[str, tuple[float, str]] = {
    "acres":                 (2.0e5,  "acres"),      # the largest US ranches are ~1e6; a roll is not
    "square_feet":           (5.0e9,  "square feet"),
    "acre_feet":             (1.0e6,  "acre-feet"),  # Lake Powell is ~2.4e7
    "gallons":               (1.0e12, "gallons"),
    "cubic_feet_per_second": (1.0e6,  "cfs"),        # the Mississippi is ~6e5
    "gallons_per_minute":    (1.0e7,  "gpm"),
    "feet":                  (1.0e5,  "feet"),       # Everest is 2.9e4
    "miles":                 (1.0e4,  "miles"),
    "meters":                (1.0e5,  "meters"),
    "currency":              (1.0e12, "a currency amount"),
    "days":                  (1.0e6,  "days"),
}


def range_conflicts(unit_family: str, profile: dict, col_index: int | None) -> str | None:
    """A plain-language reason the observed range cannot be the unit the name claims, or None.

    Returns None whenever it cannot tell -- no profile, no index, no bounds for that family.
    Silence here must mean "not checked", never "checked and fine", which is why the caller only
    reports when a reason comes back.
    """
    if col_index is None or unit_family not in UNIT_MAX:
        return None
    hi, human = UNIT_MAX[unit_family]
    try:
        mx = profile.get(f"max_{col_index}")
        mn = profile.get(f"min_{col_index}")
        mx = float(mx) if mx is not None else None
        mn = float(mn) if mn is not None else None
    except (TypeError, ValueError):
        return None
    if mx is not None and mx > hi:
        return (f"the name claims {human}, and the largest value is {mx:,.0f}, "
                f"which is more than {hi:,.0f}")
    if mn is not None and mn < 0 and unit_family not in ("currency", "feet", "meters"):
        return f"the name claims {human}, and the smallest value is {mn:,.2f}, which is negative"
    return None
