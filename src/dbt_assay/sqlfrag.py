"""sqlglot's tree of a model, in the SQL fragment Lean defines (lean/Sql). (L4)

The per-model parse proof states: Lean's parser, run on the model's text, yields exactly the tree
sqlglot produced. This module writes sqlglot's side of that equation, twice -- as a Lean term for
the theorem, and as the canonical s-expression `Sql/Show.lean` prints, so a mismatch can say
where the two trees part. A construct outside the fragment raises `Outside` with its name: that
model's parse is "unproven" and keeps the L2 round trip as its evidence.

Nothing here decides anything: if this translation is wrong, the theorem fails and the model reads
"unproven", never "proven".
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp

_DIALECT = ["duckdb"]           # the dialect being read, for printing a function's own name


class Outside(Exception):
    """A construct the fragment does not cover."""


# One name per type the engines spell several ways, exactly as `Sql.normType` in Lean.
_TYPES = {"integer": "int", "int4": "int", "signed": "int", "int8": "bigint", "long": "bigint",
          "int2": "smallint", "float8": "double", "double_precision": "double", "float4": "float",
          "real": "float", "numeric": "decimal", "number": "decimal", "bool": "boolean",
          "logical": "boolean", "string": "varchar", "text": "varchar",
          "char_varying": "varchar", "datetime": "timestamp"}

# One name per function the engines spell several ways, exactly as `Sql.normFn` in Lean.
_FNS = {"substr": "substring", "ifnull": "coalesce", "nvl": "coalesce", "len": "length",
        "char_length": "length", "character_length": "length", "lcase": "lower",
        "ucase": "upper", "pow": "power", "ceiling": "ceil", "day_of_week": "dayofweek",
        "stddev_samp": "stddev", "var_samp": "variance", "array_agg": "list",
        "string_agg": "group_concat", "listagg": "group_concat"}

_BIN = {exp.EQ: "=", exp.NEQ: "<>", exp.LT: "<", exp.LTE: "<=", exp.GT: ">", exp.GTE: ">=",
        exp.Add: "+", exp.Sub: "-", exp.Mul: "*", exp.Div: "/", exp.Mod: "%", exp.DPipe: "||",
        exp.And: "AND", exp.Or: "OR", exp.Like: "LIKE", exp.ILike: "ILIKE"}


def _ident(i) -> str:
    if i is None:
        return ""
    if isinstance(i, exp.Identifier):
        return i.this if i.quoted else i.this.lower()
    return str(i).lower()


def _parts(node) -> list:
    return [_ident(node.args.get(k)) for k in ("catalog", "db") if node.args.get(k)]


def _type(dt) -> str:
    if not isinstance(dt, exp.DataType):
        raise Outside("a type")
    base = dt.this.value.lower() if hasattr(dt.this, "value") else str(dt.this).lower()
    base = _TYPES.get(base, base)
    params = [p.this.this if isinstance(p, exp.DataTypeParam) and isinstance(p.this, exp.Literal)
              else None for p in dt.expressions]
    if any(p is None for p in params):
        raise Outside("a type parameter")
    return base + ("(" + ",".join(str(p) for p in params) + ")" if params else "")


def expr(e) -> tuple:
    """A node of the fragment's Expr, as a tuple."""
    while isinstance(e, exp.Paren):
        e = e.this
    if isinstance(e, exp.Column):
        if isinstance(e.this, exp.Star):
            return ("star", [_ident(p) for p in e.parts[:-1]])
        qual = [_ident(e.args.get(k)) for k in ("catalog", "db", "table") if e.args.get(k)]
        return ("col", qual, _ident(e.this))
    if isinstance(e, exp.Star):
        return ("star", [])
    if isinstance(e, exp.Literal):
        return ("str", e.this) if e.is_string else ("num", e.this)
    if isinstance(e, exp.Null):
        return ("null",)
    if isinstance(e, exp.Boolean):
        return ("true",) if e.this else ("false",)
    if type(e) in _BIN:
        return ("bin", _BIN[type(e)], expr(e.this), expr(e.expression))
    if isinstance(e, exp.Neg):
        return ("un", "-", expr(e.this))
    if isinstance(e, exp.Not):
        inner = e.this
        while isinstance(inner, exp.Paren):
            inner = inner.this
        if isinstance(inner, exp.Is) and isinstance(inner.expression, exp.Null):
            return ("isnull", expr(inner.this), True)
        if isinstance(inner, exp.In):
            return _in(inner, True)
        if isinstance(inner, exp.Between):
            return ("between", expr(inner.this), expr(inner.args["low"]),
                    expr(inner.args["high"]), True)
        return ("un", "NOT", expr(e.this))
    if isinstance(e, exp.Is):
        if isinstance(e.expression, exp.Null):
            return ("isnull", expr(e.this), False)
        raise Outside("IS other than IS NULL")
    if isinstance(e, exp.In):
        return _in(e, False)
    if isinstance(e, exp.Between):
        return ("between", expr(e.this), expr(e.args["low"]), expr(e.args["high"]), False)
    if isinstance(e, exp.Case):
        op = e.args.get("this")
        whens = [(expr(i.this), expr(i.args["true"])) for i in e.args.get("ifs") or []]
        d = e.args.get("default")
        return ("case", expr(op) if op is not None else None, whens,
                expr(d) if d is not None else None)
    if isinstance(e, exp.TryCast):
        return ("trycast", expr(e.this), _type(e.args["to"]))
    if isinstance(e, exp.Cast):
        return ("cast", expr(e.this), _type(e.args["to"]))
    if isinstance(e, exp.Filter):
        w = e.expression
        cond = w.this if isinstance(w, exp.Where) else w
        return ("filter", expr(e.this), expr(cond))
    if isinstance(e, exp.Window):
        if e.args.get("spec") is not None or e.args.get("alias"):
            raise Outside("a window frame")
        part = [expr(p) for p in e.args.get("partition_by") or []]
        order = []
        o = e.args.get("order")
        for x in (o.expressions if o is not None else []):
            order.append(_ordered(x))
        return ("window", expr(e.this), part, order)
    if isinstance(e, exp.Count):
        t = e.this
        if isinstance(t, exp.Star):
            return ("fn", "count", False, [("star", [])])
        if isinstance(t, exp.Distinct):
            return ("fn", "count", True, [expr(x) for x in t.expressions])
        return ("fn", "count", False, [expr(t)] if t is not None else [])
    if isinstance(e, exp.Anonymous):
        name = str(e.name).lower()
        return ("fn", _FNS.get(name, name), False, [expr(a) for a in e.expressions])
    if isinstance(e, exp.Func):
        if isinstance(e.this, exp.Distinct):
            raise Outside("DISTINCT inside a function")
        args = []
        for k in e.arg_types:
            v = e.args.get(k)
            if v is None or isinstance(v, (bool, str)):
                continue
            args += [expr(x) for x in v] if isinstance(v, list) else [expr(v)]
        # sqlglot names a function by its own class (`regexp_like`); the text names it as the
        # dialect does (`regexp_matches`), which is how the node prints back in that dialect.
        name = e.sql_name().lower()
        try:
            head = e.sql(dialect=_DIALECT[0]).split("(", 1)[0].strip().lower()
            if head and all(ch.isalnum() or ch == "_" for ch in head):
                name = head
        except Exception:                                        # noqa: BLE001, S110
            pass
        return ("fn", _FNS.get(name, name), False, args)
    raise Outside(type(e).__name__)


