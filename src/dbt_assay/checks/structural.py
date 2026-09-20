"""Structural checks. Exact, free, no API key, no network.

*** A CHECK THAT CANNOT SEE ITS SUBJECT MUST NOT PASS. ***
Every check here is skipped explicitly and loudly for a model whose SQL could not be read or parsed,
and `assay` reports that count beside the findings. A scanner that silently matched nothing has
passed for the wrong reason, which is how a guard ends up protecting nothing at all.

*** SEVERITY IS ARITHMETIC. ***
A finding carries a base severity, and the graph multiplies it. The same defect on a leaf and on a
node feeding nine marts are not the same finding, and nobody should have to remember which is which.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..parse import Digest, case_branch_values

# Functions that return DEGREES on geographic coordinates. Ranking by one of these orders an
# east-west offset nearer than it truly is: a degree of longitude compresses by cos(latitude),
# 0.78 at Colorado's 39N. Measured in the warehouse this tool was built for: 11.3% of termini and
# 18.4% of structures picked a different segment when ranked in metres instead.
# *** ONLY DISTANCE RANKS WRONGLY. MEASURED, NOT ASSUMED. ***
# The distortion is ANISOTROPIC: it shrinks the east-west axis and leaves north-south alone, so
# ranking candidates that lie at different BEARINGS from one point reorders them. That is the real
# defect (11.3% of termini, 18.4% of structures).
# Area, length and perimeter compared among candidates in one locality are all scaled by very nearly
# the SAME factor, so the ranking survives. First run of this check flagged a model ranking counties
# by intersection area with one section -- every candidate at one latitude, ordering unchanged. That
# was a false positive, and these are separated rather than tuned away.
DEGREE_FUNCS = {"ST_DISTANCE"}
DEGREE_MAGNITUDE_FUNCS = {"ST_LENGTH", "ST_AREA", "ST_PERIMETER"}

# Building a box and using it as a distance filter is a different shape from a circle: a
# 0.02-degree box is ~2km east-west and ~2.2km north-south.
BBOX_FUNCS = {"ST_MAKEENVELOPE", "ST_EXPAND", "ST_ENVELOPE"}


@dataclass
class Finding:
    check: str
    subject: str                    # unique_id
    subject_name: str
    file: str
    summary: str
    detail: str
    base: int = 2                   # 1 low, 2 medium, 3 high; the graph scales it
    evidence: dict = field(default_factory=dict)
    descendants: int = 0
    marts: int = 0

    @property
    def weight(self) -> float:
        """base, lifted by reach. A defect feeding nine marts outranks the same defect on a leaf."""
        return self.base * (1 + min(self.descendants, 50) / 25 + min(self.marts, 10) / 5)


# --------------------------------------------------------------------------- tests that can't fail

def _tests_by_model(project) -> dict:
    out: dict = {}
    for t in project.tests:
        if t.tests_model:
            out.setdefault(t.tests_model, []).append(t)
    return out


def unevaluable_tests(project, digests: dict[str, Digest], schema=None) -> list[tuple]:
    """(model, test, column, why) for tests this check could not look at.

    *** A CHECK THAT CANNOT SEE MUST NOT READ AS A PASS. ***
    On a real public package, every one of 38 tests sat on a column assay could not resolve --
    the models end in `select *` -- and the check reported "no structural findings", which looks
    exactly like a clean bill of health. Silence has to be distinguishable from absence.
    """
    out = []
    for uid, tests in _tests_by_model(project).items():
        m = project.models.get(uid)
        d = digests.get(uid)
        for t in tests:
            col = (t.column or "").lower()
            if not m:
                continue
            if not d or not d.ok:
                out.append((m.name, t.name, col, "the model's SQL could not be parsed"))
            elif not col:
                continue
            elif col not in d.output_roots:
                why = ("the model's output columns are unknown (it ends in `select *`)"
                       if "*" in d.output_columns
                       else "that column is not in the model's final select")
                out.append((m.name, t.name, col, why))
    return out


def tests_that_cannot_fail(project, digests: dict[str, Digest]) -> list[Finding]:
    """dbt reports that a test passed. It never reports that a test was INCAPABLE of failing."""
    found = []
    by_model = _tests_by_model(project)

    for uid, tests in by_model.items():
        d = digests.get(uid)
        m = project.models.get(uid)
        if not m or not d or not d.ok:
            continue

        for t in tests:
            col = (t.column or "").lower()
            root = d.output_roots.get(col)
            why = None

            if t.kind == "not_null" and root == "coalesce:literal":
                why = (f"`{col}` is built with COALESCE whose last argument is a literal, so it can "
                       f"never be NULL and this test can never fire.")
            elif t.kind == "not_null" and root == "literal":
                why = f"`{col}` is a literal in the select list."
            elif t.kind == "unique" and root and root.startswith("window:row_number"):
                why = (f"`{col}` is a ROW_NUMBER(), which is unique by construction. The test "
                       f"restates the window, it does not check the data.")
            elif t.kind == "unique" and d.group_by and col in {
                    g.split(".")[-1].lower() for g in d.group_by} and len(d.group_by) == 1:
                why = (f"`{col}` is the model's only GROUP BY key, so one row per value is "
                       f"guaranteed by the query.")
            elif t.kind == "accepted_values" and root == "case":
                branches = case_branch_values(d.output_exprs.get(col, ""))
                accepted = [str(v) for v in (t.kwargs.get("values") or [])]
                if branches is not None and accepted:
                    produced = {b for b in branches if b is not None}
                    if produced and produced.issubset(set(accepted)):
                        why = (f"`{col}` is a CASE that can only return {sorted(produced)}, all of "
                               f"which are already in the accepted list.")

            if why:
                found.append(Finding(
                    check="test_cannot_fail",
                    subject=uid, subject_name=m.name, file=m.path,
                    summary=f"{t.kind} test on `{col}` cannot fail",
                    detail=why, base=2,
                    evidence={"test": t.name, "kind": t.kind, "column": col,
                              "expression": d.output_exprs.get(col, "")[:160],
                              "severity": t.severity},
                ))
    return found


# --------------------------------------------------------------------------- geometry

def ranks_by_degrees(project, digests: dict[str, Digest]) -> list[Finding]:
    """Ordering by a degree-returning function, AFTER alias and wrapper resolution.

    This is the check four hand-written regexes failed at. The AST resolves the order key through
    CASE expressions, nested calls and aliases, so a model that merely MENTIONS ST_Distance in a
    projection is not flagged, and one that sorts by it through an alias is.
    """
    found = []
    for uid, d in digests.items():
        if not d.ok:
            continue
        m = project.models[uid]
        for w in d.windows:
            for i, r in enumerate(w.order_roots):
                ru = r.upper()   # root_of() may return "column"/"case" in lower case
                # Reprojected first: the measure is in the target CRS's units, which is the correct
                # pattern and must never be flagged.
                if i < len(w.order_reprojected) and w.order_reprojected[i]:
                    continue
                if ru in DEGREE_FUNCS:
                    base, note = 3, (
                        "A degree of longitude compresses by cos(latitude) while a degree of "
                        "latitude does not, so ranking candidates that lie at different bearings "
                        "from one point reorders them. Rank in a metre-based measure, or transform "
                        "to a projected CRS first.")
                elif ru in DEGREE_MAGNITUDE_FUNCS:
                    base, note = 1, (
                        "This returns a degree-based magnitude. Among candidates in one locality "
                        "the scale factor is near-constant so the ORDER usually survives; the "
                        "VALUE is still not a real-world measure. Informational.")
                else:
                    continue
                found.append(Finding(
                    check="ranks_by_degrees",
                    subject=uid, subject_name=m.name, file=m.path,
                    summary=f"window ranks by {r}, which returns degrees",
                    detail=note,
                    base=base,
                    evidence={"order_key": w.order_sql[i] if i < len(w.order_sql) else "",
                              "partition_by": w.partition_by, "position": w.position},
                ))
    return found


def bbox_used_as_distance(project, digests: dict[str, Digest]) -> list[Finding]:
    found = []
    for uid, d in digests.items():
        if not d.ok:
            continue
        m = project.models[uid]
        in_join = d.functions_at("join_condition") & BBOX_FUNCS
        in_where = d.functions_at("where") & BBOX_FUNCS
        if in_join or in_where:
            found.append(Finding(
                check="bbox_as_radius",
                subject=uid, subject_name=m.name, file=m.path,
                summary=f"bounding box used as a proximity filter ({', '.join(sorted(in_join | in_where))})",
                detail=("A box is not a circle. At Colorado's latitude a 0.02-degree box is about "
                        "2km east-west and 2.2km north-south, so the filter is anisotropic. Fine as "
                        "a prefilter before an exact test; wrong as the test itself."),
                base=1,
                evidence={"position": "join_condition" if in_join else "where"},
            ))
    return found


# --------------------------------------------------------------------------- windows and dialect

def window_sees_only_filtered_rows(project, digests: dict[str, Digest]) -> list[Finding]:
    """NOT IN `CHECKS`. Kept for a sharper definition, not shipped.

    *** THIS FIRED 18 TIMES ON ITS FIRST RUN AND NOT ONE WAS DEFENSIBLE. ***
    The condition below (an unordered window in a projection, in a query that has a WHERE) matches
    every ordinary `count(*) over (partition by k)`. The real defect needs the filter to REDUCE the
    very partition the window reads, and nothing in the AST alone settles that. A check that fires
    on everything carries nothing, and shipping it would cost the other checks their credibility.

    `max(x) over (partition by k)` in a select list runs AFTER the WHERE.

    It can only ever see rows that already survived the filter, which is usually not what the
    author meant when the filter picks one row per key.
    """
    found = []
    for uid, d in digests.items():
        if not d.ok or not d.predicates:
            continue
        m = project.models[uid]
        for w in d.windows:
            if w.position == "projection" and not w.order_roots:
                found.append(Finding(
                    check="window_after_where",
                    subject=uid, subject_name=m.name, file=m.path,
                    summary="unordered window in a select list, alongside a WHERE",
                    detail=("A window in the select list is evaluated after WHERE, so it sees only "
                            "rows that already passed the filter. If it was meant to see the whole "
                            "partition, compute it before filtering or move the filter to QUALIFY."),
                    base=1,
                    evidence={"partition_by": w.partition_by,
                              "predicate": d.predicates[0][:160]},
                ))
    return found


_WILDCARD = re.compile(r"[.*+?\[\]{}()^$|\\]")


def duckdb_tilde_is_full_match(project, digests: dict[str, Digest]) -> list[Finding]:
    """In DuckDB `~` is `regexp_full_match`, not Postgres's partial match.

    A pattern carrying no regex metacharacters at all is somebody meaning "contains", and a full
    match against it returns zero rows. That took a run down with a ZeroDivisionError once.
    """
    found = []
    for uid, d in digests.items():
        if not d.ok:
            continue
        m = project.models[uid]
        for pat in d.full_match_patterns:
            if _WILDCARD.search(pat):
                continue
            found.append(Finding(
                check="duckdb_full_match",
                subject=uid, subject_name=m.name, file=m.path,
                summary=f"full-match regex against a literal pattern '{pat}'",
                detail=("DuckDB's `~` is regexp_full_match, unlike Postgres. A pattern with no "
                        "metacharacters matches only that exact whole string, so this almost "
                        "certainly returns nothing. Use a contains test, or anchor the pattern "
                        "with `.*` deliberately."),
                base=3,
                evidence={"pattern": pat},
            ))
    return found


# window_sees_only_filtered_rows is deliberately absent. See its docstring.
def arbitrary_pick(project, digests: dict[str, Digest]) -> list[Finding]:
    """A dedupe whose ORDER BY cannot break every tie, so which row survives is luck.

    *** THIS ONE HAS BITTEN THIS PROJECT REPEATEDLY. ***
    `row_number() over (partition by k order by x) = 1` keeps ONE row per k. If `x` has ties the
    winner is whatever the engine happened to return, and it can differ between builds on the same
    data. A crosswalk here made three such picks; a capped query ordered by a column with 150,625
    duplicates drew a different subset every run.

    A tie-break is total when its last key is a column the project declares unique. That is
    checkable against the project's own tests, with no data.
    """
    from ..relate import declared_keys
    unique_cols = set()
    for cols in declared_keys(project).values():
        if len(cols) == 1:
            unique_cols.add(cols[0])
    for t in project.tests:
        if t.kind == "unique" and t.column:
            unique_cols.add(t.column.lower())

    found = []
    for uid, d in digests.items():
        if not d.ok:
            continue
        m = project.models[uid]
        for w in d.windows:
            # only a dedupe: a ranking nobody filters on is a reported position, not a choice
            if not w.partition_columns or not w.order_sql:
                continue
            if not (d.has_qualify or any("rn" in p_.lower() or "= 1" in p_
                                         for p_ in d.predicates)):
                continue
            keys = [o.split()[0].split(".")[-1].strip("()").lower() for o in w.order_sql]
            if any(k in unique_cols for k in keys):
                continue                       # the last resort is a declared-unique column
            found.append(Finding(
                check="arbitrary_pick",
                subject=uid, subject_name=m.name, file=m.path,
                summary=f"dedupe on {w.partition_columns} whose tie-break may not be total",
                detail=("`row_number() ... = 1` keeps one row per partition. None of the ORDER BY "
                        "keys is a column this project declares unique, so ties are broken by "
                        "whatever the engine returned, and the winner can change between builds "
                        "on identical data. Add a unique column as the last sort key."),
                base=2,
                evidence={"partition_by": w.partition_columns, "order_by": w.order_sql[:3]},
            ))
    return found


# *** PARSING A STRUCTURED STRING IS NOT PICKING FROM A LIST. ***
# The first version flagged any first-element access, and all 8 findings on a real project were
# deliberate parses: the street out of "123 Main St, Denver, CO", the prefix of a licence number,
# the first word of a status. `SPLIT_PART(address, ',', 1)` IS the street.
#
# The real defect is a list of EQUIVALENT values -- `associated_case_numbers` is a comma list and
# taking the first is a coin toss. What separates them is the column's own name, so that is what
# is read.
# Anchored on a WORD BOUNDARY, not end-of-string: the column name sits INSIDE the expression
# -- `SPLIT_PART(associated_case_numbers, ',', 1)` -- so `$` matched nothing at all.
_LISTY = re.compile(
    r"_(numbers|ids|keys|codes|names|values|list|items|tags)\b"
    r"|\b(all|multi|assoc\w*)_", re.IGNORECASE)


def first_match_from_a_multivalued_field(project, digests: dict[str, Digest]) -> list[Finding]:
    """Taking element one of a LIST of equivalent values is a silent choice."""
    found = []
    for uid, d in digests.items():
        if not d.ok or not d.first_element_picks:
            continue
        listy = [(e, h) for e, h in d.first_element_picks if _LISTY.search(e)]
        if not listy:
            continue
        m = project.models[uid]
        expr, how = listy[0]
        found.append(Finding(
            check="first_match_pick",
            subject=uid, subject_name=m.name, file=m.path,
            summary=f"takes the first element of a multi-valued field via {how}",
            detail=("Which element is first is the SOURCE's ordering, not a fact about the entity. "
                    "If the field genuinely holds several values, either keep them all and let the "
                    "grain say so, or choose deliberately with an ordering you can defend."),
            base=2,
            evidence={"expression": expr, "how": how, "occurrences": len(listy)},
        ))
    return found


def variant_columns(project, digests, schema=None) -> list[Finding]:
    """A loader split a mixed-type column, and the base column now holds a SUBSET.

    dlt writes `<col>` and `<col>__v_double` when a source mixes types. Reading the base column
    silently drops every row whose value went to the variant: one here lost 22 of 125 real values.
    """
    if schema is None:
        return []
    found = []
    for uid, m in project.models.items():
        cols = [c.lower() for c in schema.columns(uid).names]
        pairs = [(c, base) for c in cols
                 for base in [c.split("__v_")[0]] if "__v_" in c and base in cols]
        if not pairs:
            continue
        found.append(Finding(
            check="variant_column",
            subject=uid, subject_name=m.name, file=m.path,
            summary=f"{len(pairs)} column(s) split by the loader into a typed variant",
            detail=("A mixed-type source column becomes `<col>` and `<col>__v_<type>`, and the "
                    "base column then holds only the rows whose value matched the first type it "
                    "saw. Reading it alone silently drops the rest."),
            base=3,
            evidence={"pairs": [f"{b} / {v}" for v, b in pairs][:6]},
        ))
    return found


CHECKS = (
    tests_that_cannot_fail,
    ranks_by_degrees,
    bbox_used_as_distance,
    duckdb_tilde_is_full_match,
    arbitrary_pick,
    first_match_from_a_multivalued_field,
)


def run_all(project, digests: dict[str, Digest], schema=None) -> list[Finding]:
    out: list[Finding] = []
    for fn in CHECKS:
        out.extend(fn(project, digests))
    out.extend(variant_columns(project, digests, schema))
    for f in out:
        b = project.blast_radius(f.subject)
        f.descendants, f.marts = b["descendants"], b["marts"]
    return sorted(out, key=lambda f: -f.weight)
