"""Structural facts from compiled SQL, via sqlglot.

*** IF A PARSER CAN ANSWER IT, NEVER ASK A MODEL. ***
Everything in here is exact. It is the half of the tool that costs nothing, needs no API key, and
cannot hallucinate. A judgment is only reached for meaning the AST does not carry.

*** POSITION IS THE WHOLE POINT. ***
A defect is rarely "this function appears". It is "this function appears HERE". Measured on a real
model: `ST_Distance` (degrees) sits in a projection where it is only reported, while the ranking
orders by `ST_Distance_Sphere` (meters) and is correct. A text search flags that model; the AST
clears it. Four hand-written static guards failed on this exact distinction before -- one missed a
sort on an ALIAS, one ran a character window past the clause -- and an AST has neither failure mode.
"""
from __future__ import annotations

import contextlib
import sys
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

# Two models in a 295-model project blew the default limit: machine-generated SQL with thousands of
# UNIONed literal rows. Raising it is cheaper than failing, and the cap keeps a pathological file
# from taking the process down with it.
_RECURSION = 20_000


@contextlib.contextmanager
def deep():
    """The raised recursion limit, for every reader of a model's SQL, not only `digest`.

    *** THE DIGEST HAD IT AND THE RUN CHECK DID NOT. *** (sunny-data feedback M3) One model parsed
    for its certificates and then crashed the run check's own parse of the same SQL with
    "maximum recursion depth exceeded", which read as a coverage gap."""
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, _RECURSION))
    try:
        yield
    finally:
        sys.setrecursionlimit(old)

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
    one and miss the other, which is a silent miss rather than an error. Normalized here so no
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


def _picks_only_keys(window, keys: set) -> bool:
    """Whether every column projected beside this window is one of its keys. A star, an
    expression, or any column outside the keys makes it False: only a proof counts. (N5)"""
    sel = window.find_ancestor(exp.Select)
    if sel is None or not keys:
        return False
    for proj in sel.expressions:
        if proj.find(exp.Window) is not None:
            continue                          # the rank itself
        if isinstance(proj, exp.Star) or proj.find(exp.Star) is not None:
            return False
        inner = proj.unalias() if isinstance(proj, exp.Alias) else proj
        if isinstance(inner, (exp.Literal, exp.Null)):
            continue
        if not isinstance(inner, exp.Column) or inner.name.lower() not in keys:
            return False
    return True


def _kept_columns(window) -> list | None:
    """Every column the select holding this window keeps, or None when it keeps a star or an
    expression other than the window itself."""
    sel = window.find_ancestor(exp.Select)
    if sel is None:
        return None
    out = []
    for proj in sel.expressions:
        if proj.find(exp.Window) is not None:
            continue
        inner = proj.unalias() if isinstance(proj, exp.Alias) else proj
        if isinstance(inner, (exp.Literal, exp.Null)):
            continue
        if not isinstance(inner, exp.Column) or isinstance(inner, exp.Star) \
                or inner.find(exp.Star) is not None:
            return None
        out.append(inner.name.lower())
    return out


def _base_column(e: exp.Expression) -> str | None:
    """The single column an expression rests on, if there is exactly one."""
    if isinstance(e, exp.Alias):
        e = e.this
    cols = list(e.find_all(exp.Column)) if isinstance(e, exp.Expression) else []
    return cols[0].name.lower() if len(cols) == 1 else None


_BBOX_NAMES = ("makeenvelope", "expand", "envelope")


def _bbox_corners(tree) -> dict:
    """{function: 'stored_bounds' | 'point_plus_offset' | 'mixed'} for each box-building call.

    Bare columns are a box that already exists in the data. Arithmetic on a point is a box someone
    built to stand in for a radius, and only the second is what the proximity check is about.
    """
    out: dict = {}
    for f in tree.find_all(exp.Anonymous, exp.Func):
        name = (getattr(f, "name", "") or "").lower()
        if not any(b in name for b in _BBOX_NAMES):
            continue
        args = f.args.get("expressions") or []
        if not args:
            continue
        kinds = set()
        for a in args:
            if isinstance(a, exp.Column):
                kinds.add("column")
            elif isinstance(a, (exp.Add, exp.Sub, exp.Mul, exp.Div)):
                kinds.add("offset")
            elif isinstance(a, exp.Literal):
                kinds.add("literal")
            else:
                kinds.add("other")
        if kinds == {"column"}:
            out[name.upper()] = "stored_bounds"
        elif "offset" in kinds:
            out[name.upper()] = "point_plus_offset"
        else:
            out[name.upper()] = "mixed"
    return out


def _out_name(e) -> str | None:
    """The output name of a select item, when it is a plain column or an aliased one."""
    if isinstance(e, exp.Alias):
        return e.alias.lower() if e.alias else None
    if isinstance(e, exp.Column):
        return e.name.lower()
    return None