def _in(e, neg: bool) -> tuple:
    if e.args.get("query") is not None or e.args.get("unnest") is not None:
        raise Outside("IN over a subquery")
    return ("in", expr(e.this), [expr(x) for x in e.expressions], neg)


def _ordered(x) -> tuple:
    if not isinstance(x, exp.Ordered):
        return (expr(x), False)
    return (expr(x.this), bool(x.args.get("desc")))


def _table(t) -> tuple:
    if not isinstance(t, exp.Table) or t.args.get("joins") or t.args.get("laterals"):
        raise Outside("a FROM that is not a relation")
    parts = [_ident(t.args.get(k)) for k in ("catalog", "db") if t.args.get(k)] + [_ident(t.this)]
    a = t.args.get("alias")
    alias = _ident(a.this) if a is not None and a.this is not None else None
    if a is not None and a.args.get("columns"):
        raise Outside("column aliases on a relation")
    return parts, alias


def select(s, top: bool = False) -> dict:
    if not isinstance(s, exp.Select):
        raise Outside(type(s).__name__)
    for bad in (() if top else ("with", "with_")) + ("laterals", "windows", "prewhere", "connect", "sample",
                "settings", "locks", "cluster", "distribute", "sort", "offset", "into"):
        if s.args.get(bad):
            raise Outside(bad)
    d = s.args.get("distinct")
    if d is not None and d.args.get("on") is not None:
        raise Outside("DISTINCT ON")
    items = []
    for x in s.expressions:
        if isinstance(x, exp.Alias):
            items.append((expr(x.this), _ident(x.args.get("alias"))))
        else:
            items.append((expr(x), None))
    frm = s.args.get("from_") or s.args.get("from")
    source = _table(frm.this) if frm is not None else None
    joins = []
    for j in s.args.get("joins") or []:
        side = (j.side or "").upper()
        kind = (j.kind or "").upper()
        if kind in ("SEMI", "ANTI", "ASOF", "POSITIONAL") or j.args.get("method"):
            raise Outside(f"{kind or j.args.get('method')} JOIN")
        k = side or ("CROSS" if kind == "CROSS" else "INNER")
        parts, alias = _table(j.this)
        on = j.args.get("on")
        using = [_ident(u) for u in j.args.get("using") or []]
        joins.append({"kind": k, "table": parts, "alias": alias,
                      "on": expr(on) if on is not None else None, "using": using})
    w = s.args.get("where")
    g = s.args.get("group")
    if g is not None and (g.args.get("all") or g.args.get("rollup") or g.args.get("cube")
                          or g.args.get("grouping_sets")):
        raise Outside("GROUP BY ALL / ROLLUP / CUBE")
    h = s.args.get("having")
    qf = s.args.get("qualify")
    o = s.args.get("order")
    lim = s.args.get("limit")
    limit = None
    if lim is not None:
        v = lim.args.get("expression")
        if not isinstance(v, exp.Literal) or v.is_string:
            raise Outside("a LIMIT that is not a number")
        limit = v.this
    return {"distinct": d is not None, "items": items, "source": source, "joins": joins,
            "where": expr(w.this) if w is not None else None,
            "group": [expr(x) for x in g.expressions] if g is not None else [],
            "having": expr(h.this) if h is not None else None,
            "qualify": expr(qf.this) if qf is not None else None,
            "order": [_ordered(x) for x in (o.expressions if o is not None else [])],
            "limit": limit}


