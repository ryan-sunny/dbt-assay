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

import functools
import re

import sqlglot
from sqlglot import exp


@functools.lru_cache(maxsize=8192)
def _parse(sql: str, dialect: str):
    """Read-only trees, cached: the same expression is parsed for several subject kinds and
    several questions (32,074 parses on one run of `ask`). No caller here modifies one."""
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


# ------------------------------------------------------------------------------ arrival time

def arrival_candidates(project, digests, schema) -> list:
    """The time columns of an incremental model whose names do not already say which is the
    arrival: the only places `arrival_time_column` is worth asking. (G-D)"""
    from .checks import incremental as inc_mod
    from .subjects import Subject, _prune
    out = []
    for uid, i in sorted(inc_mod.read(project, digests).items()):
        event = i.event_time if i.strategy == "microbatch" else i.filter_column
        d = _ok(digests, uid)
        if not event or d is None:
            continue
        cols = [c.lower() for c in d.output_columns]
        if any(c in inc_mod.ARRIVAL_NAMES for c in cols):
            continue                                  # a loader column: its name already says
        m = project.models[uid]
        times = [c for c in cols if c != event.lower() and (_TIME.search(c) or c.endswith("_on"))]
        for c in times:
            out.append(Subject(
                "arrival_candidate", f"{uid}::arrival::{c}", uid, f"{m.name}.{c}", file=m.path,
                state=_prune({"model": m.name, "column": c, "event_column": event,
                              "expression": (d.output_exprs or {}).get(c, "")[:200],
                              "column_description": _desc(m, c),
                              "other_time_columns": [x for x in times if x != c][:8]})))
    return out


# ------------------------------------------------------------------------ how it is built
#
# The structure families (questions/structure.yml). Each builder is the count that decides where
# the question means anything: a staging model that joins, a mart that unions, a threshold in a
# filter. dbt Labs' layers read from the folder and the name, the way the project names them.

def _layer_kind(m) -> str:
    lay = str(getattr(m, "layer", "") or "").lower()
    name = str(getattr(m, "name", "") or "").lower()
    path = str(getattr(m, "path", "") or "").lower()
    if lay in ("staging", "base") or name.startswith(("stg_", "base_")) or "/staging/" in path:
        return "staging"
    if lay.startswith("int") or name.startswith("int_") or "/intermediate/" in path:
        return "intermediate"
    if lay.startswith("mart") or name.startswith(("fct_", "dim_", "fact_", "mart_")) \
            or "/marts/" in path:
        return "mart"
    return ""


def _yours(m) -> bool:
    return not getattr(m, "is_installed_package", False)


def staging_work(project, digests, schema) -> list:
    """Staging models that join, group or filter: the only ones where "is this staging work"
    means anything."""
    from .subjects import Subject, _prune
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None or not _yours(m) or _layer_kind(m) != "staging":
            continue
        joined = sorted({str(getattr(j, "target", "") or "") for j in (d.joins or [])} - {""})
        # a NULL check is cleaning, in either spelling sqlglot writes it
        filters = [p for p in (d.predicates_atomic or [])
                   if not re.fullmatch(r"(?is)\s*(?:not\s+)?[\w.\"]+\s+is\s+(?:not\s+)?null\s*",
                                       p)]
        if not (d.joins or d.group_by or filters):
            continue
        out.append(Subject("staging_work", f"{uid}::staging_work::model", uid, m.name,
                           file=m.path, state=_prune({
                               "model": m.name, "reads": sorted(d.relations)[:6],
                               "joins": joined[:8] or ([f"{len(d.joins)} join(s)"]
                                                       if d.joins else []),
                               "groups_by": list(d.group_by_columns or [])[:8],
                               "filters_it_applies": [" ".join(f.split())[:200]
                                                      for f in filters[:6]],
                               "model_description": str(getattr(m, "description", ""))[:400]})))
    return out


def grain_statements(project, digests, schema) -> list:
    """Marts with a description and a grain the SQL produces: does the one say the other."""
    from .subjects import Subject, _prune
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        desc = str(getattr(m, "description", "") or "").strip()
        if d is None or not _yours(m) or _layer_kind(m) != "mart" or not desc:
            continue
        grain = list(d.group_by_columns or []) or list((d.own_unique or (None,))[0] or [])
        if not grain:
            continue
        out.append(Subject("grain_statement", f"{uid}::grain_statement::model", uid, m.name,
                           file=m.path, state=_prune({"model": m.name, "description": desc[:600],
                                                      "grain_in_the_sql": grain[:8]})))
    return out


def intermediate_purposes(project, digests, schema) -> list:
    """Intermediate models with many steps: three joins or five CTEs."""
    from .subjects import Subject, _prune
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None or not _yours(m) or _layer_kind(m) != "intermediate":
            continue
        if len(d.joins or []) < 3 and len(d.ctes or []) < 5:
            continue
        out.append(Subject("intermediate_purpose", f"{uid}::intermediate_purpose::model", uid,
                           m.name, file=m.path, state=_prune({
                               "model": m.name, "ctes": list(d.ctes or [])[:12],
                               "joins": sorted(d.relations)[:10],
                               "output_columns": list(d.output_columns or [])[:30],
                               "model_description": str(getattr(m, "description", ""))[:400]})))
    return out


