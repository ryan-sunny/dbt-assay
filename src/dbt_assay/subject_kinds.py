"""The subjects the build queue's new families are asked about, narrowed by code first.

*** CODE NARROWS, A JUDGMENT DECIDES, AND NOTHING IS ASKED THAT A PARSER COULD ANSWER. ***
Each builder here is the cheap exact half of a family: it finds the handful of places a question is
even meaningful -- a COALESCE onto a literal, an IN-list, a join on a date, a dedupe -- and hands the
judgment only those, with only the facts that bear on them. A family asked about every column of a
356-model warehouse would cost dollars and answer mostly "not applicable"; asked about the 13
defaulted columns it answers the question somebody has.

Every subject is keyed `<model uid>::<kind>::<what>`, so the generic runner files the answer under
the model and `finding_when` turns a defect answer into a finding on that model.
"""
from __future__ import annotations

import re

import sqlglot
from sqlglot import exp


def _parse(sql: str, dialect: str):
    try:
        return sqlglot.parse_one(sql, dialect=dialect or None)
    except Exception:                                               # noqa: BLE001
        return None


def _unwrap(node):
    while isinstance(node, (exp.Alias, exp.Paren, exp.Cast, exp.TryCast)):
        node = node.this
    return node


def _desc(m, col: str) -> str:
    from .subjects import described
    return str(described(m).get(col) or "")[:300]


def _ok(digests, uid):
    d = (digests or {}).get(uid)
    return d if d is not None and d.ok else None


# ------------------------------------------------------------------------------ defaults

def _default_of(expr: str, dialect: str):
    """(inner sql, default literal sql) when `expr` is COALESCE(..., <literal>), else None."""
    node = _unwrap(_parse(expr, dialect))
    if not isinstance(node, (exp.Coalesce,)):
        return None
    args = [node.this, *(node.expressions or [])]
    last = _unwrap(args[-1])
    if not isinstance(last, (exp.Literal, exp.Boolean)) and not (
            isinstance(last, exp.Neg) and isinstance(last.this, exp.Literal)):
        return None
    inner = ", ".join(a.sql(dialect=dialect or None) for a in args[:-1])
    return inner, args[-1].sql(dialect=dialect or None)


def defaults(project, digests, schema) -> list:
    """Every output column that is COALESCE onto a literal: the value that stands in for NULL."""
    from .subjects import Subject, _prune
    dialect = getattr(project, "dialect", "") or ""
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None:
            continue
        for col, expr in (d.output_exprs or {}).items():
            got = _default_of(expr or "", dialect)
            if not got:
                continue
            inner, lit = got
            out.append(Subject(
                "default", f"{uid}::default::{col}", uid, f"{m.name}.{col}", file=m.path,
                state=_prune({"model": m.name, "column": col,
                              "expression": " ".join(str(expr).split())[:300],
                              "the_value_it_stands_in_for": inner[:200],
                              "default": lit,
                              "column_description": _desc(m, col),
                              "model_description": str(getattr(m, "description", "") or "")[:300]
                              })))
    return out


# ------------------------------------------------------------------------------ what breaks

def _cannot_fail(project, digests) -> set:
    from .checks.structural import tests_that_cannot_fail
    out = set()
    for f in tests_that_cannot_fail(project, digests):
        col = str((f.evidence or {}).get("column") or "").lower()
        test = str((f.evidence or {}).get("test") or "")
        out.add((f.subject, col, test))
    return out


