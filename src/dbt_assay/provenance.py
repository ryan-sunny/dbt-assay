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


def _root_class(root: str) -> str | None:
    if root == "literal":
        return "constant"
    if root == "coalesce:literal":
        return "defaulted"
    if root.startswith("window:"):
        return "ranked"
    if root.startswith("agg:"):
        return "aggregated"
    if root.startswith("func:") or root in ("case", "coalesce"):
        return "computed"
    if root == "column":
        return None                  # passes through: which parent decides carried vs from_source
    return None


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
            out[c] = ColumnProvenance(c, kind, EXPLAIN[kind])
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

        owner = schema.uid_of.get((origin or "").lower())
        if owner and owner in project.sources:
            out[c] = ColumnProvenance(c, "from_source", EXPLAIN["from_source"], origin)
        elif owner or root == "column":
            out[c] = ColumnProvenance(c, "carried", EXPLAIN["carried"], origin)
        else:
            out[c] = ColumnProvenance(c, "unknown", EXPLAIN["unknown"], origin)
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
