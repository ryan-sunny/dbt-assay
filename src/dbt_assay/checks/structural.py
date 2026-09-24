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

import hashlib
import json
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from ..parse import Digest, case_branch_values

# Functions that return DEGREES on geographic coordinates. Ranking by one of these orders an
# east-west offset nearer than it truly is: a degree of longitude compresses by cos(latitude),
# 0.78 at Colorado's 39N. Measured in the warehouse this tool was built for: 11.3% of termini and
# 18.4% of structures picked a different segment when ranked in meters instead.
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


# *** WHAT MAKES A FINDING THIS ONE, AND WHAT IS ONLY ITS CURRENT STRENGTH. ***
# `arbitrary_pick` emits one finding per window function, and on a real warehouse four of them on
# one model shared a summary and differed only in their `order_by`. So evidence has to be in the
# handle. But a judged finding's evidence also carries a PROBABILITY, and that moves whenever the
# model or the state moves -- hashing it would orphan every ruling on the next run, which is the
# exact bug this id exists to fix. Measurements are excluded: a float is a probability or a share,
# and reach is a property of the DAG rather than of the defect.
# `one_of_each` is a READING AID: one example per variant, so a reader sees what differs. It is
# rebuilt from the descriptions every run and must not move the finding's identity, which is what
# every ruling on it is filed under.
# `store` is what the store held when a config comment was checked against it: it moves every
# time somebody rules, and the finding is the same comment being wrong.
# `partition_as_written` is a READING AID too: the window's partition as the SQL spells it, beside
# the resolved column that identifies the finding. Kept out of the id so the rulings already filed
# on these findings stay where they are (25.9).
# `models` and `sharing` list a cluster's other members: a model joining the cluster later must not
# move the finding every member's ruling is filed under.
_MEASURED = frozenset({"downstream", "marts", "probability", "confidence", "one_of_each",
                       "store", "partition_as_written", "models", "sharing",
                       # A finding raised because the premise that held it back broke is the SAME
                       # finding it was before the premise held: a ruling on it still applies.
                       "why_it_is_back", "proven_rule"})


def _identity(evidence: dict) -> str:
    keep = {k: v for k, v in sorted((evidence or {}).items())
            if k not in _MEASURED and not isinstance(v, float)}
    return json.dumps(keep, sort_keys=True, default=str)


@dataclass
class Finding:
    check: str
    subject: str                    # unique_id
    subject_name: str
    file: str
    summary: str
    detail: str
    base: int = 2                   # 1 low, 2 medium, 3 high; the graph scales it
    # *** THE QUESTION WHOSE VERDICTS AUTHORIZE THIS FINDING. ***
    # Empty means the finding is structural: a parser decided it, there is no error rate to
    # measure, and it may gate immediately. A JUDGED finding must name the family it rests on,
    # because verdicts are recorded per QUESTION and a finding is not a question. Nine of ten
    # families authorized nothing while this was inferred from `check` instead: you could rule on
    # `column_role` all afternoon and no finding was ever named `column_role`, so the gate floor
    # was never satisfied and nobody could see why.
    rests_on: str = ""
    evidence: dict = field(default_factory=dict)
    descendants: int = 0
    marts: int = 0
    # What OUTSIDE the warehouse this reaches, by the project's own exposures. Never in the id:
    # declaring an exposure must not orphan a ruling.
    exposures: list = field(default_factory=list)

    @property
    def id(self) -> str:
        """A stable handle for THIS finding, which is finer than the model it is about.

        *** A VERDICT ON A MODEL LANDS ON EVERY FINDING THAT MODEL HAS. ***
        Reported from the field: `az_section_summary` carries EIGHT `test_cannot_fail` findings
        and `rule(subject, question)` could only say "this model, this check", so one keypress
        answered all eight. It is also how a correct finding got ruled wrong -- `dim_business` was
        read as a union false positive, which is true of four of its six edges, while two of them
        join on (geography, building_key) against a grain of (geography, business_key,
        building_key) and fan out 1.48x, measured at 69,966 rows over 47,178 pairs.
        `silently_multiplied` was right about those two and the ruling covered them anyway.

        Hashed over check, subject and summary rather than the run: a ruling has to survive the
        next run or it is not a ruling. The summary carries the column or the hop, which is what
        separates eight findings on one model.
        """
        raw = f"{self.check}|{self.subject}|{self.summary}|{_identity(self.evidence)}"
        return hashlib.sha1(raw.encode()).hexdigest()[:12]

    @property
    def weight(self) -> float:
        """base, lifted by reach. A defect feeding nine marts outranks the same defect on a leaf.

        *** WHAT REACHES A PRODUCT OUTRANKS WHAT REACHES A LAYER. ***
        An exposure is the project saying a model feeds something outside the warehouse. One is
        worth more than the whole marts-and-descendants lift (which tops out at 4), so an exposed
        finding ranks above an unexposed one of the same severity everywhere weight orders a list.
        """
        p = self.weight_parts()
        return self.base * p["lift"]

    def weight_parts(self) -> dict:
        """The weight, taken apart, so a surface can show where the number came from.

        *** "weight 7.6" WITH NO FORMULA ANYWHERE. *** Reported from the page. The weight is
        computed HERE and only here, from these parts, so a surface that shows the parts cannot
        disagree with the number it ranks by.
        """
        n_exp = len(self.exposures or ())
        d, m, x = min(self.descendants, 50), min(self.marts, 10), min(n_exp, 2)
        parts = {"descendants": d / 25, "marts": m / 5, "exposures": 5 * x}
        return {"base": self.base, "lift": 1 + sum(parts.values()), "parts": parts,
                "counts": {"descendants": self.descendants, "marts": self.marts,
                           "exposures": n_exp},
                "caps": {"descendants": 50, "marts": 10, "exposures": 2},
                "divisors": {"descendants": 25, "marts": 5}, "per_exposure": 5}


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