def _unique_of_select(sel, cte_facts: dict) -> tuple[list | None, str, list]:
    """(keys, how, order) a select is unique on by its own construction, in its OUTPUT names.

    `group by` columns (a position resolves to the select item it names; an expression that is
    not a column settles nothing), `DISTINCT ON`, or `qualify row_number() over (partition by P
    ...) = 1`. A select that only reads one CTE, joins nothing and groups nothing keeps that CTE's
    keys when it projects them."""
    if not isinstance(sel, exp.Select):
        return None, "", []
    items = sel.expressions or []
    names_by_src = {}
    for it in items:
        nm = _out_name(it)
        inner = it.this if isinstance(it, exp.Alias) else it
        if nm and isinstance(inner, exp.Column):
            names_by_src.setdefault(inner.name.lower(), nm)
    dist = sel.args.get("distinct")
    on = dist.args.get("on") if dist is not None else None
    if on is not None:
        cols = [c for c in on.expressions] if hasattr(on, "expressions") else []
        keys = [names_by_src.get(c.name.lower(), c.name.lower()) for c in cols
                if isinstance(c, exp.Column)]
        if keys and len(keys) == len(cols):
            return keys, "distinct_on", []
    if dist is not None and on is None:
        # `select distinct a, b` is `group by a, b`: one row per combination of what it keeps
        keys = [_out_name(it) for it in items]
        if keys and all(keys) and all(isinstance(it.this if isinstance(it, exp.Alias) else it,
                                                 exp.Column) for it in items):
            return keys, "group_by", []
    g = sel.args.get("group")
    if g is not None and not (g.args.get("rollup") or g.args.get("cube")
                              or g.args.get("grouping_sets") or g.args.get("all")):
        keys = []
        for x in g.expressions:
            if isinstance(x, exp.Literal) and x.is_int:
                i = int(x.this) - 1
                if not 0 <= i < len(items):
                    return None, "", []
                nm = _out_name(items[i])
                if nm is None:
                    return None, "", []
                keys.append(nm)
            elif isinstance(x, exp.Column):
                keys.append(names_by_src.get(x.name.lower(), x.name.lower()))
            else:
                return None, "", []
        return (keys or None), "group_by", []
    q = sel.args.get("qualify")
    if q is not None:
        cond = q.this
        if isinstance(cond, exp.EQ):
            w, one = cond.this, cond.expression
            if isinstance(one, exp.Window):
                w, one = one, w
            if (isinstance(w, exp.Window) and isinstance(w.this, exp.RowNumber)
                    and isinstance(one, exp.Literal) and one.this == "1"):
                part = w.args.get("partition_by") or []
                if part and all(isinstance(c, exp.Column) for c in part):
                    o = w.args.get("order")
                    order = [x.this.name.lower() for x in (o.expressions if o else [])
                             if isinstance(getattr(x, "this", None), exp.Column)]
                    return ([names_by_src.get(c.name.lower(), c.name.lower()) for c in part],
                            "row_number", order)
    frm = _from_of(sel)
    # `select ... from (select ..., row_number() over (partition by k ...) as rn ...) where rn = 1`
    w = sel.args.get("where")
    if frm is not None and isinstance(frm.this, exp.Subquery) and w is not None \
            and not sel.args.get("joins") and isinstance(w.this, exp.EQ):
        col, one = w.this.this, w.this.expression
        if isinstance(one, exp.Column):
            col, one = one, col
        inner = frm.this.this
        if isinstance(col, exp.Column) and isinstance(one, exp.Literal) and one.this == "1" \
                and isinstance(inner, exp.Select):
            for it in inner.expressions:
                if isinstance(it, exp.Alias) and it.alias.lower() == col.name.lower() \
                        and isinstance(it.this, exp.Window) \
                        and isinstance(it.this.this, exp.RowNumber):
                    part = it.this.args.get("partition_by") or []
                    if part and all(isinstance(c, exp.Column) for c in part):
                        inner_names = {}
                        for x in inner.expressions:
                            nm = _out_name(x)
                            src = x.this if isinstance(x, exp.Alias) else x
                            if nm and isinstance(src, exp.Column):
                                inner_names.setdefault(src.name.lower(), nm)
                        keys = [names_by_src.get(inner_names.get(c.name.lower(), c.name.lower()),
                                                 inner_names.get(c.name.lower(), c.name.lower()))
                                for c in part]
                        o = it.this.args.get("order")
                        order = [x.this.name.lower() for x in (o.expressions if o else [])
                                 if isinstance(getattr(x, "this", None), exp.Column)]
                        return keys, "row_number", order
    if frm is not None and isinstance(frm.this, exp.Table) and not sel.args.get("joins"):
        up = cte_facts.get(frm.this.name.lower())
        if up and up.get("unique"):
            keys = [names_by_src.get(k) for k in up["unique"]]
            if all(keys) and not any(isinstance(it, exp.Star) for it in items):
                return keys, up["how"], up.get("order", [])
            if any(isinstance(it, exp.Star) or (isinstance(it, exp.Column)
                                                and isinstance(it.this, exp.Star)) for it in items):
                return up["unique"], up["how"], up.get("order", [])
    return None, "", []


