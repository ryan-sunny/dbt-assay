"""Do two columns in different models mean the same thing?

*** THE PROJECT ALREADY LABELLED THIS AND NOBODY NOTICED. ***
Every join condition in the warehouse is somebody asserting that two columns hold the same concept.
`a.county = b.county_name` is a positive label, free, already written, and never used. That is the
same trick the declared keys gave grain, and it is what makes this family measurable on day one
rather than after a fortnight of adjudication.

*** ALL-PAIRS IS MILLIONS, SO CANDIDATES ARE BLOCKED FIRST. ***
4,684 columns is eleven million pairs. Candidates are narrowed by name relatedness and matching
role before anything is asked, which is ordinary record-linkage blocking: cheap, approximate, and
harmless when wrong because the judgment still decides.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .contracts import QUESTIONS
from .jev import score

ALIGN_Q = QUESTIONS["same_concept"]
ALIGN_VERSION = ALIGN_Q["prompt_version"]

# Rounding to the nearest level, as the cookbook routes it: 0.5 and 1.5 are natural cut points and
# nobody has to tune a threshold.
ROUTES = {0: "different", 1: "review", 2: "same"}

_SPLIT = re.compile(r"[^a-z0-9]+")
# Suffixes that carry no meaning of their own: county / county_name / county_id are one concept.
_NOISE = {"id", "key", "name", "code", "num", "no", "pk", "fk", "val", "value", "txt", "desc"}


def tokens(col: str) -> set[str]:
    return {t for t in _SPLIT.split(col.lower()) if t and t not in _NOISE}


def relatedness(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


@dataclass
class Pair:
    model_a: str
    column_a: str
    model_b: str
    column_b: str
    role_a: str | None = None
    role_b: str | None = None
    label: str | None = None        # "same", where a join in this project already asserts it

    @property
    def key(self) -> str:
        return f"{self.model_a}.{self.column_a}::{self.model_b}.{self.column_b}"


def joined_pairs(project, digests, schema, dialect: str = "duckdb") -> set:
    """Column pairs this project already joins. Free positive labels.

    *** THE TWO SIDES OF ONE EQUALITY, NOT EVERY COLUMN IN THE ON CLAUSE. ***
    A first version paired all the names in a condition with each other, so a compound join
    `a.building_key = b.building_key AND a.geography = b.geography` produced the label
    (building_key, geography) -- two separate equalities read as one claim. It yielded 132 "labels"
    including ('bad', 'city'), all of them nonsense. Only `left = right` is somebody asserting two
    columns hold the same concept.
    """
    import sqlglot
    from sqlglot import exp

    out = set()
    for uid, m in project.models.items():
        d = digests.get(uid)
        if not d or not d.ok or not m.compiled:
            continue
        try:
            tree = sqlglot.parse_one(m.compiled, dialect=dialect)
        except Exception:                                    # noqa: BLE001,S112
            continue
        for join in tree.find_all(exp.Join):
            on = join.args.get("on")
            if on is not None:
                for eq in on.find_all(exp.EQ):
                    a, b = eq.this, eq.expression
                    if isinstance(a, exp.Column) and isinstance(b, exp.Column):
                        na, nb = a.name.lower(), b.name.lower()
                        if na != nb:
                            out.add(tuple(sorted((na, nb))))
    return out


def candidates(entries, joined: set, min_relatedness: float = 0.5,
               max_pairs: int = 400) -> list[Pair]:
    # *** A ROLE SHARPENS THE BLOCKING BUT MUST NOT BE REQUIRED. ***
    # Roles come from a judgment that may not have been run, and demanding one produced zero
    # candidates on a real project. Name relatedness alone is a perfectly good filter; a role, where
    # it exists, just stops an identifier being paired with a measure.
    cols = []
    for e in entries:
        for c in e.columns:
            cols.append((e.name, c.name, c.role.value if c.role else None))
    labels = {tuple(sorted(x)) for x in joined}
    out: list[Pair] = []
    seen = set()
    for i, (ma, ca, ra) in enumerate(cols):
        for mb, cb, rb in cols[i + 1:]:
            if ma == mb:
                continue
            if ra and rb and ra != rb:
                continue
            if ca == cb:
                continue                      # identical names need no judgment
            if relatedness(ca, cb) < min_relatedness:
                continue
            k = tuple(sorted((f"{ma}.{ca}", f"{mb}.{cb}")))
            if k in seen:
                continue
            seen.add(k)
            lab = "same" if tuple(sorted((ca.lower(), cb.lower()))) in labels else None
            out.append(Pair(ma, ca, mb, cb, ra, rb, lab))
    # *** LABELLED PAIRS GO FIRST, OR CALIBRATION NEVER HAPPENS. ***
    # Walking columns in order filled the cap with unlabelled pairs and reported "0 already
    # asserted by a join" on a project that had 26 such assertions. The measurement is the point,
    # so the pairs that can be scored are always in the sample.
    out.sort(key=lambda p: (p.label is None, p.model_a, p.column_a))
    return out[:max_pairs]


def build_state(pairs: list[Pair], vocab: dict | None = None) -> dict:
    state = {"pairs": [
        {"id": i, "column_a": {"model": p.model_a, "column": p.column_a, "role": p.role_a},
         "column_b": {"model": p.model_b, "column": p.column_b, "role": p.role_b}}
        for i, p in enumerate(pairs)]}
    if vocab:
        state["vocabulary"] = vocab
    return state


def questions_for(pairs: list[Pair]) -> dict:
    return {f"align__{i}": score({"pair_id": i,
                                  "column_a": f"{p.model_a}.{p.column_a}",
                                  "column_b": f"{p.model_b}.{p.column_b}",
                                  **ALIGN_Q["instructions"]},
                                 ALIGN_Q["criteria"])
            for i, p in enumerate(pairs)}


def route(answer: dict) -> str:
    """Round to the nearest level. No threshold to tune, which is the cookbook's whole point."""
    try:
        v = float(answer["answer"])
    except (TypeError, ValueError, KeyError):
        return "review"
    return ROUTES.get(round(v), "review")