def compound(t, top: bool = False) -> dict:
    """A select, or `UNION [ALL]` of selects, flattened left to right."""
    rest = []
    while isinstance(t, exp.Union):
        if t.args.get("order") or t.args.get("limit") or t.args.get("offset"):
            raise Outside("ORDER BY or LIMIT on a UNION")
        rest.insert(0, ("UNION" if t.args.get("distinct") else "UNION ALL",
                        select(t.expression)))
        t = t.this
    if isinstance(t, (exp.Intersect, exp.Except)):
        raise Outside(type(t).__name__)
    return {"first": select(t, top=top), "rest": rest}


def query(sql: str, dialect: str) -> dict:
    """The model's tree in the fragment, or raises `Outside`."""
    _DIALECT[0] = dialect or "duckdb"
    try:
        trees = sqlglot.parse(sql, read=dialect)
    except Exception as e:                                       # noqa: BLE001
        raise Outside(f"sqlglot could not parse it: {str(e)[:80]}") from None
    trees = [t for t in trees if t is not None]
    if len(trees) != 1:
        raise Outside("more than one statement")
    t = trees[0]
    w = t.args.get("with_") or t.args.get("with")
    ctes = []
    if w is not None:
        if w.args.get("recursive"):
            raise Outside("WITH RECURSIVE")
        for c in w.expressions:
            if c.args.get("alias") is not None and c.args["alias"].args.get("columns"):
                raise Outside("CTE column lists")
            ctes.append((_ident(c.args["alias"].this), compound(c.this)))
    return {"ctes": ctes, "body": compound(t, top=True)}


# ------------------------------------------------------------------ the canonical printing

def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _qs(xs) -> str:
    return "[" + " ".join(_q(x) for x in xs) + "]"


def _blist(xs) -> str:
    """How Show.lean prints its own list types: `[]`, or `[a b ]`."""
    return "[]" if not xs else "[" + " ".join(xs) + " ]"