def _cte_facts(tree) -> dict:
    """{cte name: {unique, how, order, sources, filter_of}} in definition order, so a CTE can
    use the facts of the CTEs it reads."""
    out: dict = {}
    w = tree.args.get("with_") or tree.args.get("with")
    for c in (w.expressions if w is not None else []):
        name = c.alias_or_name.lower()
        body = c.this
        sources: set = set()
        for t in body.find_all(exp.Table):
            n = (t.name or "").lower()
            if n in out:
                sources |= set(out[n]["sources"])
            elif n:
                sources.add(n)
        keys, how, order = _unique_of_select(body, out) if isinstance(body, exp.Select) \
            else (None, "", [])
        filter_of = None
        if isinstance(body, exp.Select) and not body.args.get("joins") \
                and not body.args.get("group") and not body.args.get("qualify") \
                and not body.args.get("distinct") and not list(body.find_all(exp.AggFunc)) \
                and not list(body.find_all(exp.Window)):
            frm = _from_of(body)
            if frm is not None and isinstance(frm.this, exp.Table):
                n = (frm.this.name or "").lower()
                filter_of = out[n]["filter_of"] if n in out else n
        out[name] = {"unique": keys, "how": how, "order": order,
                     "sources": sorted(sources), "filter_of": filter_of}
    return out


def _equi(on, alias: str) -> tuple[bool, list]:
    """(is it an equi-join, the target-side equality columns)."""
    if on is None:
        return False, []
    conj = list(on.flatten()) if isinstance(on, exp.And) else [on]
    keys, ok = [], True
    for c in conj:
        while isinstance(c, exp.Paren):
            c = c.this
        if isinstance(c, exp.EQ) and isinstance(c.this, exp.Column) \
                and isinstance(c.expression, exp.Column):
            a, b = c.this, c.expression
            if alias and (b.table or "").lower() == alias.lower():
                keys.append(b.name.lower())
            elif alias and (a.table or "").lower() == alias.lower():
                keys.append(a.name.lower())
            else:
                ok = False
            continue
        tables = {(col.table or "").lower() for col in c.find_all(exp.Column)}
        if alias and alias.lower() in tables and len(tables) > 1:
            ok = False                    # a condition across both sides that is not equality
    return (ok and bool(keys)), sorted(set(keys))


def _final_dedupe(tree) -> tuple[str, list]:
    """("distinct_on" | "group_by", columns) that decide the MODEL's rows, or ("", []).

    Only the outermost select, or the chain of CTEs it reads straight through with no join. A
    DISTINCT ON in a lookup CTE says what one row of the LOOKUP is -- taking the first one found
    anywhere gave `water_source_history` a grain of `source_table` -- and a GROUP BY two CTEs down
    is the grain of `mart_owner_portfolios`, which inherited its driver's key through it.
    """
    sel = tree if isinstance(tree, exp.Select) else None
    for depth in range(4):
        if sel is None:
            return "", []
        dist = sel.args.get("distinct")
        on = dist.args.get("on") if dist is not None else None
        if on is not None:
            return "distinct_on", [c.name for c in on.find_all(exp.Column)]
        grp = sel.args.get("group")
        if grp is not None and depth == 0:
            return "", []                 # the top level's GROUP BY has its own route
        if grp is not None:
            cols = [c.name for e in grp.expressions for c in ([e] if isinstance(e, exp.Column)
                                                              else [])]
            return ("group_by", cols) if cols and len(cols) == len(grp.expressions) else ("", [])
        frm = _from_of(sel)
        if sel.args.get("joins") or frm is None:
            return "", []
        src = frm.this
        if not isinstance(src, exp.Table):
            return "", []
        # sqlglot 30 spells these `from_` and `with_`; both spellings are read.
        with_ = tree.args.get("with_") or tree.args.get("with") or exp.With()
        ctes = {c.alias_or_name.lower(): c.this for c in with_.expressions}
        sel = ctes.get(src.name.lower())
        sel = sel if isinstance(sel, exp.Select) else None
    return "", []


def _union_members(tree, dialect: str) -> set:
    """Relations that appear inside a UNION arm of this model.

    A parent read this way contributes rows alongside its siblings rather than being joined to,
    so one of its rows is one of the child's. Whatever else the child does, THIS hop did not
    multiply anything.
    """
    out: set = set()
    for u in tree.find_all(exp.Union):
        for tbl in u.find_all(exp.Table):
            name = tbl.name or tbl.sql(dialect=dialect)
            if name:
                out.add(name)
    return out


def _pre_aggregated(tree, dialect: str) -> dict:
    """{relation: [group keys]} for every relation collapsed inside a subquery or CTE.

    A child that joins `(select k, count(*) from parent group by k)` has already reduced that
    parent to one row per k, so the join cannot multiply. Without this the state says "joins on k,
    no grouping" -- every word true, and the conclusion wrong.
    """
    out: dict = {}
    # The OUTERMOST select is excluded: a top-level `group by` makes the child deliberately
    # coarser, which is a different answer and is already carried as `groups_by`. Counting it here
    # would mask a real narrowing as "the parent was already collapsed".
    root = tree.find(exp.Select)
    for node in tree.find_all(exp.Select):
        if node is root:
            continue
        g = node.args.get("group")
        if g is None and not any(isinstance(e, exp.Distinct) for e in node.args.get("expressions", [])
                                 if e is not None):
            continue
        keys = [x.sql(dialect=dialect) for x in g.expressions] if g is not None else []
        for tbl in node.find_all(exp.Table):
            name = tbl.name or tbl.sql(dialect=dialect)
            if name:
                out.setdefault(name, keys)
    return out