def column_risks(project, digests, schema) -> list:
    """Columns carrying a structural reason to go wrong silently, in models a mart reads.

    The signals are all parser facts: part of a declared or grouped key, joined on by a child, a
    CASE with no ELSE (a new category falls through to NULL), a COALESCE onto a literal, a window,
    a regex. A column with none of them, or in a model nothing downstream reads, is not asked.
    """
    from .relate import declared_keys
    from .subjects import Subject, _prune
    dialect = getattr(project, "dialect", "") or ""
    keys = declared_keys(project)
    dead = _cannot_fail(project, digests)
    tests: dict = {}
    for t in project.tests:
        if t.tests_model and t.column:
            tests.setdefault((t.tests_model, t.column.lower()), []).append(t)
    # *** WHO JOINS TO THIS MODEL ON THIS COLUMN -- NOT WHO JOINS ON THE NAME ANYWHERE. ***
    # The first version matched the column name across every join in the project and called
    # `geom_json` "joined on by 5 models". A join is a fact about a PARENT: a child whose join
    # targets this model's relation, on this column.
    rel = {u: str(r).replace('"', "").lower()
           for u, r in (getattr(schema, "relation", {}) or {}).items() if r}
    joined: dict = {}
    for cuid, cm in project.models.items():
        cd = _ok(digests, cuid)
        if cd is None:
            continue
        for j in cd.joins or []:
            target = str(j.target_relation or "").replace('"', "").lower()
            if not target:
                continue
            for c in [*(j.using or []), *(j.target_keys or [])]:
                joined.setdefault((target, str(c).lower()), set()).add(cm.name)
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None:
            continue
        radius = project.blast_radius(uid)
        if not radius["marts"]:
            continue
        key = {c.lower() for c in (keys.get(uid) or [])} | {
            c.lower() for c in (d.group_by_columns or [])}
        for col, expr in (d.output_exprs or {}).items():
            e = " ".join(str(expr or "").split())
            up = e.upper()
            signals = []
            if col.lower() in key:
                signals.append("part of this model's key")
            readers = sorted(n for n in joined.get((rel.get(uid, ""), col.lower()), set())
                             if n != m.name)
            if readers:
                signals.append(f"joined on by {len(readers)} model(s): {', '.join(readers[:4])}")
            if "CASE" in up and " ELSE " not in f" {up} ":
                signals.append("a CASE with no ELSE, so an unlisted value becomes NULL")
            if _default_of(e, dialect):
                signals.append("a COALESCE onto a literal")
            if " OVER " in f" {up} " or up.startswith("ROW_NUMBER") or " OVER(" in up:
                signals.append("assigned by a window function")
            if "REGEXP" in up or "RLIKE" in up or "SIMILAR TO" in up:
                signals.append("parsed out with a regex")
            if not signals:
                continue
            on_it = tests.get((uid, col.lower()), [])
            # A column already carrying a test that CAN fail has its risk covered; the question is
            # for the columns whose only protection is nothing, or a test that asserts nothing.
            if any((uid, col.lower(), t.name) not in dead for t in on_it):
                continue
            out.append(Subject(
                "column_risk", f"{uid}::risk::{col}", uid, f"{m.name}.{col}", file=m.path,
                state=_prune({
                    "model": m.name, "column": col, "expression": e[:300],
                    "why_it_could_go_wrong": signals,
                    "tests_on_it": [f"{t.kind or t.name}"
                                    + (" (cannot fail: it asserts what the SQL already "
                                       "guarantees)" if (uid, col.lower(), t.name) in dead
                                       else "") for t in on_it][:6] or ["none"],
                    "marts_downstream": radius["marts"],
                    "models_downstream": radius["descendants"],
                    "column_description": _desc(m, col)})))
    out.sort(key=lambda s: (-s.state.get("marts_downstream", 0), s.key))
    return out


# ------------------------------------------------------------------------------ filters

def enumerated_filters(project, digests, schema) -> list:
    """Filters that list values by hand: `x IN ('a', 'b', 'c')`, `NOT IN`, or three LIKEs."""
    from .subjects import Subject, _prune
    dialect = getattr(project, "dialect", "") or ""
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None:
            continue
        preds = [p for p in (d.predicates_atomic or []) if p and p.strip() not in ("1 = 1",)]
        for i, p in enumerate(preds):
            node = _parse(f"select 1 where {p}", dialect)
            if node is None:
                continue
            ins = [n for n in node.find_all(exp.In)
                   if len([x for x in (n.expressions or []) if isinstance(x, exp.Literal)]) >= 3]
            likes = list(node.find_all(exp.Like, exp.ILike))
            if not ins and len(likes) < 3:
                continue
            values = ([x.sql(dialect=dialect or None) for n in ins for x in n.expressions][:30]
                      or [x.expression.sql(dialect=dialect or None) for x in likes][:30])
            column = (ins[0].this.sql(dialect=dialect or None) if ins
                      else likes[0].this.sql(dialect=dialect or None))
            out.append(Subject(
                "enumerated_filter", f"{uid}::enum::{i}", uid, f"{m.name} filter {i + 1}",
                file=m.path,
                state=_prune({"model": m.name, "filter": p[:600], "column": column,
                              "values_listed": values, "count_listed": len(values),
                              "negated": " NOT " in f" {p.upper()} ",
                              "model_description": str(getattr(m, "description", "") or "")[:300],
                              "the_models_other_filters": [x for x in preds if x != p][:6]})))
    return out


# ------------------------------------------------------------------------------ units