def s_expr(e) -> str:
    k = e[0]
    if k == "col":
        return f"(col {_qs(e[1])} {_q(e[2])})"
    if k == "star":
        return f"(star {_qs(e[1])})"
    if k == "num":
        return f"(num {_q(e[1])})"
    if k == "str":
        return f"(str {_q(e[1])})"
    if k in ("null", "true", "false"):
        return f"({k})"
    if k == "bin":
        return f"(bin {_q(e[1])} {s_expr(e[2])} {s_expr(e[3])})"
    if k == "un":
        return f"(un {_q(e[1])} {s_expr(e[2])})"
    if k == "isnull":
        return f"(isnull {s_expr(e[1])}{' not' if e[2] else ''})"
    if k == "in":
        return f"(in {s_expr(e[1])} {_blist([s_expr(x) for x in e[2]])}{' not' if e[3] else ''})"
    if k == "between":
        return (f"(between {s_expr(e[1])} {s_expr(e[2])} {s_expr(e[3])}"
                f"{' not' if e[4] else ''})")
    if k == "case":
        op = s_expr(e[1]) if e[1] is not None else "(none)"
        ws = _blist([f"({s_expr(c)} {s_expr(v)})" for c, v in e[2]])
        el = s_expr(e[3]) if e[3] is not None else "(none)"
        return f"(case {op} {ws} {el})"
    if k == "cast":
        return f"(cast {s_expr(e[1])} {_q(e[2])})"
    if k == "trycast":
        return f"(trycast {s_expr(e[1])} {_q(e[2])})"
    if k == "filter":
        return f"(filter {s_expr(e[1])} {s_expr(e[2])})"
    if k == "fn":
        return f"(fn {_q(e[1])}{' distinct ' if e[2] else ' '}{_blist([s_expr(a) for a in e[3]])})"
    if k == "window":
        order = _blist([f"({s_expr(x)} {'desc' if d else 'asc'})" for x, d in e[3]])
        return f"(window {s_expr(e[1])} {_blist([s_expr(p) for p in e[2]])} {order})"
    raise ValueError(k)


def _opt(e) -> str:
    return s_expr(e) if e is not None else "(none)"


def s_select(s: dict) -> str:
    items = " ".join(f"({s_expr(e)} {_q(a) if a is not None else '(none)'})"
                     for e, a in s["items"])
    src = (f"(from {_qs(s['source'][0])} "
           f"{_q(s['source'][1]) if s['source'][1] is not None else '(none)'})"
           if s["source"] else "(none)")
    joins = " ".join(f"(join {_q(j['kind'])} {_qs(j['table'])} "
                     f"{_q(j['alias']) if j['alias'] is not None else '(none)'} "
                     f"{_opt(j['on'])} {_qs(j['using'])})" for j in s["joins"])
    order = " ".join(f"({s_expr(e)} {'desc' if d else 'asc'})" for e, d in s["order"])
    return (f"(select {'distinct ' if s['distinct'] else ''}[{items}] {src} [{joins}] "
            f"{_opt(s['where'])} [{' '.join(s_expr(g) for g in s['group'])}] "
            f"{_opt(s['having'])} {_opt(s['qualify'])} [{order}] "
            f"{_q(s['limit']) if s['limit'] is not None else '(none)'})")


def s_compound(c: dict) -> str:
    if not c["rest"]:
        return s_select(c["first"])
    rest = " ".join(f"({_q(op)} {s_select(x)})" for op, x in c["rest"])
    return f"(union {s_select(c['first'])} [{rest}])"


def s_query(q: dict) -> str:
    ctes = " ".join(f"({_q(n)} {s_compound(s)})" for n, s in q["ctes"])
    return f"(query [{ctes}] {s_compound(q['body'])})"


# ------------------------------------------------------------------ the Lean term

def _ls(s: str) -> str:
    """A name as the fragment stores it: its code points (see Syntax.lean on why)."""
    return "[" + ", ".join(str(ord(c)) for c in s) + "]"


def _lss(xs) -> str:
    return "[" + ", ".join(_ls(x) for x in xs) + "]"


def _lopt(v, f) -> str:
    return f"(some {f(v)})" if v is not None else "none"