def _resolve_group_by(group_exprs, select_exprs) -> list[str]:
    out = []
    for g in group_exprs:
        if isinstance(g, exp.Literal) and g.is_int:
            i = int(g.this) - 1                      # ordinals are 1-based
            if 0 <= i < len(select_exprs):
                sel = select_exprs[i]
                out.append((_base_column(sel) or sel.alias_or_name or "").lower())
                continue
        if isinstance(g, exp.Column):
            out.append(g.name.lower())
            continue
        base = _base_column(g)
        out.append(base or g.sql().lower())
    return [c for c in out if c]


def _is_scalar_scoped(where: exp.Expression) -> bool:
    """Does this WHERE shape only ONE COLUMN, rather than the model's rows?

    Scoping by nesting depth was wrong: a CTE's filter genuinely shapes the output and belongs to
    the model. What does not is a SCALAR subquery in a select list -- `(select count(*) from z
    where amount = 0) as years_diverting_nothing` -- whose condition describes that one value. Shown
    as a model filter, it makes a diversion summary look like it keeps only rows where nothing was
    diverted.
    """
    node = where.parent
    while node is not None:
        parent = node.parent
        if isinstance(node, exp.Subquery) and parent is not None and not isinstance(
                parent, (exp.From, exp.Join, exp.CTE)):
            return True
        if isinstance(node, exp.Filter):
            return True
        node = parent
    return False


def _conjuncts(e: exp.Expression) -> list:
    """The top-level ANDed parts of a predicate. An OR stays whole: its branches are one decision."""
    if isinstance(e, exp.And):
        return _conjuncts(e.this) + _conjuncts(e.expression)
    if isinstance(e, exp.Paren):
        return _conjuncts(e.this)
    return [e]


def _reprojected(e: exp.Expression) -> bool:
    """Does this expression transform its geometry to another CRS before measuring?"""
    return any(func_name(f) in ("ST_TRANSFORM", "ST_SETSRID")
               for f in e.find_all(exp.Func))


def _from_of(sel: exp.Select):
    """sqlglot renamed this arg from `from` to `from_` at v30.

    Reading only the new key returns None on older sqlglot, which is a SILENT miss: the FROM
    relation simply vanishes and every check downstream behaves as though the model joined nothing.
    The package supports sqlglot>=25, so both spellings are read.
    """
    return sel.args.get("from_") or sel.args.get("from")


def trace(tree, sel, col: str, _depth: int = 0) -> list:
    """Where output column `col` of select `sel` comes from, hop by hop down its FROM chain:
    [(relation or CTE name, column there), ...], the last hop a real table. Empty when the column
    is computed, comes from a joined relation, or cannot be followed.

    *** A RENAME IS NOT THE SAME COLUMN. *** (found by the run check) `select objectid as county_id`
    made a certificate assume "`county_id` unique in the source", a column the source does not
    have; Lean proved it, and a run of the model contradicted it."""
    if _depth > 20 or not isinstance(sel, exp.Select):
        return []
    col = col.lower()
    frm = _from_of(sel)
    if frm is None:
        return []
    src = frm.this
    alias = (src.alias_or_name or "").lower()
    joined = bool(sel.args.get("joins"))
    hit = None
    for x in sel.expressions:
        if (x.alias_or_name or "").lower() == col and not isinstance(x, exp.Star):
            hit = x
            break
    if hit is not None:
        inner = hit.this if isinstance(hit, exp.Alias) else hit
        # a cast to text keeps distinct values distinct (and NULL NULL): the same key
        if isinstance(inner, exp.Cast) and isinstance(inner.this, exp.Column) \
                and inner.to.this in (exp.DataType.Type.VARCHAR, exp.DataType.Type.TEXT,
                                      exp.DataType.Type.NVARCHAR):
            inner = inner.this
        if not isinstance(inner, exp.Column) or isinstance(inner.this, exp.Star):
            return []
        q = (inner.table or "").lower()
        if (q and q != alias) or (not q and joined):
            return []
        name = inner.name.lower()
    else:
        stars = [x for x in sel.expressions if isinstance(x, exp.Star)
                 or (isinstance(x, exp.Column) and isinstance(x.this, exp.Star))]
        if not stars or joined and not any(
                isinstance(x, exp.Column) and (x.table or "").lower() == alias for x in stars):
            return []
        name = col
    if isinstance(src, exp.Subquery):
        rest = trace(tree, src.this, name, _depth + 1)
        return [(alias, name), *rest] if rest else []
    if not isinstance(src, exp.Table):
        return []
    tname = (src.name or "").lower()
    w = tree.args.get("with_") or tree.args.get("with")
    for c in (w.expressions if w is not None else []):
        if c.alias_or_name.lower() == tname and not src.db:
            rest = trace(tree, c.this, name, _depth + 1)
            return [(tname, name), *rest] if rest else []
    return [(tname, name)]


