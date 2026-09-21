"""Where each column's value came from. No judgment required.

*** "WHERE DID THIS NUMBER COME FROM" IS THE FIRST QUESTION ANY STAKEHOLDER ASKS, AND NO WAREHOUSE
CAN ANSWER IT. ***
It looks like a meaning question and it is almost entirely a structural one. A column either passes
through unchanged, or it was computed, aggregated, defaulted, ranked or fixed here -- and the AST
plus the DAG says which. So this family is CODE, and asking a model would spend tokens to learn what
a parser already knows.

The one thing structure cannot settle is what happened OUTSIDE dbt. A column arriving from a source
is `from_source` and nothing in the project can say whether a human typed it, a vendor sent it, or a
model inferred it. That limit is reported as itself rather than guessed at, and it is the same
boundary the probe exists to work at.
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

# Ordered: the FIRST class that applies wins, because a coalesce over an aggregate is a defaulted
# aggregate and calling it merely "aggregated" loses the part that changes how it reads.
CLASSES = (
    "constant",          # a literal; the same for every row
    "null_placeholder",  # CAST(NULL AS t): this arm of a union has nothing to put here
    "defaulted",         # coalesce/ifnull with a literal tail: NULL was replaced HERE
    "ranked",            # a window function assigned it
    "aggregated",        # an aggregate, here or resolved upstream
    "computed",          # a function, CASE or arithmetic applied here
    "carried",           # passes through unchanged from another MODEL
    "from_source",       # passes through unchanged from a SOURCE: origin is outside dbt
    "unknown",
)

EXPLAIN = {
    "constant": "a literal in the select list; identical for every row",
    "null_placeholder": "CAST(NULL AS ...) -- this arm of a union has no value for the column, so "
                        "it is NULL for every row the arm contributes, and a downstream reader "
                        "cannot tell that from a value that is missing",
    "defaulted": "NULL was replaced with a literal here, so a downstream null test cannot fire",
    "ranked": "assigned by a window function; it describes position, not the entity",
    "aggregated": "an aggregate over several rows; it cannot identify one",
    "computed": "derived here by an expression",
    "carried": "passes through unchanged from an upstream model",
    "from_source": "arrives from a source; what produced it is outside this project",
    "unknown": "assay could not resolve where this came from",
}


@dataclass
class ColumnProvenance:
    column: str
    kind: str
    evidence: str = ""
    origin: str | None = None        # the relation it was last read from, when it passes through


# *** EVERY ROOT NAMES AN OPERATION, SO A ROOT IS NEVER "UNKNOWN". ***
# Measured on a 358-model warehouse: 233 columns read `unknown` while carrying a perfectly good
# root -- `filter` 91, `null` 34, `ignorenulls` 20, `paren` 19, then a long tail of `gt`, `not`,
# `dpipe`, `div`, `like`, `subquery`. The parser had already answered and the classifier had no
# case for the answer, so it reported "could not resolve where this came from" about an expression
# it was holding in its hand.
#
# `unknown` now means one thing only: the value PASSES THROUGH and nothing says from where.
_PASSES_THROUGH = ("column", "star")


def _root_class(root: str) -> str | None:
    if root == "literal":
        return "constant"
    # A NULL cast is how a union arm pads a column it has no value for. That is not a constant
    # anybody chose and it is not unknown: it is a hole with a type, and a reader downstream
    # cannot tell it from a value that went missing.
    if root in ("null", "cast:null"):
        return "null_placeholder"
    if root == "coalesce:literal":
        return "defaulted"
    if root.startswith("window:") or root == "ignorenulls":
        return "ranked"
    # `FILTER (WHERE ...)` is an aggregate's own clause, so the root is the aggregate.
    if root.startswith("agg:") or root == "filter":
        return "aggregated"
    if root in _PASSES_THROUGH or not root:
        return None                  # which parent decides carried vs from_source
    # *** THE CATCH-ALL NAMES WHAT IT CAUGHT. ***
    # A fallback that silently swallows every shape it was not told about is how a class stops
    # meaning anything. The root travels in the evidence, so a new sqlglot node type shows up as
    # `computed (paren)` and can be given its own case rather than disappearing into the pile.
    return "computed"


def classify(uid: str, project, digests, schema, dialect: str | None = None
             ) -> dict[str, ColumnProvenance]:
    dialect = dialect or getattr(project, "dialect", "duckdb")
    d = digests.get(uid)
    if not d or not d.ok:
        return {}
    roots = {**(d.output_roots or {}), **(d.resolved_roots or {}), **schema.columns(uid).roots}

    out: dict[str, ColumnProvenance] = {}
    for col in schema.columns(uid).names:
        c = col.lower()
        root = str(roots.get(c, ""))
        kind = _root_class(root)
        if kind:
            # The catch-all names what it caught, so a shape nobody has given a case to is
            # visible rather than absorbed.
            why = EXPLAIN[kind]
            if kind == "computed" and not root.startswith("func:") and root not in ("case",
                                                                                    "coalesce"):
                why = f"{why} ({root})"
            out[c] = ColumnProvenance(c, kind, why)
            continue

        # It passes through. WHICH relation it came from decides whether the origin is still
        # visible inside this project or has left it.
        origin, expr = None, d.output_exprs.get(c, "")
        if expr:
            try:
                e = sqlglot.parse_one(expr, dialect=dialect)
            except Exception:                                    # noqa: BLE001
                e = None
            if isinstance(e, exp.Column) and e.table:
                origin = d.alias_relation.get(e.table.lower())
        if not origin and len(d.from_relations) == 1:
            origin = d.from_relations[0]

        # *** A STAR GIVES US THE NAMES AND LOSES WHERE EACH ONE CAME FROM. ***
        # `select *` over six relations expands to 73 column names and ONE root, `star`, so every
        # passed-through column read `unknown` -- 236 of them on the field warehouse, 71 of the 73
        # on one mart. The parents' column lists are already assembled for sqlglot, so the parent
        # that offers the name is a lookup rather than a guess.
        #
        # AND IT ONLY ANSWERS WHEN EXACTLY ONE PARENT OFFERS IT. Two parents publishing `wdid` is
        # genuinely ambiguous, and picking the first is the defect this tool checks other people's
        # SQL for. The ambiguity is reported as itself.
        ambiguous: list = []
        if not origin and root in ("star", ""):
            owners = _parents_offering(uid, c, project, schema)
            if len(owners) == 1:
                origin = schema.relation.get(owners[0]) or origin
            elif len(owners) > 1:
                ambiguous = [project.name_of(o) for o in owners]

        owner = schema.uid_of.get((origin or "").lower())
        if owner and owner in project.sources:
            out[c] = ColumnProvenance(c, "from_source", EXPLAIN["from_source"], origin)
        elif owner or root == "column":
            out[c] = ColumnProvenance(c, "carried", EXPLAIN["carried"], origin)
        elif ambiguous:
            out[c] = ColumnProvenance(
                c, "unknown",
                f"a `select *` carries it and {len(ambiguous)} parents publish this name "
                f"({', '.join(sorted(ambiguous)[:4])}), so which one it is cannot be settled "
                f"from the SQL. Name the columns, or qualify the star.", origin)
        else:
            out[c] = ColumnProvenance(c, "unknown", EXPLAIN["unknown"], origin)
    return out


def _parents_offering(uid: str, col: str, project, schema) -> list[str]:
    """Every parent of `uid` that publishes a column called `col`.

    One is an answer. Several is an ambiguity to report, never a list to pick from.
    """
    out = []
    for p in (project.models[uid].parents if uid in project.models else []):
        try:
            names = {n.lower() for n in schema.columns(p).names}
        except Exception:                                        # noqa: BLE001, S112
            continue
        if col in names:
            out.append(p)
    return out


def trace(col: str, uid: str, project, digests, schema, max_hops: int = 12) -> list[tuple]:
    """Follow one column back through the DAG until it stops passing through.

    This is the answer to "where did this number come from", assembled by code from facts, and it
    stops at the first hop that DID something to the value rather than guessing further.
    """
    hops: list[tuple] = []
    cur_uid, cur_col = uid, col.lower()
    for _ in range(max_hops):
        p = classify(cur_uid, project, digests, schema).get(cur_col)
        if not p:
            break
        hops.append((project.name_of(cur_uid), cur_col, p.kind, p.evidence))
        if p.kind not in ("carried",):
            break
        nxt = schema.uid_of.get((p.origin or "").lower())
        if not nxt or nxt == cur_uid:
            break
        d = digests.get(nxt)
        # the name may change at the boundary: follow the alias the parent publishes it under
        if d and cur_col not in {x.lower() for x in d.output_columns}:
            cur_col = next((k for k, v in (d.alias_of or {}).items() if v == cur_col), cur_col)
        cur_uid = nxt
    return hops
