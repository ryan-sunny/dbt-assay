"""A measure summed as a floating-point number. (G-C)

*** THE SAME DATA, TWO BUILDS, TWO TOTALS. ***
Floating-point addition is not associative: `(a + b) + c` and `a + (b + c)` can differ in the last
bits, and a warehouse adds in whatever order its threads and partitions arrive. So `sum(amount)`
over a DOUBLE column is not reproducible: a rebuild on identical rows can move the total, a test
comparing it to a snapshot flickers, and two marts that should reconcile disagree by a cent. It
has bitten this project's own warehouse (a DOUBLE average moved between builds). The fix is one
cast, `sum(cast(amount as decimal(18, 2)))`, which makes the addition exact.

Fires when all three hold:
- the output column is built by `sum` or `avg` over an input whose type is floating point (the
  catalog, then the manifest; a bare `sum` with neither falls back to its own output type);
- its role is `measure` (judged); a column whose role nobody judged is counted, never flagged;
- the model is a mart or has one downstream.

A type that could not be read is NOT a pass. Every sum whose input type is unknown is counted in
`UNREAD` and `check` says how many, so a project with no catalog is told the check was blind.
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp

from .structural import Finding

FLOAT_TYPES = ("double", "float", "real", "float4", "float8", "float64", "double precision",
               "binary_double", "binary_float")
EXACT_TYPES = ("decimal", "numeric", "number", "int", "integer", "bigint", "smallint",
               "tinyint", "hugeint", "int64", "int2", "int4", "int8", "money", "bignumeric")

# What the latest run could not read, for `check` to say: [(reason, count)].
UNREAD: list = []


def _kind(t: str) -> str:
    """float | exact | unknown, from a declared type."""
    from ..cost import normalize_type
    n = normalize_type(t or "")
    if not n:
        return "unknown"
    if n in FLOAT_TYPES or n.startswith("float") or n.startswith("double"):
        return "float"
    if n in EXACT_TYPES or n.startswith("decimal") or n.startswith("numeric") \
            or n.startswith("number") or n.endswith("int"):
        return "exact"
    return "unknown"


def _types_by_relation(project, schema) -> dict:
    """{relation without quotes, lowercased: {column: type}} from the catalog then the manifest."""
    from ..cost import declared_types
    out = {}
    for rel, cols in declared_types(project, schema).items():
        out[rel.replace('"', "").replace("`", "").lower()] = cols
    return out


def _agg(node):
    """The SUM or AVG this expression is, under any casts wrapped around it, or None."""
    outer_cast = None
    while isinstance(node, (exp.Cast, exp.TryCast, exp.Paren)):
        if isinstance(node, (exp.Cast, exp.TryCast)) and outer_cast is None:
            outer_cast = node
        node = node.this
    if isinstance(node, exp.Filter):
        node = node.this
    if isinstance(node, (exp.Sum, exp.Avg)):
        return node, outer_cast
    return None, None


def _arg_type(arg, model_uid, project, digest_, types, schema) -> tuple[str, str]:
    """(type, where it came from) of what the aggregate adds up, or ("", why not)."""
    while isinstance(arg, exp.Paren):
        arg = arg.this
    if isinstance(arg, (exp.Cast, exp.TryCast)):
        to = arg.args.get("to")
        return (to.sql() if to is not None else ""), "the cast in the SQL"
    if not isinstance(arg, exp.Column):
        return "", "the input is an expression, not a column"
    name = arg.name.lower()
    qual = (arg.table or "").lower()
    rels = []
    rel = (digest_.alias_relation or {}).get(qual) if qual else None
    if rel:
        rels.append(str(rel).replace('"', "").lower())
    m = project.models.get(model_uid)
    for p in (m.parents if m is not None else []):
        r = (schema.relation.get(p) or "").replace('"', "").lower()
        if r:
            rels.append(r)
    found = {}
    for r in rels:
        cols = types.get(r) or next((v for k, v in types.items()
                                     if k.endswith("." + r) or r.endswith("." + k)), None)
        if cols and cols.get(name):
            found[r] = cols[name]
    kinds = {_kind(t) for t in found.values()}
    if len(kinds) == 1 and kinds != {"unknown"}:
        r, t = next(iter(found.items()))
        return t, f"`{r.split('.')[-1]}`'s declared type"
    return "", "the input column's type was not read"


def float_sum_is_not_reproducible(project, digests, schema, entries) -> list:
    UNREAD.clear()
    types = _types_by_relation(project, schema)
    by_uid = {e.uid: e for e in entries or []}
    dialect = getattr(project, "dialect", "duckdb") or "duckdb"
    unread = unjudged = 0
    out = []
    for uid, d in sorted(digests.items()):
        e = by_uid.get(uid)
        if e is None or not d.ok:
            continue
        if not (e.layer == "marts" or (e.marts or 0) > 0):
            continue
        roles = {c.name: (c.role.value if c.role else None) for c in e.columns}
        own = types.get((schema.relation.get(uid) or "").replace('"', "").lower()) or {}
        for col, root in sorted((d.output_roots or {}).items()):
            if root not in ("agg:sum", "agg:avg"):
                continue
            sql = (d.output_exprs or {}).get(col)
            try:
                node = sqlglot.parse_one(sql, dialect=dialect) if sql else None
            except Exception:                                    # noqa: BLE001
                node = None
            if node is None:
                unread += 1               # the expression could not be read back
                continue
            agg, outer = _agg(node)
            if agg is None:
                continue
            t, came = _arg_type(agg.this, uid, project, d, types, schema)
            if not t and isinstance(agg, exp.Sum) and outer is None and own.get(col.lower()):
                # A sum's own output type says how it added: DOUBLE only when its input was.
                t, came = own[col.lower()], "the column's own type (a sum returns its input's)"
            kind = _kind(t)
            if kind == "unknown":
                unread += 1
                continue
            if kind != "float":
                continue
            role = roles.get(col.lower())
            if role is None:
                unjudged += 1
                continue
            if role != "measure":
                continue
            fn = "sum" if isinstance(agg, exp.Sum) else "avg"
            what = agg.this.sql(dialect=dialect)
            out.append(Finding(
                check="float_sum_is_not_reproducible",
                subject=uid, subject_name=e.name, file=e.path,
                summary=f"`{col}` is a {fn} over a floating-point input",
                detail=(f"`{col}` is `{fn}({what})`, and `{what}` is `{t}`. Floating-point "
                        f"addition is not associative, so the total depends on the order the "
                        f"rows are added in, which the warehouse does not fix: it can move "
                        f"between builds on identical data. Cast before aggregating: "
                        f"`{fn}(cast({what} as decimal(18, 2)))`."),
                base=2,
                evidence={"column": col, "aggregate": fn, "input": what, "input_type": t,
                          "type_from": came,
                          "recommendation": f"{fn}(cast({what} as decimal(18, 2)))"},
                descendants=e.descendants, marts=e.marts,
            ))
    if unread:
        UNREAD.append((f"{unread} sum/avg column(s) whose expression or input type could not be "
                       f"read, so `float_sum_is_not_reproducible` could not look at them."
                       + ("" if schema.catalog_present else
                          " There is no catalog.json: `dbt docs generate` writes one, with "
                          "every column's type."), unread))
    if unjudged:
        UNREAD.append((f"{unjudged} floating-point sum/avg column(s) whose role was never "
                       f"judged, so not flagged: only a `measure` is. `assay columns` judges "
                       f"roles.", unjudged))
    return out