def trace_output(sql: str, col: str, dialect: str = "duckdb") -> list:
    """`trace` from a model's final select."""
    try:
        with deep():
            tree = sqlglot.parse_one(sql, read=dialect)
            return trace(tree, tree, col)
    except Exception:                                            # noqa: BLE001
        return []


def trace_cte(sql: str, cte: str, col: str, dialect: str = "duckdb") -> list:
    """`trace` from one CTE's select."""
    try:
        with deep():
            tree = sqlglot.parse_one(sql, read=dialect)
            w = tree.args.get("with_") or tree.args.get("with")
            for c in (w.expressions if w is not None else []):
                if c.alias_or_name.lower() == cte.lower():
                    return trace(tree, c.this, col)
    except Exception:                                            # noqa: BLE001
        return []
    return []


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
    # *** `FILTER (WHERE ...)` IS THE AGGREGATE'S OWN CLAUSE, SO THE ROOT IS THE AGGREGATE. ***
    # Read as `filter`, it says an aggregate happened and hides WHICH, and which is the entire
    # question: `count(*) filter (...)` is never NULL and `min(x) filter (...)` is NULL whenever
    # the filter keeps nothing. Callers that only needed the class already treated `filter` as
    # aggregated; descending costs them nothing and makes the root exact.
    if isinstance(e, exp.Filter):
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
    # *** WHAT THE JOIN IS ON, AND WHAT IT READS, AS A PROOF NEEDS THEM. *** (L1, 0.52)
    # `equi`: every condition is a column = column equality or touches one side only (a filter);
    # a spatial or range join is not, and no row-count rule applies to it. `equi_keys`: the
    # target-side columns of the equalities. `target_cte`: the CTE it joins, by name.
    # `target_unique`: what the target is unique on BY CONSTRUCTION (its group by, its DISTINCT
    # ON, its one-row-per-partition dedupe), with `target_unique_by` naming which and
    # `target_order` the dedupe's order keys. `target_sources`: the tables the target reads.
    # `target_filter_of`: the one table a CTE only filters and projects, when that is all it does.
    equi: bool = True
    equi_keys: list[str] = field(default_factory=list)
    target_cte: str | None = None
    target_unique: list | None = None
    target_unique_by: str = ""
    target_order: list = field(default_factory=list)
    target_sources: list = field(default_factory=list)
    target_filter_of: str | None = None