# A token that names a unit. A column carrying one asserts a unit, and two models carrying the
# same name assert the SAME one -- which is the thing worth checking.
_UNIT = re.compile(r"(^|_)(acres?|af|acre_?feet|cfs|gpm|ft|feet|meters?|m|km|mi|miles|sqft|"
                   r"sq_?ft|pct|percent|usd|dollars|cents|kwh?|lbs|kg|days|hours|hrs|minutes|"
                   r"mins|seconds|secs|years|yrs)($|_)")


def same_name_measures(project, digests, schema) -> list:
    """Pairs of models producing a same-named, unit-bearing column, at least one computing it."""
    from .subjects import Subject, _prune
    by_col: dict = {}
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None:
            continue
        for col, expr in (d.output_exprs or {}).items():
            if not _UNIT.search(col.lower()):
                continue
            e = " ".join(str(expr or "").split())
            by_col.setdefault(col.lower(), []).append((uid, m, e))
    out = []
    for col, rows in sorted(by_col.items()):
        # *** A CARRY CANNOT DISAGREE ABOUT UNITS WITH WHAT IT CARRIES. ***
        # Most same-named pairs are one model computing a value and a descendant passing it
        # through; only two models that each COMPUTE it can assert different units under one name.
        computed = sorted((r for r in rows if r[2] and r[2].lower().split(".")[-1] != col),
                          key=lambda r: r[1].name)
        if len(computed) < 2:
            continue
        base = computed[0]
        for other in computed[1:4]:
            out.append(Subject(
                "same_name_measure", f"{base[0]}::units::{col}::{other[1].name}", base[0],
                f"{col}: {base[1].name} ~ {other[1].name}", file=base[1].path,
                state=_prune({"column": col,
                              "first_model": base[1].name, "first_expression": base[2][:250],
                              "first_description": _desc(base[1], col),
                              "second_model": other[1].name,
                              "second_expression": other[2][:250],
                              "second_description": _desc(other[1], col)})))
    return out


# ------------------------------------------------------------------------------ time grain

_TIME = re.compile(r"(date|day|month|year|week|quarter|period|_at$|_ts$|as_of|time)")


def time_joins(project, digests, schema) -> list:
    """Joins whose keys include a time column: where two time grains meet and can disagree."""
    from .subjects import Subject, _prune
    rel_to_uid = {str(r).replace('"', "").lower(): u
                  for u, r in (getattr(schema, "relation", {}) or {}).items() if r}
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None:
            continue
        for i, j in enumerate(d.joins or []):
            cols = [str(c).split(".")[-1].lower()
                    for c in [*(j.using or []), *(j.on_columns or [])]]
            times = sorted({c for c in cols if _TIME.search(c)})
            if not times:
                continue
            target_uid = rel_to_uid.get(str(j.target_relation or "").replace('"', "").lower())
            td = _ok(digests, target_uid) if target_uid else None
            tm = project.models.get(target_uid) if target_uid else None
            out.append(Subject(
                "time_join", f"{uid}::timejoin::{i}", uid, f"{m.name} join {i + 1}",
                file=m.path,
                state=_prune({
                    "model": m.name, "joins": tm.name if tm else str(j.target)[:120],
                    "join_kind": j.kind, "joined_on": cols[:8], "time_columns": times,
                    "this_model_groups_by": list(d.group_by or [])[:8],
                    "the_joined_model_groups_by": list(td.group_by or [])[:8] if td else None,
                    "time_expressions_here": {c: (d.output_exprs or {}).get(c, "")[:160]
                                              for c in times if (d.output_exprs or {}).get(c)},
                    "time_expressions_there": ({c: (td.output_exprs or {}).get(c, "")[:160]
                                                for c in times
                                                if (td.output_exprs or {}).get(c)}
                                               if td else None)})))
    return out


# ------------------------------------------------------------------------------ tie-breaks