def mart_unions(project, digests, schema) -> list:
    """Marts that union several arms: whether every row is one kind of thing."""
    from .subjects import Subject, _prune
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None or not _yours(m) or _layer_kind(m) != "mart":
            continue
        if len(d.union_members or ()) < 2:
            continue
        out.append(Subject("mart_union", f"{uid}::mart_union::model", uid, m.name, file=m.path,
                           state=_prune({"model": m.name, "arms": sorted(d.union_members)[:10],
                                         "output_columns": list(d.output_columns or [])[:30],
                                         "model_description":
                                             str(getattr(m, "description", ""))[:400]})))
    return out


def mart_kinds(project, digests, schema) -> list:
    """Every mart: is it a fact or a dimension, and does its name say which."""
    from .subjects import Subject, _prune
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None or not _yours(m) or _layer_kind(m) != "mart":
            continue
        n = m.name.lower()
        says = ("fact" if n.startswith(("fct_", "fact_")) else
                "dimension" if n.startswith("dim_") else "neither")
        out.append(Subject("mart_kind", f"{uid}::mart_kind::model", uid, m.name, file=m.path,
                           state=_prune({"model": m.name, "name_says": says,
                                         "columns": list(d.output_columns or [])[:40],
                                         "one_row_is": list(d.group_by_columns or [])[:6],
                                         "model_description":
                                             str(getattr(m, "description", ""))[:300]})))
    return out


_THRESHOLD = re.compile(r"(?i)(?:[<>]=?|=|between)\s*(-?\d{2,}(?:\.\d+)?|'\d{4}-\d{2}-\d{2}')")


def business_literals(project, digests, schema) -> list:
    """A number of two digits or more, or a date, compared against in a filter; sentinels are
    their own family."""
    from .subjects import Subject, _prune
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None or not _yours(m):
            continue
        n = 0
        for i, p in enumerate(d.predicates_atomic or []):
            hit = _THRESHOLD.search(p)
            if not hit or _SENTINEL_DATE.match(hit.group(1)) or hit.group(1).strip("'") in \
                    _SENTINEL_NUM:
                continue
            out.append(Subject("business_literal", f"{uid}::business_literal::{i}", uid,
                               f"{m.name} filter {i + 1}", file=m.path, state=_prune({
                                   "model": m.name, "predicate": " ".join(p.split())[:300],
                                   "literal": hit.group(1),
                                   "model_description":
                                       str(getattr(m, "description", ""))[:300]})))
            n += 1
            if n >= 6:
                break
    return out


def clock_uses(project, digests, schema) -> list:
    """Models the counted check found reading today's date, outside a future-date bound."""
    from .subjects import Subject, _prune
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None or not _yours(m):
            continue
        uses = [p for p in (getattr(d, "patterns", None) or [])
                if p.get("kind") == "clock" and not p.get("guard")]
        if not uses:
            continue
        out.append(Subject("clock_use", f"{uid}::clock_use::model", uid, m.name, file=m.path,
                           state=_prune({"model": m.name,
                                         "uses": [" ".join(u["sql"].split())[:200]
                                                  for u in uses][:6],
                                         "materialized": getattr(m, "materialized", ""),
                                         "model_description":
                                             str(getattr(m, "description", ""))[:300]})))
    return out


def union_distincts(project, digests, schema) -> list:
    """Every UNION without ALL."""
    from .subjects import Subject, _prune
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None or not _yours(m):
            continue
        for i, p in enumerate(p for p in (getattr(d, "patterns", None) or [])
                              if p.get("kind") == "union_distinct"):
            out.append(Subject("union_distinct", f"{uid}::union_distinct::{i}", uid,
                               f"{m.name} union {i + 1}", file=m.path, state=_prune({
                                   "model": m.name, "arms": p.get("arms") or [],
                                   "model_description":
                                       str(getattr(m, "description", ""))[:300]})))
    return out


