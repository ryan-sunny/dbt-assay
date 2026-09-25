"""Shapes in a model's SQL that are wrong often enough to count, read from the one parse.

Each is a fact about the text, never a judgment: where the model reads the clock, an aggregate
whose output depends on row order, a join key normalised on one side only, `NOT IN (subquery)`, a
LEFT JOIN a WHERE turns back into an inner join, and a LIMIT. Whether a fact is a defect depends on
more than the text (is the clock window meant, can the subquery return NULL), and the check that
reads these facts says which.

(sunny-data sweep, 2026-09-25: 17 models read the clock, 26 order-sensitive aggregates, 11
one-sided keys, 9 NOT IN, 4 undone LEFT JOINs, 3 LIMITs.)
"""
from __future__ import annotations

from sqlglot import exp

# Functions that read the clock, by the name a dialect spells them when sqlglot has no node.
CLOCK_NAMES = {"now", "getdate", "getutcdate", "sysdate", "systimestamp", "sysdatetime",
               "today", "current_date", "current_timestamp", "localtimestamp", "localtime",
               "transaction_timestamp", "statement_timestamp", "clock_timestamp", "utc_timestamp",
               "utc_date", "curdate", "curtime"}
CLOCK_NODES = (exp.CurrentDate, exp.CurrentTimestamp, exp.CurrentTime, exp.CurrentDatetime,
               exp.Localtimestamp)
# Aggregates whose result depends on the order rows arrive in, unless an ORDER BY is given.
ORDERED_AGGS = (exp.GroupConcat, exp.ArrayAgg, exp.First, exp.Last, exp.AnyValue,
                exp.ArrayUniqueAgg)
NORMALISERS = (exp.Lower, exp.Upper, exp.Trim)


def _sql(e, dialect: str) -> str:
    try:
        return e.sql(dialect=dialect)[:240]
    except Exception:                                            # noqa: BLE001
        return type(e).__name__


def _where_in(node) -> str:
    """filter | join | window | projection | order: where in its select a node sits."""
    p = node.parent
    while p is not None:
        if isinstance(p, exp.Window):
            return "window"
        if isinstance(p, (exp.Where, exp.Having, exp.Qualify)):
            return "filter"
        if isinstance(p, exp.Join):
            return "join"
        if isinstance(p, exp.Order):
            return "order"
        if isinstance(p, exp.Select):
            return "projection"
        p = p.parent
    return "projection"


def _clause(n):
    """The smallest thing a person reads as one step: the comparison, the projected column, or
    the window bound the clock sits in."""
    p = n
    while p.parent is not None and not isinstance(
            p.parent, (exp.Where, exp.Having, exp.Qualify, exp.Select, exp.Join, exp.And,
                       exp.Or, exp.Window, exp.Order, exp.Group)):
        p = p.parent
    return p


def _is_clock(n) -> bool:
    if isinstance(n, CLOCK_NODES):
        return True
    return isinstance(n, exp.Anonymous) and str(n.name).lower() in CLOCK_NAMES


def _future_guard(node) -> bool:
    """`x <= current_date`, `year(d) <= year(current_date)`, `d between '1900-01-01' and now()`:
    a bound against future-dated rows. Legitimate, and it only changes which rows pass when the
    data holds dates in the future, so it is not the time dependence this check is for."""
    child, p = node, node.parent
    # up through what wraps the clock without adding a column: casts, year(), date_trunc(), ...
    while p is not None and (isinstance(p, (exp.Cast, exp.TryCast, exp.Paren))
                             or (isinstance(p, exp.Func) and not isinstance(p, exp.AggFunc)
                                 and not any(True for _ in p.find_all(exp.Column)))):
        child, p = p, p.parent
    if isinstance(p, exp.Between):
        return p.args.get("high") is child
    if isinstance(p, (exp.LTE, exp.LT)):
        return p.expression is child
    if isinstance(p, (exp.GTE, exp.GT)):
        return p.this is child
    return False


def _cols(e) -> list[str]:
    return sorted({c.name.lower() for c in e.find_all(exp.Column) if c.name})


def _normalisers(e) -> set:
    return {type(x).__name__.lower() for x in e.find_all(*NORMALISERS)}