@dataclass
class WindowFact:
    position: str                   # qualify | projection
    partition_by: list[str] = field(default_factory=list)
    # Partition keys resolved to base columns. `partition by lower(coalesce(city, ''))` split on a
    # dot yields "''))" which is not a column, and a grain built from it is nonsense rather than
    # merely wrong.
    partition_columns: list[str] = field(default_factory=list)
    order_roots: list[str] = field(default_factory=list)  # what each order key roots in
    order_sql: list[str] = field(default_factory=list)
    # *** A DISTANCE MEASURED AFTER A REPROJECTION IS IN METERS, NOT DEGREES. ***
    # `ST_Distance(ST_Transform(p, 'EPSG:4326', 'EPSG:5070'), ..)` is CORRECT code. Flagging it is
    # how a hand-written guard failed on its second attempt: it banned the function outright and
    # caught the one model that had already done the right thing.
    order_reprojected: list[bool] = field(default_factory=list)
    # *** A TIE THE OUTPUT CANNOT SEE IS NOT A CHOICE. *** (N5) True when every column the
    # enclosing select projects is a partition or an ORDER BY key: two rows still tied after the
    # sort are then identical in everything kept, so which one survives changes nothing.
    picks_only_keys: bool = False
    # The same facts in the window's own scope, for a certificate to state (L2): the partition's
    # base columns, the order's columns (an expression is `None`), and every column the enclosing
    # select keeps (`None` when it keeps a star or an expression, so nothing can be claimed).
    part_keys: list = field(default_factory=list)
    order_keys: list = field(default_factory=list)
    kept_columns: list | None = None


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
    # *** SOURCE COLUMN -> THE NAME THIS MODEL PUBLISHES IT UNDER. ***
    # `select name as discovered_name ... group by name` has a grain of `discovered_name` to anyone
    # downstream, and of `name` only inside this query. Declared keys, tests and every consumer live
    # in the OUTPUT namespace, so a candidate expressed in the source namespace silently fails to
    # match a key that is in fact correct.
    alias_of: dict = field(default_factory=dict)
    # *** AN OUTPUT COLUMN'S EXPRESSION, RESOLVED THROUGH THE CTEs IT CAME FROM. ***
    # `record_first_year` reads as `c.first_year`, which says nothing. One hop back into the CTE it
    # is `min(year)` -- and an AGGREGATE CAN NEVER BE PART OF THE GRAIN OF THE QUERY THAT PRODUCED
    # IT. Unresolved, three models sent that judgment to Jev and it answered 0.53, correctly
    # uncertain, because the state did not contain the fact that settles it.
    resolved_roots: dict = field(default_factory=dict)
    # alias -> the CTE name or physical relation it refers to. Cross-MODEL resolution needs the DAG
    # and so happens in infer.py; this is the half a single query can answer on its own.
    alias_relation: dict = field(default_factory=dict)
    referenced_columns: list[str] = field(default_factory=list)
    joins: list[JoinFact] = field(default_factory=list)
    windows: list[WindowFact] = field(default_factory=list)
    functions: list[tuple[str, str]] = field(default_factory=list)   # (name, position)
    group_by: list[str] = field(default_factory=list)
    # *** GROUP BY RESOLVED TO REAL COLUMN NAMES. ***
    # `group by 1, 2` is an ORDINAL into the select list, and reading it literally yields a grain of
    # ['1','2'], which is not a column and silently loses the model's actual grain. `group by
    # trim(wdid)` has the same problem one level in. Both are resolved here so no caller has to.
    group_by_columns: list[str] = field(default_factory=list)
    distinct: bool = False
    predicates: list[str] = field(default_factory=list)
    # *** ONE PREDICATE PER JUDGMENT, AND ONLY THE MODEL'S OWN. ***
    # A WHERE is usually several independent decisions ANDed together, so the top-level conjuncts
    # are split out to be judged one at a time.
    #
    # And only the OUTERMOST select's filters count as the model's. Collecting every WHERE in the
    # tree presents a subquery's condition as though the model applied it: one real model computes
    # `years_diverting_nothing` from a subquery filtered to `coalesce(acre_feet, 0) = 0`, and
    # showing that as a model filter makes a diversion summary look like it keeps only the rows
    # where nothing was diverted. A judgment shown that will call the description a lie, correctly,
    # about evidence that was never true.
    predicates_atomic: list[str] = field(default_factory=list)
    predicates_nested: list[str] = field(default_factory=list)   # subquery / CTE filters
    has_qualify: bool = False
    # Relations in a FROM clause. A model's driving table is NOT something it joins to, and a check
    # that conflates the two reports a fan-out against the table the model is simply reading.
    from_relations: list[str] = field(default_factory=list)
    absorbs_fanout: bool = False    # a DISTINCT somewhere: duplicate rows are collapsed again
    # *** A RELATION COLLAPSED BEFORE THE JOIN CANNOT FAN THE JOIN OUT. ***
    # {relation: [group keys]} for anything aggregated inside a subquery or CTE. Reported from the
    # field: 33% of 543 hops came back `silently_multiplied`, and the top one was
    # `join (select wdid, count(*) from int_water_diligence group by wdid) dl on dl.wdid = r.wdid`
    # -- already one row per key. The judgment saw join keys and no grouping and inferred fan-out
    # from true facts that were not the whole state.
    pre_aggregated: dict = field(default_factory=dict)
    # What the model's own outermost select is unique on by construction: (keys, how, order).
    own_unique: tuple = (None, "", [])
    # The same for what the outermost FROM reads, when it is a CTE or a subquery, and the
    # relations under it: a model reading a grouped CTE starts from that CTE's grain.
    from_unique: tuple = (None, "", [])
    from_sources: list = field(default_factory=list)
    # *** A UNION MEMBER CANNOT MULTIPLY. ***
    # One parent row becomes exactly one child row; the child having MORE rows than any single
    # parent is a different fact and not a fan-out. Ten of twelve disagreements on a hand-ruled
    # warehouse were this: `dim_business` unions eleven staging feeds and was reported
    # `silently_multiplied` at 16 marts. Readable from the AST, no judgment, no call.
    union_members: set = field(default_factory=set)
    # `SELECT DISTINCT ON (a, b)`: one row per those columns, stated as plainly as a qualify dedupe.
    distinct_on: list = field(default_factory=list)
    # A GROUP BY on the chain of plain CTE reads the final select comes through, when the top
    # level has none: the model is one row per these, whatever its drivers were.
    final_group_by: list = field(default_factory=list)
    # *** AN ENVELOPE BUILT FROM STORED BOUNDS IS A TESSELLATION, NOT A RADIUS. ***
    # `ST_MakeEnvelope(cx0, cy0, cx1, cy1)` from columns on the same row IS the intended geometry
    # -- a cell of a grid. `ST_MakeEnvelope(lon-0.02, lat-0.02, lon+0.02, lat+0.02)` approximates a
    # circle and is the case "a box is not a circle" was written for. Both `bbox_as_radius`
    # disagreements on a hand-ruled warehouse were the first kind.
    bbox_corners: dict = field(default_factory=dict)
    # DuckDB's `~` is regexp_full_match, not Postgres's partial match. Captured here so no check
    # ever has to parse the SQL a second time; one parse per model is the contract.
    full_match_patterns: list[str] = field(default_factory=list)
    # First-element picks out of a delimited or array value. Taking [1] of a multi-valued field is
    # a silent CHOICE, and which value you get depends on the source's ordering.
    first_element_picks: list = field(default_factory=list)   # (column_expr, how)

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
    try:
        with deep():
            tree = sqlglot.parse_one(sql, dialect=dialect)
    except RecursionError:
        return Digest(name=name, ok=False, error="RecursionError: SQL too deeply nested to parse")
    except Exception as e:                                              # noqa: BLE001
        return Digest(name=name, ok=False, error=f"{type(e).__name__}: {e}"[:200])

    if tree is None:
        return Digest(name=name, ok=False, error="empty statement")

    try:
        return _extract(tree, name, dialect)
    except RecursionError:
        return Digest(name=name, ok=False, error="RecursionError while reading the parsed SQL")
    except Exception as e:                                              # noqa: BLE001
        # *** ONE PATHOLOGICAL MODEL MUST NOT TAKE DOWN A 1,600-MODEL RUN. ***
        # parse_one was guarded; rendering the tree afterwards was not, and sqlglot can raise deep
        # inside a dialect transform -- a BigQuery DATETIME() with one argument crashed an entire
        # project. A model assay cannot read is a model assay REPORTS, never an aborted run.
        return Digest(name=name, ok=False,
                      error=f"could not read the parsed SQL: {type(e).__name__}: {e}"[:200])