BUILDERS = {
    "default": defaults,
    "column_risk": column_risks,
    "enumerated_filter": enumerated_filters,
    "same_name_measure": same_name_measures,
    "time_join": time_joins,
    "ranking_window": ranking_windows,
    "sentinel": sentinels,
    "arrival_candidate": arrival_candidates,
    "staging_work": staging_work,
    "grain_statement": grain_statements,
    "intermediate_purpose": intermediate_purposes,
    "mart_union": mart_unions,
    "mart_kind": mart_kinds,
    "business_literal": business_literals,
    "clock_use": clock_uses,
    "union_distinct": union_distincts,
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
    "arrival_candidate": {"model", "column", "event_column", "expression", "column_description",
                          "other_time_columns", "what_one_row_of_this_model_is"},
    "staging_work": {"model", "reads", "joins", "groups_by", "filters_it_applies",
                     "model_description", "what_one_row_of_this_model_is"},
    "grain_statement": {"model", "description", "grain_in_the_sql", "what_one_row_of_this_model_is"},
    "intermediate_purpose": {"model", "ctes", "joins", "output_columns", "model_description", "what_one_row_of_this_model_is"},
    "mart_union": {"model", "arms", "output_columns", "model_description", "what_one_row_of_this_model_is"},
    "mart_kind": {"model", "name_says", "columns", "one_row_is", "model_description", "what_one_row_of_this_model_is"},
    "business_literal": {"model", "predicate", "literal", "model_description", "what_one_row_of_this_model_is"},
    "clock_use": {"model", "uses", "materialized", "model_description", "what_one_row_of_this_model_is"},
    "union_distinct": {"model", "arms", "model_description", "what_one_row_of_this_model_is"},
}


# ------------------------------------------------------------------------------ clusters
#
# *** FOUR KINDS THAT HAVE NO SINGLE-MODEL FORM. *** (25.24b) Each is a call site `clusters` builds
# for free; each state carries only what its one question needs. `where_the_fix_belongs` gets its
# own kind rather than a field on `predicate_cluster`, because its discriminator counts are extra
# sentences to the question beside it -- and `guide questions` measured one extra CORRECT sentence
# moving an answer from 0.96 to 0.47.

CLUSTER_KINDS = ("predicate_cluster", "predicate_cluster_route", "cluster_member", "claim_pair")


def cluster_subjects(kind: str, src) -> list:
    from . import clusters
    from .subjects import Subject
    project, digests = src.project, src.digests
    out = []
    if kind in ("predicate_cluster", "predicate_cluster_route"):
        for c in clusters.predicate_clusters(project, digests):
            if c.macro:
                # *** A RULE A MACRO ALREADY WRITES ONCE IS SETTLED BY CODE. *** Ten permit models
                # compile the same regex because one macro writes it; asking whether it is one
                # rule, or where its fix belongs, pays to be told what the manifest already says.
                continue
            first = c.members[0]
            if kind == "predicate_cluster":
                state = {"filter_shape": c.shape, "how_many_models": len(c.models),
                         "as_each_model_writes_it": [
                             _prune_none({"model": m.model, "filter": m.predicate,
                                          "column_description":
                                              _desc(project.models[m.uid], m.column)[:160]})
                             for m in sorted(c.members, key=lambda x: x.model)
                         ][:clusters.MAX_LISTED]}
                key = f"cluster::pred::{c.key}"
            else:
                state = {"filter_shape": c.shape, **clusters.route_facts(project, c)}
                key = f"cluster::route::{c.key}"
            out.append(Subject(kind, key, first.uid, f"{c.shape[:60]} in {len(c.models)} models",
                               file=project.models[first.uid].path,
                               state=_prune_none(state)))
    elif kind == "cluster_member":
        for o in clusters.odd_ones_out(project, digests):
            m = o.member
            out.append(Subject(kind, f"cluster::odd::{o.key}", m.uid, f"{m.model}: {m.fine[:60]}",
                               file=project.models[m.uid].path,
                               state=_prune_none({
                                   "shared_filter": o.shared,
                                   "how_many_models_write_it": len(o.sharing),
                                   # the columns, which the shape masks: `> 0` on acres and
                                   # `> 50000` on a sale price are not one rule
                                   "as_the_others_write_it": o.written,
                                   "this_model": m.model, "this_models_filter": m.predicate,
                                   "the_difference": o.difference,
                                   "what_this_model_says_about_it":
                                       clusters.what_it_says(project, m.uid, o.added)})))
    elif kind == "claim_pair":
        pairs, _settled = clusters.claim_pairs(src.store, project=project)
        for _j, a, b in pairs:
            out.append(Subject(kind, f"cluster::claims::{a['claim_id']}::{b['claim_id']}",
                               a["subject"], f"{a['subject_name']} ~ {b['subject_name']}",
                               state={"first_model": a["subject_name"], "first_claim": a["text"],
                                      "second_model": b["subject_name"],
                                      "second_claim": b["text"]}))
    return out


def _prune_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


STATE_FIELDS.update({
    "predicate_cluster": {"filter_shape", "how_many_models", "as_each_model_writes_it"},
    "predicate_cluster_route": {"filter_shape", "models", "how_many_models", "models_per_layer",
                                "distinct_sources_behind_all_of_them",
                                "a_shared_macro_already_writes_it"},
    "cluster_member": {"shared_filter", "how_many_models_write_it", "as_the_others_write_it",
                       "this_model", "this_models_filter", "the_difference",
                       "what_this_model_says_about_it"},
    "claim_pair": {"first_model", "first_claim", "second_model", "second_claim"},
})