def facts(tree, dialect: str = "duckdb") -> list[dict]:
    out: list[dict] = []
    # --- the clock
    for n in tree.find_all(exp.Expression):
        if _is_clock(n):
            out.append({"kind": "clock", "sql": _sql(_clause(n), dialect),
                        "where": _where_in(n), "guard": _future_guard(n)})
    # --- order-sensitive aggregates, outside a window with its own ORDER BY
    for n in tree.find_all(*ORDERED_AGGS):
        if isinstance(n.parent, exp.Window):
            continue
        ordered = bool(n.find(exp.Order)) or isinstance(n.parent, exp.WithinGroup)
        if ordered:
            continue
        # any_value over a column the query groups by is exact; the rest is a pick
        out.append({"kind": "order_sensitive_aggregate", "sql": _sql(n, dialect),
                    "function": type(n).__name__.lower()})
    # --- joins: one-sided normalisation, and a LEFT JOIN undone by the WHERE
    for sel in tree.find_all(exp.Select):
        where = sel.args.get("where")
        for j in sel.args.get("joins") or []:
            on = j.args.get("on")
            if on is not None:
                for eq in on.find_all(exp.EQ):
                    a, b = eq.this, eq.expression
                    if a is None or b is None or not _cols(a) or not _cols(b):
                        continue
                    na, nb = _normalisers(a), _normalisers(b)
                    if na != nb:
                        out.append({"kind": "one_sided_normalisation", "sql": _sql(eq, dialect),
                                    "left": sorted(na), "right": sorted(nb)})
            if str(j.args.get("side") or "").lower() != "left" or where is None:
                continue
            alias = (j.this.alias_or_name if isinstance(j.this, (exp.Table, exp.Subquery))
                     else "").lower()
            if not alias:
                continue
            for c in _conjuncts(where.this):
                refs = {col.table.lower() for col in c.find_all(exp.Column) if col.table}
                if alias not in refs:
                    continue
                if isinstance(c, exp.Is) or c.find(exp.Is) or c.find(exp.Or) or c.find(exp.Coalesce):
                    continue           # `r.x is null`, or a condition that admits the NULL row
                out.append({"kind": "left_join_undone", "sql": _sql(c, dialect),
                            "joined": alias})
    # --- NOT IN (subquery)
    for n in tree.find_all(exp.Not):
        inn = n.this
        if isinstance(inn, exp.Paren):
            inn = inn.this
        if isinstance(inn, exp.In) and inn.args.get("query") is not None:
            q = inn.args["query"]
            q = q.this if isinstance(q, exp.Subquery) else q
            col = ""
            src = ""
            if isinstance(q, exp.Select) and q.expressions:
                col = (q.expressions[0].alias_or_name or "").lower()
                f = q.args.get("from_") or q.args.get("from")
                if f is not None and isinstance(f.this, exp.Table):
                    src = f.this.name.lower()
            out.append({"kind": "not_in_subquery", "sql": _sql(n, dialect)[:240],
                        "column": col, "from": src})
    # --- LIMIT
    # --- UNION without ALL: which is meant is intent, asked by `union_should_collapse_duplicates`
    for n in tree.find_all(exp.Union):
        if n.args.get("distinct"):
            arms = []
            for side in (n.this, n.expression):
                rels = sorted({t.name.lower() for t in side.find_all(exp.Table) if t.name})
                arms.append(", ".join(rels[:4]) or _sql(side, dialect)[:80])
            out.append({"kind": "union_distinct", "sql": _sql(n, dialect)[:200], "arms": arms})
    # an ordered LIMIT inside a subquery is a top-N pick on purpose; one capping the whole model,
    # or any without an ORDER BY, is worth saying
    for n in tree.find_all(exp.Limit):
        sel = n.parent
        ordered = isinstance(sel, (exp.Select, exp.Union)) and sel.args.get("order") is not None
        if ordered and sel is not tree:
            continue
        out.append({"kind": "limit", "sql": _sql(n, dialect), "ordered": ordered})
    return out


def _conjuncts(e) -> list:
    if isinstance(e, exp.And):
        return _conjuncts(e.this) + _conjuncts(e.expression)
    if isinstance(e, exp.Paren):
        return _conjuncts(e.this)
    return [e]