def l_expr(e) -> str:
    k = e[0]
    if k == "col":
        return f"(Expr.col {_lss(e[1])} {_ls(e[2])})"
    if k == "star":
        return f"(Expr.star {_lss(e[1])})"
    if k == "num":
        return f"(Expr.num {_ls(e[1])})"
    if k == "str":
        return f"(Expr.str {_ls(e[1])})"
    if k == "null":
        return "Expr.null"
    if k in ("true", "false"):
        return f"(Expr.bool {k})"
    if k == "bin":
        return f"(Expr.bin {_ls(e[1])} {l_expr(e[2])} {l_expr(e[3])})"
    if k == "un":
        return f"(Expr.un {_ls(e[1])} {l_expr(e[2])})"
    if k == "isnull":
        return f"(Expr.isNull {l_expr(e[1])} {'true' if e[2] else 'false'})"
    if k == "in":
        return (f"(Expr.inList {l_expr(e[1])} (ExprList.ofList [{', '.join(l_expr(x) for x in e[2])}])"
                f" {'true' if e[3] else 'false'})")
    if k == "between":
        return (f"(Expr.between {l_expr(e[1])} {l_expr(e[2])} {l_expr(e[3])} "
                f"{'true' if e[4] else 'false'})")
    if k == "case":
        op = f"(OptExpr.some {l_expr(e[1])})" if e[1] is not None else "OptExpr.none"
        ws = "(WhenList.ofList [" + ", ".join(f"({l_expr(c)}, {l_expr(v)})" for c, v in e[2]) + "])"
        el = f"(OptExpr.some {l_expr(e[3])})" if e[3] is not None else "OptExpr.none"
        return f"(Expr.case {op} {ws} {el})"
    if k == "cast":
        return f"(Expr.cast {l_expr(e[1])} {_ls(e[2])})"
    if k == "trycast":
        return f"(Expr.tryCast {l_expr(e[1])} {_ls(e[2])})"
    if k == "filter":
        return f"(Expr.filtered {l_expr(e[1])} {l_expr(e[2])})"
    if k == "fn":
        return (f"(Expr.fn {_ls(e[1])} {'true' if e[2] else 'false'} "
                f"(ExprList.ofList [{', '.join(l_expr(a) for a in e[3])}]))")
    if k == "window":
        order = ", ".join(f"({l_expr(x)}, {'true' if d else 'false'})" for x, d in e[3])
        return (f"(Expr.window {l_expr(e[1])} (ExprList.ofList [{', '.join(l_expr(p) for p in e[2])}])"
                f" (OrderList.ofList [{order}]))")
    raise ValueError(k)


def l_select(s: dict) -> str:
    items = ", ".join(f"({l_expr(e)}, {_lopt(a, _ls)})" for e, a in s["items"])
    src = (f"(some ({_lss(s['source'][0])}, {_lopt(s['source'][1], _ls)}))"
           if s["source"] else "none")
    joins = ", ".join(
        f"{{ kind := {_ls(j['kind'])}, table := {_lss(j['table'])}, "
        f"alias := {_lopt(j['alias'], _ls)}, on := {_lopt(j['on'], l_expr)}, "
        f"usingCols := {_lss(j['using'])} }}" for j in s["joins"])
    order = ", ".join(f"({l_expr(e)}, {'true' if d else 'false'})" for e, d in s["order"])
    return ("{ distinct := " + ("true" if s["distinct"] else "false") + f", items := [{items}], "
            f"source := {src}, joins := [{joins}], where_ := {_lopt(s['where'], l_expr)}, "
            f"groupBy := [{', '.join(l_expr(g) for g in s['group'])}], "
            f"having := {_lopt(s['having'], l_expr)}, qualify := {_lopt(s['qualify'], l_expr)}, "
            f"orderBy := [{order}], limit := {_lopt(s['limit'], _ls)} }}")


def l_compound(c: dict) -> str:
    rest = ", ".join(f"({_ls(op)}, {l_select(x)})" for op, x in c["rest"])
    return f"{{ first := {l_select(c['first'])}, rest := [{rest}] }}"


def l_query(q: dict) -> str:
    ctes = ", ".join(f"({_ls(n)}, {l_compound(s)})" for n, s in q["ctes"])
    return f"{{ ctes := [{ctes}], body := {l_compound(q['body'])} }}"


def lean_codes(sql: str) -> str:
    """The model's text as the list of code points the proof evaluates."""
    return _ls(sql)