def ranking_windows(project, digests, schema) -> list:
    """Every dedupe -- a window someone filters to one row per partition -- with its ordering."""
    from .relate import declared_keys
    from .subjects import Subject, _prune
    unique = {}
    for uid, cols in declared_keys(project).items():
        unique[uid] = list(cols)
    single = {t.column.lower() for t in project.tests if t.kind == "unique" and t.column}
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None:
            continue
        deduping = d.has_qualify or any("rn" in p_.lower() or "= 1" in p_
                                        for p_ in (d.predicates or []))
        if not deduping:
            continue
        for i, w in enumerate(d.windows or []):
            if not w.partition_columns or not w.order_sql:
                continue
            out.append(Subject(
                "ranking_window", f"{uid}::tie::{i}", uid, f"{m.name} dedupe {i + 1}",
                file=m.path,
                state=_prune({"model": m.name, "partition_by": list(w.partition_by)[:8],
                              "order_by": list(w.order_sql)[:8],
                              "columns_declared_unique_anywhere": sorted(
                                  c for c in single
                                  if any(c in o.lower() for o in w.order_sql))[:8],
                              "this_models_declared_key": unique.get(uid),
                              "the_model_filters": [x for x in (d.predicates_atomic or [])
                                                    if x.strip() not in ("1 = 1",)][:6]})))
    return out


# ------------------------------------------------------------------------------ sentinels

_SENTINEL_NUM = {"-9999", "-999", "9999", "99999", "-99999", "999999", "-999999", "-1111"}
_SENTINEL_DATE = re.compile(r"^'?(9999|2099|2100|1900|1899|0001|1800)-\d\d-\d\d")


def _sentinel_literals(node, dialect: str) -> list:
    out = []
    for lit in node.find_all(exp.Literal):
        s = lit.sql(dialect=dialect or None)
        parent = lit.parent
        v = ("-" + s) if isinstance(parent, exp.Neg) else s
        if v.strip("'") in _SENTINEL_NUM or v in _SENTINEL_NUM or _SENTINEL_DATE.match(s):
            out.append(v)
    return out


def sentinels(project, digests, schema) -> list:
    """Places a sentinel literal -- -9999, 9999-12-31, 1900-01-01 -- appears in a model's SQL."""
    from .subjects import Subject, _prune
    dialect = getattr(project, "dialect", "") or ""
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None:
            continue
        seen = 0
        for col, expr in (d.output_exprs or {}).items():
            node = _parse(f"select {expr}", dialect) if expr else None
            hits = _sentinel_literals(node, dialect) if node is not None else []
            if hits:
                out.append(Subject(
                    "sentinel", f"{uid}::sentinel::{col}", uid, f"{m.name}.{col}", file=m.path,
                    state=_prune({"model": m.name, "where": f"the column `{col}`",
                                  "sql": " ".join(str(expr).split())[:400],
                                  "sentinel_literals": sorted(set(hits))[:6],
                                  "column_description": _desc(m, col)})))
                seen += 1
        for i, p in enumerate(d.predicates_atomic or []):
            node = _parse(f"select 1 where {p}", dialect)
            hits = _sentinel_literals(node, dialect) if node is not None else []
            if hits and seen < 12:
                out.append(Subject(
                    "sentinel", f"{uid}::sentinel::filter{i}", uid, f"{m.name} filter {i + 1}",
                    file=m.path,
                    state=_prune({"model": m.name, "where": "a filter",
                                  "sql": p[:400], "sentinel_literals": sorted(set(hits))[:6]})))
                seen += 1
    return out


BUILDERS = {
    "default": defaults,
    "column_risk": column_risks,
    "enumerated_filter": enumerated_filters,
    "same_name_measure": same_name_measures,
    "time_join": time_joins,
    "ranking_window": ranking_windows,
    "sentinel": sentinels,
}

STATE_FIELDS = {
    "default": {"model", "column", "expression", "the_value_it_stands_in_for", "default",
                "column_description", "model_description", "what_one_row_of_this_model_is"},
    "column_risk": {"model", "column", "expression", "why_it_could_go_wrong", "tests_on_it",
                    "marts_downstream", "models_downstream", "column_description",
                    "what_one_row_of_this_model_is"},
    "enumerated_filter": {"model", "filter", "column", "values_listed", "count_listed", "negated",
                          "model_description", "the_models_other_filters",
                          "what_one_row_of_this_model_is"},
    "same_name_measure": {"column", "first_model", "first_expression", "first_description",
                          "second_model", "second_expression", "second_description",
                          "what_one_row_of_this_model_is"},
    "time_join": {"model", "joins", "join_kind", "joined_on", "time_columns",
                  "this_model_groups_by", "the_joined_model_groups_by", "time_expressions_here",
                  "time_expressions_there", "what_one_row_of_this_model_is"},
    "ranking_window": {"model", "partition_by", "order_by", "columns_declared_unique_anywhere",
                       "this_models_declared_key", "the_model_filters",
                       "what_one_row_of_this_model_is"},
    "sentinel": {"model", "where", "sql", "sentinel_literals", "column_description",
                 "what_one_row_of_this_model_is"},
}
