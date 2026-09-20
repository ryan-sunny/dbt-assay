"""Structural facts from compiled SQL, via sqlglot.

*** IF A PARSER CAN ANSWER IT, NEVER ASK A MODEL. ***
Everything in here is exact. It is the half of the tool that costs nothing, needs no API key, and
cannot hallucinate. A judgment is only reached for meaning the AST does not carry.

*** POSITION IS THE WHOLE POINT. ***
A defect is rarely "this function appears". It is "this function appears HERE". Measured on a real
model: `ST_Distance` (degrees) sits in a projection where it is only reported, while the ranking
orders by `ST_Distance_Sphere` (metres) and is correct. A text search flags that model; the AST
clears it. Four hand-written static guards failed on this exact distinction before -- one missed a
sort on an ALIAS, one ran a character window past the clause -- and an AST has neither failure mode.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

# Two models in a 295-model project blew the default limit: machine-generated SQL with thousands of
# UNIONed literal rows. Raising it is cheaper than failing, and the cap keeps a pathological file
# from taking the process down with it.
_RECURSION = 20_000

CLAUSE = (
    (exp.Ordered, "order_key"),
    (exp.Join, "join_condition"),
    (exp.Where, "where"),
    (exp.Group, "group_by"),
    (exp.Having, "having"),
    (exp.Qualify, "qualify"),
)


def func_name(node: exp.Expression) -> str:
    """ALWAYS UPPER CASE.

    sqlglot returns `sql_name()` (already upper) for a function it models natively and the verbatim
    source spelling for an `Anonymous` one, so `ST_Distance` and `ST_MakeEnvelope` came back in
    different cases from the same query. A caller comparing against a constant set would then match
    one and miss the other, which is a silent miss rather than an error. Normalised here so no
    caller has to remember.
    """
    if isinstance(node, exp.Anonymous):
        return node.name.upper()
    try:
        return node.sql_name().upper()
    except Exception:                                                   # noqa: BLE001
        return type(node).__name__.upper()


def _relname(t: exp.Table) -> str:
    return ".".join(p for p in (t.catalog, t.db, t.name) if p)


def _from_of(sel: exp.Select):
    """sqlglot renamed this arg from `from` to `from_` at v30.

    Reading only the new key returns None on older sqlglot, which is a SILENT miss: the FROM
    relation simply vanishes and every check downstream behaves as though the model joined nothing.
    The package supports sqlglot>=25, so both spellings are read.
    """
    return sel.args.get("from_") or sel.args.get("from")


def position_of(node: exp.Expression) -> str:
    """The nearest enclosing clause. 'projection' when the node is in a select list."""
    anc = node.parent
    while anc is not None:
        for cls, label in CLAUSE:
            if isinstance(anc, cls):
                return label
        anc = anc.parent
    return "projection"


def root_of(expr: exp.Expression) -> str:
    """What an ORDER BY key is really sorting on, after the alias and the wrapping are stripped."""
    e = expr.this if isinstance(expr, exp.Ordered) else expr
    if isinstance(e, exp.Func):
        return func_name(e)
    if isinstance(e, exp.Column):
        return "column"
    return type(e).__name__.lower()



def _classify(e: exp.Expression) -> str:
    """What an output column ROOTS IN. Drives the tests-that-cannot-fail checks."""
    if isinstance(e, exp.Window):
        fn = e.this
        return f"window:{func_name(fn).lower()}" if isinstance(fn, exp.Expression) else "window"
    if isinstance(e, exp.Coalesce):
        args = [e.this] + list(e.expressions or [])
        # A COALESCE whose LAST argument is a literal can never return NULL, which is what makes a
        # `not_null` test on it incapable of failing.
        return "coalesce:literal" if isinstance(args[-1], exp.Literal) else "coalesce"
    if isinstance(e, exp.Case):
        return "case"
    if isinstance(e, exp.Literal):
        return "literal"
    if isinstance(e, exp.Cast):
        return _classify(e.this)
    if isinstance(e, exp.AggFunc):
        return f"agg:{func_name(e).lower()}"
    if isinstance(e, exp.Func):
        return f"func:{func_name(e).lower()}"
    if isinstance(e, exp.Column):
        return "column"
    return type(e).__name__.lower()


def case_branch_values(sql_expr: str, dialect: str = "duckdb") -> list[str] | None:
    """Every literal a CASE can return, or None if it can return something non-literal."""
    try:
        e = sqlglot.parse_one(sql_expr, dialect=dialect)
    except Exception:                                                   # noqa: BLE001
        return None
    if not isinstance(e, exp.Case):
        return None
    outs = [i.args.get("true") for i in e.args.get("ifs", [])]
    default = e.args.get("default")
    outs.append(default if default is not None else exp.Null())
    vals = []
    for o in outs:
        if isinstance(o, exp.Literal):
            vals.append(o.this)
        elif isinstance(o, exp.Null):
            vals.append(None)
        else:
            return None
    return vals


@dataclass
class JoinFact:
    kind: str                       # INNER | LEFT | RIGHT | FULL | CROSS
    lateral: bool
    target: str                     # the relation or subquery being joined, truncated
    using: list[str] = field(default_factory=list)
    on_columns: list[str] = field(default_factory=list)   # qualified, both sides
    # *** WHICH RELATION THIS JOIN ACTUALLY TARGETS. ***
    # Without this, a check matches join keys globally across a model and cannot tell a join from
    # the FROM relation, nor a table from an aggregated subquery. Three false positives, three
    # different causes, all of them this.
    target_alias: str = ""
    target_relation: str | None = None    # catalog.schema.table, when the target is a real table
    target_is_subquery: bool = False
    target_aggregates: bool = False       # the subquery GROUPs or DISTINCTs: the grain is collapsed
    target_keys: list[str] = field(default_factory=list)  # unqualified cols on the TARGET side


@dataclass
class WindowFact:
    position: str                   # qualify | projection
    partition_by: list[str] = field(default_factory=list)
    order_roots: list[str] = field(default_factory=list)  # what each order key roots in
    order_sql: list[str] = field(default_factory=list)


@dataclass
class Digest:
    """Everything the AST knows about one model. The state a judgment is later built from."""
    name: str
    ok: bool
    error: str | None = None
    ctes: list[str] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)     # physical tables read
    output_columns: list[str] = field(default_factory=list)
    # column -> the SQL that produces it, and what that SQL ROOTS IN. The tautology checks read the
    # root: `not_null` on a column rooted in COALESCE with a literal fallback can never fail.
    output_exprs: dict = field(default_factory=dict)
    output_roots: dict = field(default_factory=dict)
    referenced_columns: list[str] = field(default_factory=list)
    joins: list[JoinFact] = field(default_factory=list)
    windows: list[WindowFact] = field(default_factory=list)
    functions: list[tuple[str, str]] = field(default_factory=list)   # (name, position)
    group_by: list[str] = field(default_factory=list)
    distinct: bool = False
    predicates: list[str] = field(default_factory=list)
    has_qualify: bool = False
    # Relations in a FROM clause. A model's driving table is NOT something it joins to, and a check
    # that conflates the two reports a fan-out against the table the model is simply reading.
    from_relations: list[str] = field(default_factory=list)
    absorbs_fanout: bool = False    # a DISTINCT somewhere: duplicate rows are collapsed again
    # DuckDB's `~` is regexp_full_match, not Postgres's partial match. Captured here so no check
    # ever has to parse the SQL a second time; one parse per model is the contract.
    full_match_patterns: list[str] = field(default_factory=list)

    def functions_at(self, position: str) -> set[str]:
        return {n for n, p in self.functions if p == position}

    def join_keys(self) -> set[str]:
        """Unqualified column names this model joins on. The key-survival question starts here."""
        out = set()
        for j in self.joins:
            out.update(u.lower() for u in j.using)
            out.update(c.split(".")[-1].lower() for c in j.on_columns)
        return out


def digest(sql: str, name: str = "", dialect: str = "duckdb") -> Digest:
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, _RECURSION))
    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
    except RecursionError:
        return Digest(name=name, ok=False, error="RecursionError: SQL too deeply nested to parse")
    except Exception as e:                                              # noqa: BLE001
        return Digest(name=name, ok=False, error=f"{type(e).__name__}: {e}"[:200])
    finally:
        sys.setrecursionlimit(old)

    if tree is None:
        return Digest(name=name, ok=False, error="empty statement")

    d = Digest(name=name, ok=True)
    cte_names = set()

    for c in tree.find_all(exp.CTE):
        d.ctes.append(c.alias)
        cte_names.add(c.alias.lower())

    for t in tree.find_all(exp.Table):
        # A reference to a CTE is not a relation read; only a real table is.
        if t.name and t.name.lower() not in cte_names:
            d.relations.append(".".join(p for p in (t.catalog, t.db, t.name) if p))
    d.relations = sorted(set(d.relations))

    # The FINAL select's projections are the model's output columns. Selects inside CTEs are not.
    final = tree.find(exp.Select) if not tree.args.get("with") else tree
    if isinstance(final, exp.Select):
        d.output_columns = [e.alias_or_name for e in final.expressions if e.alias_or_name]
        for e in final.expressions:
            nm = e.alias_or_name
            if not nm:
                continue
            inner = e.this if isinstance(e, exp.Alias) else e
            d.output_exprs[nm.lower()] = inner.sql(dialect=dialect)[:300]
            d.output_roots[nm.lower()] = _classify(inner)
        d.distinct = bool(final.args.get("distinct"))
        g = final.args.get("group")
        if g:
            d.group_by = [x.sql(dialect=dialect) for x in g.expressions]

    d.referenced_columns = sorted({c.name.lower() for c in tree.find_all(exp.Column) if c.name})

    for sel in tree.find_all(exp.Select):
        src = _from_of(sel)
        if (src is not None and isinstance(src.this, exp.Table) and src.this.name
                and src.this.name.lower() not in cte_names):
            d.from_relations.append(_relname(src.this))
    d.from_relations = sorted(set(d.from_relations))

    for j in tree.find_all(exp.Join):
        side = (j.args.get("side") or "").upper()
        kindw = (j.args.get("kind") or "").upper()
        on, using = j.args.get("on"), j.args.get("using")
        kind = side or kindw or ("CROSS" if not on and not using else "INNER")
        tgt = j.this
        alias = (tgt.alias_or_name or "") if isinstance(tgt, exp.Expression) else ""
        rel, is_sub, aggs = None, False, False
        if isinstance(tgt, exp.Table):
            if tgt.name and tgt.name.lower() not in cte_names:
                rel = _relname(tgt)
        elif isinstance(tgt, (exp.Subquery, exp.Lateral)):
            is_sub = True
            inner = tgt.find(exp.Select)
            if inner is not None:
                aggs = bool(inner.args.get("group")) or bool(inner.args.get("distinct"))
        on_cols = sorted({c.sql(dialect=dialect) for c in on.find_all(exp.Column)}) if on else []
        tkeys = sorted({c.name.lower() for c in on.find_all(exp.Column)
                        if c.table and alias and c.table.lower() == alias.lower()}) if on else []
        if using:
            tkeys = sorted({u.alias_or_name.lower() for u in using})
        d.joins.append(JoinFact(
            kind=kind,
            lateral=isinstance(tgt, exp.Lateral) or bool(j.args.get("lateral")),
            target=tgt.sql(dialect=dialect).replace("\n", " ")[:60],
            using=[u.alias_or_name for u in (using or [])],
            on_columns=on_cols,
            target_alias=alias, target_relation=rel,
            target_is_subquery=is_sub, target_aggregates=aggs,
            target_keys=tkeys,
        ))

    # *** LOOK FOR DISTINCT EVERYWHERE, NOT JUST IN AN AGGREGATE IN THE FINAL SELECT. ***
    # `count(distinct x)` parses as Count(this=Distinct(..)) rather than as a `distinct` arg;
    # `string_agg(distinct ..)` may not even be an AggFunc; and a real model absorbed its fan-out
    # with a plain `select distinct` inside a CTE, which none of the above would have seen. Missing
    # any of these marks a deliberate, correct pattern as a defect.
    d.absorbs_fanout = (
        any(sel.args.get("distinct") for sel in tree.find_all(exp.Select))
        or bool(list(tree.find_all(exp.Distinct)))
    )

    for w in tree.find_all(exp.Window):
        pos = "projection"
        anc = w.parent
        while anc is not None:
            if isinstance(anc, exp.Qualify):
                pos = "qualify"
                break
            anc = anc.parent
        order = w.args.get("order")
        d.windows.append(WindowFact(
            position=pos,
            partition_by=[p.sql(dialect=dialect) for p in (w.args.get("partition_by") or [])],
            order_roots=[root_of(o) for o in (order.expressions if order else [])],
            order_sql=[o.sql(dialect=dialect)[:90] for o in (order.expressions if order else [])],
        ))

    d.has_qualify = bool(list(tree.find_all(exp.Qualify)))

    seen = set()
    for f in tree.find_all(exp.Func):
        key = (func_name(f), position_of(f), id(f))
        if key[:2] in seen and len(seen) > 5000:
            continue
        seen.add(key[:2])
        d.functions.append((func_name(f), position_of(f)))

    for wclause in tree.find_all(exp.Where):
        d.predicates.append(wclause.this.sql(dialect=dialect)[:300])

    for node in tree.find_all(exp.RegexpFullMatch):
        pat = node.expression
        if isinstance(pat, exp.Literal) and isinstance(pat.this, str):
            d.full_match_patterns.append(pat.this)

    return d