def _extract(tree, name: str, dialect: str) -> Digest:
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
            base = _base_column(inner)
            if base and base != nm.lower():
                d.alias_of.setdefault(base, nm.lower())
        d.distinct = bool(final.args.get("distinct"))
        g = final.args.get("group")
        if g:
            d.group_by = [x.sql(dialect=dialect) for x in g.expressions]
            d.group_by_columns = _resolve_group_by(g.expressions, final.expressions)

    # CTE name -> {column: what it roots in}, so an outer reference can be followed one hop back.
    cte_outputs: dict = {}
    for c in tree.find_all(exp.CTE):
        inner = c.this if isinstance(c.this, exp.Select) else c.this.find(exp.Select)
        if inner is None:
            continue
        cte_outputs[c.alias.lower()] = {
            e.alias_or_name.lower(): _classify(e.this if isinstance(e, exp.Alias) else e)
            for e in inner.expressions if e.alias_or_name
        }

    # alias -> CTE name or physical relation, from every FROM and JOIN
    alias_to_cte: dict = {}
    for sel in tree.find_all(exp.Select):
        src = _from_of(sel)
        tables = [src.this] if src is not None else []
        tables += [j.this for j in (sel.args.get("joins") or [])]
        for t in tables:
            if not isinstance(t, exp.Table) or not t.name:
                continue
            alias = (t.alias or t.name).lower()
            if t.name.lower() in cte_outputs:
                alias_to_cte[alias] = t.name.lower()
            else:
                d.alias_relation[alias] = _relname(t)

    for out_name, sql_txt in list(d.output_exprs.items()):
        root = d.output_roots.get(out_name, "")
        if root != "column":
            d.resolved_roots[out_name] = root
            continue
        try:
            col = sqlglot.parse_one(sql_txt, dialect=dialect)
        except Exception:                                    # noqa: BLE001,S112
            continue
        if not isinstance(col, exp.Column):
            continue
        cte = alias_to_cte.get((col.table or "").lower()) or (
            col.table.lower() if col.table and col.table.lower() in cte_outputs else None)
        inner_root = (cte_outputs.get(cte) or {}).get(col.name.lower()) if cte else None
        d.resolved_roots[out_name] = inner_root or root

    out_set = {c.lower() for c in d.output_columns}
    d.group_by_columns = [c if c in out_set else d.alias_of.get(c, c) for c in d.group_by_columns]

    d.referenced_columns = sorted({c.name.lower() for c in tree.find_all(exp.Column) if c.name})

    for sel in tree.find_all(exp.Select):
        src = _from_of(sel)
        if (src is not None and isinstance(src.this, exp.Table) and src.this.name
                and src.this.name.lower() not in cte_names):
            d.from_relations.append(_relname(src.this))
    d.from_relations = sorted(set(d.from_relations))

    cte_facts = _cte_facts(tree)
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
        if using:
            equi, ekeys = True, list(tkeys)
        else:
            equi, ekeys = _equi(on, alias)
        tcte = t_unique = None
        t_how, t_order, t_sources, t_filter = "", [], [], None
        if isinstance(tgt, exp.Table) and tgt.name and tgt.name.lower() in cte_names:
            tcte = tgt.name.lower()
            f = cte_facts.get(tcte) or {}
            t_unique, t_how, t_order = f.get("unique"), f.get("how", ""), f.get("order", [])
            t_sources, t_filter = f.get("sources", []), f.get("filter_of")
        elif isinstance(tgt, exp.Subquery):
            inner = tgt.this
            t_unique, t_how, t_order = _unique_of_select(inner, cte_facts)
            t_sources = sorted({(t.name or "").lower() for t in tgt.find_all(exp.Table)} - {""})
        elif rel:
            t_sources = [tgt.name.lower()]
        d.joins.append(JoinFact(
            kind=kind,
            lateral=isinstance(tgt, exp.Lateral) or bool(j.args.get("lateral")),
            target=tgt.sql(dialect=dialect).replace("\n", " ")[:60],
            using=[u.alias_or_name for u in (using or [])],
            on_columns=on_cols,
            target_alias=alias, target_relation=rel,
            target_is_subquery=is_sub, target_aggregates=aggs,
            target_keys=tkeys, equi=equi, equi_keys=ekeys, target_cte=tcte,
            target_unique=t_unique, target_unique_by=t_how, target_order=t_order,
            target_sources=t_sources, target_filter_of=t_filter,
        ))

    # *** LOOK FOR DISTINCT EVERYWHERE, NOT JUST IN AN AGGREGATE IN THE FINAL SELECT. ***
    # `count(distinct x)` parses as Count(this=Distinct(..)) rather than as a `distinct` arg;
    # `string_agg(distinct ..)` may not even be an AggFunc; and a real model absorbed its fan-out
    # with a plain `select distinct` inside a CTE, which none of the above would have seen. Missing
    # any of these marks a deliberate, correct pattern as a defect.
    d.pre_aggregated = _pre_aggregated(tree, dialect)
    d.own_unique = _unique_of_select(tree, cte_facts) if isinstance(tree, exp.Select) \
        else (None, "", [])
    if isinstance(tree, exp.Select):
        frm = _from_of(tree)
        src = frm.this if frm is not None else None
        if isinstance(src, exp.Table) and (src.name or "").lower() in cte_facts:
            f = cte_facts[src.name.lower()]
            d.from_unique = (f["unique"], f["how"], f.get("order", []))
            d.from_sources = list(f["sources"])
        elif isinstance(src, exp.Subquery):
            d.from_unique = _unique_of_select(src.this, cte_facts)
            d.from_sources = sorted({(t.name or "").lower() for t in src.find_all(exp.Table)}
                                    - {""} - set(cte_facts))
    d.union_members = _union_members(tree, dialect)
    kind, cols = _final_dedupe(tree)
    if kind == "distinct_on":
        d.distinct_on = cols
    elif kind == "group_by":
        d.final_group_by = cols
    d.bbox_corners = _bbox_corners(tree)
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
        parts = w.args.get("partition_by") or []
        _out = {c.lower() for c in d.output_columns}
        _keys = {c.lower() for c in (_base_column(p) for p in parts) if c}
        _keys |= {o.this.name.lower() for o in (order.expressions if order else [])
                  if isinstance(getattr(o, "this", None), exp.Column)}
        d.windows.append(WindowFact(
            position=pos,
            partition_by=[p.sql(dialect=dialect) for p in parts],
            partition_columns=[c if c in _out else d.alias_of.get(c, c)
                               for c in (_base_column(p) for p in parts) if c],
            order_roots=[root_of(o) for o in (order.expressions if order else [])],
            order_sql=[o.sql(dialect=dialect)[:90] for o in (order.expressions if order else [])],
            order_reprojected=[_reprojected(o) for o in (order.expressions if order else [])],
            picks_only_keys=_picks_only_keys(w, _keys),
            part_keys=[c for c in (_base_column(p) for p in parts) if c],
            order_keys=[o.this.name.lower() if isinstance(getattr(o, "this", None), exp.Column)
                        else None for o in (order.expressions if order else [])],
            kept_columns=_kept_columns(w),
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
        bucket = d.predicates_nested if _is_scalar_scoped(wclause) else d.predicates_atomic
        for leaf in _conjuncts(wclause.this):
            txt = leaf.sql(dialect=dialect).strip()[:220]
            if txt and txt not in bucket:
                bucket.append(txt)

    # *** THE NODE TYPE DOES NOT EXIST BEFORE sqlglot 28. ***
    # Referencing it unconditionally makes assay fail to parse ANY model on an older sqlglot,
    # which is worse than losing one dialect check. The declared floor is 28 for this reason and
    # the fallback keeps a narrower floor viable for anyone who pins deliberately.
    # *** THE ARGUMENT IS NAMED, NOT POSITIONAL, AND THE INDEX IS NORMALIZED. ***
    # sqlglot parses SPLIT_PART into a node whose parts are `this`/`delimiter`/`part_index`, so
    # reading positional arguments found nothing at all. And `arr[1]` is normalized to `arr[0]`,
    # so a check looking only for a literal 1 is silently blind to every array pick.
    for _fn in tree.find_all(exp.Func):
        if func_name(_fn) in ("SPLIT_PART", "SPLITPART"):
            _idx = _fn.args.get("part_index")
            if isinstance(_idx, exp.Literal) and str(_idx.this) == "1":
                d.first_element_picks.append((_fn.sql(dialect=dialect)[:110], "split_part(.., 1)"))
    for _br in tree.find_all(exp.Bracket):
        _i = (_br.expressions or [None])[0]
        _lit = _i.this if isinstance(_i, exp.Literal) else None
        if str(_lit) in ("0", "1") and isinstance(_br.this, (exp.Column, exp.Func)):
            d.first_element_picks.append((_br.sql(dialect=dialect)[:110], "the first element"))


    full_match = getattr(exp, "RegexpFullMatch", None)
    if full_match is not None:
        for node in tree.find_all(full_match):
            pat = node.expression
            if isinstance(pat, exp.Literal) and isinstance(pat.this, str):
                d.full_match_patterns.append(pat.this)

    return d