def _sharpen(expr: str, ev: dict) -> dict:
    """Add what the expression settles beyond 'this cannot fail'."""
    lit = default_literal(expr)
    if lit is not None:
        ev["coalesce_default"] = lit
        ev["what_to_do"] = (f"the guard is live and pointed at the wrong column: count how often "
                            f"the value IS {lit}. `assay tests --count-defaults` does it.")
    if cannot_fail_by_construction(expr):
        ev["cannot_fail_by"] = "construction"
        ev["what_to_do"] = ("a CASE with one branch and no ELSE yields exactly one value or NULL, "
                            "so this cannot fail whatever the data does -- stronger than the "
                            "general finding.")
    return ev


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
                    evidence=_sharpen(d.output_exprs.get(col, ""),
                                      {"test": t.name, "kind": t.kind, "column": col,
                                       "expression": d.output_exprs.get(col, "")[:160],
                                       "severity": t.severity}),
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
                        "from one point reorders them. Rank in a meter-based measure, or transform "
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
        # *** A BOX BUILT FROM STORED BOUNDS IS A TESSELLATION, NOT AN APPROXIMATED CIRCLE. ***
        # `ST_MakeEnvelope(cx0, cy0, cx1, cy1)` from columns on the same row IS the intended
        # geometry -- a grid cell -- and "a box is not a circle" only holds when the envelope
        # stands in for a radius. Both disagreements on a hand-ruled warehouse were this, and the
        # AST settles it: bare columns versus arithmetic on a point.
        kinds = {v for k, v in (d.bbox_corners or {}).items() if k in (in_join | in_where)}
        if kinds and kinds <= {"stored_bounds"}:
            continue
        if in_join or in_where:
            found.append(Finding(
                check="bbox_as_radius",
                subject=uid, subject_name=m.name, file=m.path,
                summary=f"bounding box used as a proximity filter ({', '.join(sorted(in_join | in_where))})",
                detail=("A box is not a circle. At Colorado's latitude a 0.02-degree box is about "
                        "2km east-west and 2.2km north-south, so the filter is anisotropic. Fine as "
                        "a prefilter before an exact test; wrong as the test itself."),
                base=1,
                evidence={"position": "join_condition" if in_join else "where",
                          "corners": sorted(kinds) or ["unknown"]},
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

    A tie-break is total when one of its keys is a column declared unique IN THE RELATION IT IS
    READ FROM: this model, or a parent it selects from. That is checkable against the project's
    own tests, with no data.

    *** ANY TABLE'S UNIQUE TEST USED TO EXCUSE ANY PICK WITH A COLUMN OF THE SAME NAME. ***
    Found by the premise ledger, which asks which relation an exemption leans on: on a 358-model
    project 10 of the 12 picks excused this way leaned on a test in an unrelated table --
    `stg_adwr_wells` ordered by `objectid` was excused by `stg_blm_co_mineral_estate`'s test on its
    own `objectid`. A name is not a column.
    """
    from .. import ledger
    from ..relate import declared_keys
    # Which relations declare each column unique on its own.
    declared_in: dict = {}
    for uid_, cols in declared_keys(project).items():
        if len(cols) == 1:
            declared_in.setdefault(cols[0], set()).add(uid_)
    for t in project.tests:
        if t.kind == "unique" and t.column and t.tests_model:
            declared_in.setdefault(t.column.lower(), set()).add(t.tests_model)

    def _pick_premise(led, rel, k):
        return ledger.unique(led, rel, [k])

    found = []
    for uid, d in digests.items():
        if not d.ok:
            continue
        m = project.models[uid]
        near = {uid, *(m.parents or [])}
        for w in d.windows:
            # only a dedupe: a ranking nobody filters on is a reported position, not a choice
            if not w.partition_columns or not w.order_sql:
                continue
            if not (d.has_qualify or any("rn" in p_.lower() or "= 1" in p_
                                         for p_ in d.predicates)):
                continue
            keys = [o.split()[0].split(".")[-1].strip("()").lower() for o in w.order_sql]
            back = None
            hit = next(((k, sorted(declared_in.get(k, set()) & near,
                                   key=lambda r: (r == uid, r))[0])
                        for k in keys if declared_in.get(k, set()) & near), None)
            if hit is not None:
                k, rel = hit
                # the last resort is a declared-unique column, and that is a premise
                back = ledger.hold("arbitrary_pick", uid, f"{','.join(w.partition_columns)}|{k}",
                                   lambda led, rel=rel, k=k: _pick_premise(led, rel, k),
                                   f"ties are broken by `{k}` while it is unique in "
                                   f"`{project.name_of(rel)}`")
                if back is None:
                    continue
            if back is None and getattr(w, "picks_only_keys", False):
                continue                       # a remaining tie is identical in all it keeps
            ev = {"partition_by": w.partition_columns, "order_by": w.order_sql[:3],
                  "partition_as_written": w.partition_by}
            if back:
                ev["why_it_is_back"] = back
            found.append(Finding(
                check="arbitrary_pick",
                subject=uid, subject_name=m.name, file=m.path,
                # *** THE PARTITION ALONE DOES NOT SEPARATE TWO OF THESE ON ONE MODEL. ***
                # `int_azcc_owners` carries two, both partitioned by `owner_key`, ordered by
                # `officer_name DESC, matched_name` and by `scraped_at DESC`. Two distinct
                # defects rendered as the same sentence twice, so a reader could not tell which
                # one they were ruling on -- and the id docstring says the summary is precisely
                # what separates eight findings on one model. The tie-break is what differs, so
                # the tie-break is in it.
                summary=(f"dedupe on {w.partition_columns} whose tie-break may not be total"
                         + (f", ordered by {', '.join(w.order_sql)}" if w.order_sql else "")),
                detail=("`row_number() ... = 1` keeps one row per partition. None of the ORDER BY "
                        "keys is declared unique in this model or a parent it reads, so ties are "
                        "broken by "
                        "whatever the engine returned, and the winner can change between builds "
                        "on identical data. Add a unique column as the last sort key."),
                base=2,
                # *** THE EVIDENCE NAMED A PARTITION THE WINDOW DOES NOT USE. *** (25.9)
                # `partition by matched_name` resolves forward to the output alias `owner_key`,
                # which is right for lineage and wrong for a reader: two of three windows in one
                # file read as `owner_key`, and one of them pointed at the wrong window. The
                # construct as written rides beside the resolved key.
                evidence=ev,
            ))
    return found


# *** PARSING A STRUCTURED STRING IS NOT PICKING FROM A LIST. ***
# The first version flagged any first-element access, and all 8 findings on a real project were
# deliberate parses: the street out of "123 Main St, Denver, CO", the prefix of a license number,
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


def _aggregate_input_cannot_be_null(uid: str, column: str, project, digests) -> bool:
    """True when the thing being aggregated can never be NULL, so the aggregate never is.

    Two exact signals, both free:

    - the argument is `COALESCE(x, <literal>)`. The same fact `test_cannot_fail` reads from the
      other side: a literal tail means the expression cannot produce NULL;
    - the argument is a column, and exactly ONE parent of this model declares a `not_null` test on
      a column of that name. One parent is an answer. Several publishing the name is genuinely
      ambiguous, and picking the first is the defect this file reports in other people's SQL, so
      an ambiguous name settles nothing and the finding stands.
    """
    d = digests.get(uid)
    expr = (getattr(d, "output_exprs", None) or {}).get((column or "").lower(), "") if d else ""
    if not expr:
        return False
    try:
        tree = sqlglot.parse_one(expr, dialect=getattr(project, "dialect", "duckdb"))
    except Exception:                                            # noqa: BLE001
        return False
    agg = tree if isinstance(tree, exp.AggFunc) else next(iter(tree.find_all(exp.AggFunc)), None)
    if agg is None:
        # The column is read out of a CTE, so the aggregate is not in this expression and there is
        # no argument to examine. No evidence is not evidence of safety.
        return False

    # A coalesce with a literal tail, anywhere in the aggregate's argument.
    for co in agg.find_all(exp.Coalesce):
        args = [co.this, *(co.expressions or [])]
        if args and isinstance(args[-1], exp.Literal):
            return True

    names = {c.name.lower() for c in agg.find_all(exp.Column) if c.name}
    if not names:
        return False
    for name in names:
        owners = [p for p in (project.models[uid].parents if uid in project.models else [])
                  if any(t.kind == "not_null" and (t.column or "").lower() == name
                         and t.tests_model == p for t in project.tests)]
        if len(owners) == 1:
            return True
    return False


def run_all(project, digests: dict[str, Digest], schema=None) -> list[Finding]:
    from .sources import SOURCE_CHECKS, source_freshness_stale
    out: list[Finding] = []
    for fn in CHECKS:
        out.extend(fn(project, digests))
    out.extend(variant_columns(project, digests, schema))
    # Completeness at the edge of the warehouse. Pure manifest, free, and in the SAME stream as
    # everything else so it reaches `check`, MCP, the store and the ruling loop by one path.
    for fn in SOURCE_CHECKS:
        out.extend(fn(project))
    out.extend(source_freshness_stale(project))
    # What a COLUMN is, according to the person who wrote it down. Pure manifest, free, and in
    # the same stream, so it reaches `check`, MCP, the store and the ruling loop by one path.
    from .columns import COLUMN_CHECKS
    for fn in COLUMN_CHECKS:
        out.extend(fn(project, digests, schema))
    for f in out:
        b = project.blast_radius(f.subject)
        f.descendants, f.marts = b["descendants"], b["marts"]
    return sorted(out, key=lambda f: -f.weight)


# *** "THIS TEST CANNOT FAIL" IS TRUE AND IS NOT THE ACTIONABLE SENTENCE. ***
# Reported after ruling on forty of these by hand: every one is correct as stated, and a `not_null`
# on a `COALESCE(x, <literal>)` is not a dead guard -- it is a LIVE guard pointed at the wrong
# column. On one warehouse:
#
#   water_rights.dwr_analysis_status   170,730 of 172,695  (99%) are the default
#   water_parcels.irrigated_acres    2,623,519 of 2,732,101 (96%) are 0
#
# The test passes on every row while saying nothing about whether any lookup ran, because the
# coalesce conflates "none" with "not measured". The share is one query, batched the same way
# `which_have_failures` already batches.
_COALESCE_DEFAULT = re.compile(
    r"COALESCE\s*\(.+?,\s*('(?:[^']|'')*'|-?\d+(?:\.\d+)?|TRUE|FALSE)\s*\)\s*$",
    re.IGNORECASE | re.DOTALL)


def default_literal(expression: str) -> str | None:
    """The literal a COALESCE falls back to, when the whole expression is that coalesce."""
    m = _COALESCE_DEFAULT.search((expression or "").strip())
    return m.group(1) if m else None


def cannot_fail_by_construction(expression: str) -> bool:
    """A CASE with one branch and no ELSE produces exactly one value or NULL.

    An `accepted_values` test on it cannot fail BY CONSTRUCTION rather than by today's data, which
    is a stronger statement than the general finding and was indistinguishable from it.
    """
    e = (expression or "").strip()
    if not re.match(r"^CASE\b", e, re.IGNORECASE):
        return False
    whens = len(re.findall(r"\bWHEN\b", e, re.IGNORECASE))
    has_else = bool(re.search(r"\bELSE\b", e, re.IGNORECASE))
    return whens == 1 and not has_else


def default_share_sql(rows: list, dialect: str = "duckdb") -> str:
    """One statement counting how often each default wins. `rows` is (relation, column, literal)."""
    parts = []
    for rel, col, lit in rows:
        parts.append(
            f"select '{rel}.{col}' as c, count(*) as n, "
            f"count(*) filter (where \"{col}\" is not distinct from {lit}) as d from {rel}")
    return " union all ".join(parts)


# ------------------------------------------------- the mirror of test_cannot_fail

def test_outruns_its_source(project, digests, schema=None, entries=None) -> list[Finding]:
    """A test asserting something the column it tests has no right to promise.

    *** `test_cannot_fail` IS THE ABSENCE DIRECTION. THIS IS THE OTHER ONE. ***
    That family answers "here is something nothing asserts" and is this project's most reliable,
    38 of 38 agreed. This answers "here is something asserted that was never true upstream", and
    it is the one a real outage produced:

        not_null on int_water_well_parcel.parcel_id, failed on ONE row of 49,034.
        The column is CARRIED from water_parcels, where 7,226 of 2,732,262 rows are null.

    The test could only ever have been one bad row from failing. It took a long time to find that
    row, and when it did the obvious repair was to drop a legitimately matched well -- fixing the
    data to protect an assertion the data never supported.

    *** AND THE FREE SIGNAL HAS TO BE EXACT, NOT MERELY SUGGESTIVE. ***
    The first version fired when the parent simply did not declare `not_null` on the column. That
    is 148 of 227 carried-column tests on a real warehouse -- 65%, because not declaring
    `not_null` on every parent column is ordinary practice rather than a defect. A check that
    fires on the normal case is the `209 of 573` shape, and it is how a list of exceptions becomes
    a list.

    So the free half is only the two cases where the column CAN be null by construction, both
    readable off the AST:

      - it is carried from a UNION arm that pads it with `CAST(NULL AS ...)`, so it is null for
        every row that arm contributes;
      - it is carried from a LEFT, RIGHT or FULL joined parent, so it is null for every driving
        row the join did not match. A `not_null` there is asserting the join always matches, which
        is a claim the join itself does not make;
      - it is produced by a NULL-PRESERVING AGGREGATE, so it is null for every group whose inputs
        are all null. This is the one the outage was, and it took carrying the exact AST root to
        see: the model above was rewritten to an INNER join after the incident, so the join half
        of this check is correctly silent on it -- and `parcel_id` is `min(parcel_id)` over a
        grouped CTE, which returns NULL for a group where every parcel has a null id, and the
        INNER join keeps that row because it joins on `addr_key`, not on `parcel_id`. The repair
        moved the nullability; it did not remove it.

        `count` is the exception and it is the common case: `count(x)` over a group is 0, never
        NULL. On the field warehouse that is 16 of the 23 aggregated `not_null` tests, so telling
        the aggregates apart is the difference between 7 findings and 23.

    Whether the parent actually CONTAINS nulls is a count, and counts arrive with `--verify` like
    every other counted check here -- absent rather than guessed.
    """
    # *** WHICH AGGREGATES CANNOT RETURN NULL. ***
    # Every other aggregate returns NULL for an all-NULL group, and a GROUP BY still emits that
    # row. Listed as the exceptions rather than the rule, because a new aggregate nobody has
    # thought about should read as null-preserving -- the safe direction for a check that says
    # "this assertion may not hold".
    NEVER_NULL = {"count", "count_if", "countif", "count_distinct", "approx_count_distinct"}

    # *** AND AN AGGREGATE WHOSE INPUT CANNOT BE NULL CANNOT RETURN NULL. ***
    # Ruled on all seven of this check's own findings by reading the SQL and then counting the
    # parents. Three were wrong, all for one reason: the thing being aggregated is never NULL, so
    # the group can never be entirely NULL.
    #
    #   bool_or(is_sfha)                    stg_fema_flood_zones.is_sfha carries `not_null`
    #   bool_or(is_acquired)                stg_cwcb_isf.is_acquired     carries `not_null`
    #   listagg(coalesce(use_label, '...')) a literal tail; the argument is `defaulted`
    #
    # against the two that are right, where the input carries no such test and does hold nulls:
    #
    #   min(letter_date)   1,300 null of 17,193, and 1,300 groups entirely null
    #   min(parcel_id)     5,876 null of 2,732,101   <- the outage
    #
    # Both signals are free and exact: a `not_null` test is a declaration in the manifest, and a
    # coalesce with a literal tail is the same thing `test_cannot_fail` reads in the other
    # direction. Where the expression does not carry the aggregate at all -- the column is read
    # out of a CTE -- there is no argument to check and the finding stands, which is the safe
    # direction for a check whose claim is "this assertion may not hold".
    out = []
    if entries is None:
        return out
    by_uid = {e.uid: e for e in entries}

    for t in project.tests:
        if t.kind != "not_null" or not t.column or not t.tests_model:
            continue
        e = by_uid.get(t.tests_model)
        if e is None:
            continue
        ce = next((c for c in e.columns if c.name.lower() == t.column.lower()), None)
        if ce is None or ce.provenance is None:
            continue
        # The RELATION it was read from, not the prose explaining the class.
        origin = str(getattr(ce.provenance, "origin", "") or "")
        note = str(ce.provenance.note or "")

        # *** ONLY THE TWO CASES WHERE THE COLUMN CAN BE NULL BY CONSTRUCTION. ***
        parent_name, why = "", ""
        if ce.provenance.value == "null_placeholder":
            parent_name = "a union arm"
            why = ("this column is padded with `CAST(NULL AS ...)` in a UNION arm, so it is NULL "
                   "for every row that arm contributes. The test cannot pass while that arm "
                   "produces rows.")
        elif ce.provenance.value == "carried":
            for pname, kind in sorted((e.join_kind or {}).items()):
                if (kind and kind != "INNER"
                        and (pname.lower() in origin.lower()
                             or origin.lower().endswith("." + pname.lower()))):
                    parent_name = pname
                    why = (f"carried from `{pname}`, which is {kind} JOINed here -- so it is NULL "
                           f"for every driving row that join does not match. This test asserts "
                           f"the join always matches, which the join itself does not claim.")
                    break
        elif ce.provenance.value == "aggregated":
            fn = str(getattr(ce.provenance, "root", "") or "").split(":", 1)[-1].lower()
            if fn and fn not in NEVER_NULL and not _aggregate_input_cannot_be_null(
                    e.uid, t.column, project, digests):
                parent_name = f"{fn}()"
                why = (f"produced by `{fn}()`, which returns NULL for any group where every "
                       f"input row is NULL -- and the GROUP BY still emits that row. The test "
                       f"asserts no group is ever entirely empty of a value, which is a claim "
                       f"about the data, not about the aggregate.")
        if not why:
            continue

        out.append(Finding(
            check="test_outruns_its_source", subject=e.uid, subject_name=e.name,
            file=e.path, base=2,
            summary=f"`not_null` on {e.name}.{t.column}, which can be NULL by construction: "
                    f"{parent_name}",
            detail=(f"{why}\n\nThe failure mode is not a red build. It is a test that passes for "
                    "years and then fails on ONE row, long after the code that could explain it "
                    "was written -- and the obvious repair at that point is to delete the row, "
                    "which fixes the data to protect an assertion the data never supported.\n\n"
                    "Measured in the field: `not_null` on `int_water_well_parcel.parcel_id` "
                    "failed on one row of 49,034, and the column is carried from a parent where "
                    "7,226 of 2,732,262 rows are null. It could only ever have been one bad row "
                    "from failing.\n\nEither assert it where the value is produced, or stop "
                    "asserting it here.\n\n" + note),
            evidence={"column": t.column, "carried_from": parent_name, "test": t.name,
                      "provenance": ce.provenance.value}))
    return out
