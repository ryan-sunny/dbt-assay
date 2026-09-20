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


def _base_column(e: exp.Expression) -> str | None:
    """The single column an expression rests on, if there is exactly one."""
    if isinstance(e, exp.Alias):
        e = e.this
    cols = list(e.find_all(exp.Column)) if isinstance(e, exp.Expression) else []
    return cols[0].name.lower() if len(cols) == 1 else None


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
    # Partition keys resolved to base columns. `partition by lower(coalesce(city, ''))` split on a
    # dot yields "''))" which is not a column, and a grain built from it is nonsense rather than
    # merely wrong.
    partition_columns: list[str] = field(default_factory=list)
    order_roots: list[str] = field(default_factory=list)  # what each order key roots in
    order_sql: list[str] = field(default_factory=list)
    # *** A DISTANCE MEASURED AFTER A REPROJECTION IS IN METRES, NOT DEGREES. ***
    # `ST_Distance(ST_Transform(p, 'EPSG:4326', 'EPSG:5070'), ..)` is CORRECT code. Flagging it is
    # how a hand-written guard failed on its second attempt: it banned the function outright and
    # caught the one model that had already done the right thing.
    order_reprojected: list[bool] = field(default_factory=list)


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
        parts = w.args.get("partition_by") or []
        _out = {c.lower() for c in d.output_columns}
        d.windows.append(WindowFact(
            position=pos,
            partition_by=[p.sql(dialect=dialect) for p in parts],
            partition_columns=[c if c in _out else d.alias_of.get(c, c)
                               for c in (_base_column(p) for p in parts) if c],
            order_roots=[root_of(o) for o in (order.expressions if order else [])],
            order_sql=[o.sql(dialect=dialect)[:90] for o in (order.expressions if order else [])],
            order_reprojected=[_reprojected(o) for o in (order.expressions if order else [])],
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
    # *** THE ARGUMENT IS NAMED, NOT POSITIONAL, AND THE INDEX IS NORMALISED. ***
    # sqlglot parses SPLIT_PART into a node whose parts are `this`/`delimiter`/`part_index`, so
    # reading positional arguments found nothing at all. And `arr[1]` is normalised to `arr[0]`,
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
