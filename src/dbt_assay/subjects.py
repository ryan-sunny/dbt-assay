"""Subjects a question can be asked about, built from the project without a bespoke call site.

*** A FAMILY WITH A NEW NAME USED TO BE LOADED, LINTED, LISTED, AND NEVER ASKED. ***
Every shipped family had its own hand-written caller that named it by string literal, so writing a
question was only half the job and the other half was a code change. Three custom families were
written on a real warehouse, linted at zero errors, shown by `assay banks` as `yours`, and did
nothing. They looked exactly like coverage.

A family declares the SUBJECT it wants and assay builds that state for it. `subject: expression`
gets one output expression at a time; `subject: window` gets one window function. Those two were
asked for by name in the field and had no call site at all.

*** THE STATE IS THE SMALLEST THING THAT CAN ANSWER THE QUESTION. ***
Measured: a claim alone read 0.96 and the same claim plus one CORRECT extra sentence read 0.47.
Every builder here sends the subject and the few facts that bear on it, never the whole model.
"""
from __future__ import annotations

from dataclasses import dataclass, field

KINDS = ("model", "edge", "column", "predicate", "expression", "window")


@dataclass
class Subject:
    kind: str
    key: str                       # stable, and what a decision is filed under
    uid: str                       # the model this belongs to, for blast radius and findings
    name: str                      # what a person would call it
    state: dict = field(default_factory=dict)
    file: str = ""


def _model_of(project, uid):
    return project.models.get(uid)


def build(kind: str, project, digests: dict, schema, limit: int = 0) -> list[Subject]:
    """Every subject of one kind in this project. Ordered so a --limit takes the reachable ones."""
    if kind not in KINDS:
        raise ValueError(f"unknown subject {kind!r}. Use one of {KINDS}.")
    fn = {"model": _models, "edge": _edges, "column": _columns,
          "predicate": _predicates, "expression": _expressions, "window": _windows}[kind]
    out = fn(project, digests, schema)
    # Most reachable first: a limit should spend itself where a defect costs most.
    out.sort(key=lambda s: -project.blast_radius(s.uid)["descendants"])
    return out[:limit] if limit else out


def _ok(digests, uid):
    d = digests.get(uid)
    return d if d is not None and d.ok else None


def _models(project, digests, schema) -> list[Subject]:
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None:
            continue
        out.append(Subject(
            "model", f"{uid}::model", uid, m.name, file=m.path,
            state={"model": m.name,
                   "columns": list(schema.columns(uid).names)[:50],
                   "filters": [p for p in (d.predicates_atomic or []) if p.strip() not in
                               ("1 = 1", "TRUE", "true")][:12],
                   "groups_by": list(d.group_by or [])[:8] or None,
                   "reads": [project.name_of(p) for p in (m.parents or [])][:8]}))
    return out


def _edges(project, digests, schema) -> list[Subject]:
    from . import relate
    facts, _ = relate.run_all(project, digests, schema)
    declared = relate.declared_keys(project)
    out = []
    for f in facts:
        cd = _ok(digests, f.child)
        if cd is None or not (f.joined_on or f.dropped):
            continue
        st = {"parent": {"model": f.parent_name, "declared_key": declared.get(f.parent) or None,
                         "columns": list(f.carried or [])[:20]},
              "child": {"model": f.child_name, "joins_on": list(f.joined_on or [])[:8],
                        "groups_by": list(cd.group_by or [])[:8] or None},
              "columns_the_child_drops": sorted(f.dropped or [])[:16] or None}
        pre = (cd.pre_aggregated or {}).get(f.parent_name)
        if pre is not None:
            st["the_child_already_collapsed_the_parent_before_joining"] = {
                "relation": f.parent_name, "to_one_row_per": pre or "a distinct"}
        out.append(Subject("edge", f"{f.child}::edge::{f.parent}", f.child,
                           f"{f.parent_name} -> {f.child_name}",
                           file=(_model_of(project, f.child) or _Blank()).path, state=_prune(st)))
    return out


def _columns(project, digests, schema) -> list[Subject]:
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None:
            continue
        roots = {**(d.output_roots or {}), **(d.resolved_roots or {})}
        for c in list(schema.columns(uid).names)[:80]:
            expr = (d.output_exprs or {}).get(c.lower()) or (d.output_exprs or {}).get(c)
            out.append(Subject(
                "column", f"{uid}::col::{c}", uid, f"{m.name}.{c}", file=m.path,
                state=_prune({"model": m.name, "column": c,
                              "expression": (expr or "")[:300] or None,
                              "derived_from": roots.get(c.lower()),
                              "other_columns": [x for x in schema.columns(uid).names
                                                if x != c][:25]})))
    return out


def _predicates(project, digests, schema) -> list[Subject]:
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None:
            continue
        for i, p in enumerate([x for x in (d.predicates_atomic or [])
                               if x.strip() not in ("1 = 1", "TRUE", "true")][:20]):
            out.append(Subject(
                "predicate", f"{uid}::pred::{i}", uid, f"{m.name}: {p[:48]}", file=m.path,
                state=_prune({"model": m.name, "predicate_under_judgement": p,
                              "the_models_other_filters":
                                  [x for x in (d.predicates_atomic or []) if x != p][:8],
                              "reads": [project.name_of(x) for x in (m.parents or [])][:6]})))
    return out


def _expressions(project, digests, schema) -> list[Subject]:
    """One output expression at a time. Asked for in the field and there was no call site."""
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None:
            continue
        for col, expr in list((d.output_exprs or {}).items())[:60]:
            if not expr or expr.strip().lower() == col.lower():
                continue                      # a bare passthrough asserts nothing
            out.append(Subject(
                "expression", f"{uid}::expr::{col}", uid, f"{m.name}.{col}", file=m.path,
                state=_prune({"model": m.name, "produces_column": col,
                              "expression": expr[:400],
                              "derived_from": (d.output_roots or {}).get(col.lower()),
                              "the_model_groups_by": list(d.group_by or [])[:8] or None,
                              "columns_it_reads": list(d.referenced_columns or [])[:25]})))
    return out


def _windows(project, digests, schema) -> list[Subject]:
    """One window function at a time. The other subject the field asked for by name."""
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None:
            continue
        for i, w in enumerate(d.windows or []):
            out.append(Subject(
                "window", f"{uid}::win::{i}", uid, f"{m.name} window {i + 1}", file=m.path,
                state=_prune({"model": m.name,
                              "where_it_sits": getattr(w, "position", None),
                              "partition_by": list(getattr(w, "partition_by", []) or [])[:8],
                              # *** `order_sql` IS THE FIELD. `order_roots` IS ['column']. ***
                              # Guessed `order_by` first, which does not exist, and fell back to
                              # the roots -- so the state carried the word "column" and 65% of
                              # answers were `cannot_tell`. A subject that cannot see the thing it
                              # is asked about produces a confident non-answer, which is the
                              # failure this whole tool is about.
                              "order_by": list(getattr(w, "order_sql", []) or [])[:8],
                              "order_is_reprojected":
                                  any(getattr(w, "order_reprojected", []) or []) or None,
                              "the_model_filters": [x for x in (d.predicates_atomic or [])
                                                    if x.strip() not in ("1 = 1",)][:8]})))
    return out


class _Blank:
    path = ""


def _prune(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, [], {}, "")}
