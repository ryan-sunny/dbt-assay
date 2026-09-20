"""Edge facts, and the defects that only exist between models.

*** THE EDGE IS THE UNIT, NOT THE PATH. ***
Measured on a 357-model project: 956 edges against 11,796 distinct source-to-mart paths, and 44% of
models join more than one parent. A model's row count is a function of ALL its inputs, so attrition
"along a path" is not merely expensive, it is ILL-DEFINED wherever a join sits -- and a confident
number for it would be a fiction. So facts are recorded per edge, and anything path-shaped is a
query over those facts.

*** THE DEFECT IS AN ABSENCE, AND IT IS FOUR MODELS UPSTREAM OF WHERE IT HURTS. ***
`case_number` joins correctly at staging because `division` is beside it. An intermediate model
drops `division` because it was not needed THERE. Three hops later a join on `case_number` alone is
wrong, and unfixable at that point because the disambiguator has left the lineage. Every model in
the chain reviews clean on its own. Nothing else in the dbt ecosystem looks for absences.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .checks.structural import Finding
from .parse import Digest


@dataclass
class EdgeFact:
    parent: str
    child: str
    parent_name: str
    child_name: str
    available: list[str] = field(default_factory=list)   # columns the parent offers
    carried: list[str] = field(default_factory=list)     # of those, what the child references
    dropped: list[str] = field(default_factory=list)     # available and not referenced
    joined_on: list[str] = field(default_factory=list)   # parent columns used as join keys


def _outputs(project, uid: str, digests: dict[str, Digest]) -> set[str]:
    """What a node offers downstream. A source declares its columns; a model computes them."""
    if uid in project.sources:
        return {c.lower() for c in project.sources[uid].columns}
    d = digests.get(uid)
    return {c.lower() for c in d.output_columns} if d and d.ok else set()


def edge_facts(project, digests: dict[str, Digest]) -> list[EdgeFact]:
    facts = []
    for parent, child in project.edges:
        cd = digests.get(child)
        if not cd or not cd.ok:
            continue
        avail = _outputs(project, parent, digests)
        if not avail:
            continue                                    # nothing declared: no honest fact to record
        refd = set(cd.referenced_columns)
        keys = cd.join_keys()
        facts.append(EdgeFact(
            parent=parent, child=child,
            parent_name=project.name_of(parent), child_name=project.name_of(child),
            available=sorted(avail),
            carried=sorted(avail & refd),
            dropped=sorted(avail - refd),
            joined_on=sorted(avail & keys),
        ))
    return facts


def declared_keys(project) -> dict[str, list[str]]:
    """Each model's key, AS THE PROJECT ITSELF DECLARES IT.

    *** CO-OCCURRENCE WAS THE WRONG SIGNAL AND WAS MEASURED TO BE. ***
    A first version inferred qualifier-hood from columns appearing in the same ON clause. On a real
    project that produced 96 findings and not one was defensible: `wdid` and `admin_number` sit
    together because they are a compound join condition, and `xmin/ymax/geom` cluster because they
    are a four-part spatial predicate. Neither is one column disambiguating another.

    Whether a key is unique WITHOUT its companion is a property of the DATA, not of the SQL, and a
    question the query text cannot settle belongs in another layer. But it does not need a model
    either, because dbt already carries the answer: `unique_combination_of_columns` declares a
    composite key, and a plain `unique` declares a single one. That is authoritative, exact, and
    free.
    """
    keys: dict[str, list[str]] = {}
    for t in project.tests:
        if not t.tests_model:
            continue
        if t.kind and t.kind.startswith("unique_combination"):
            cols = t.kwargs.get("combination_of_columns") or []
            if len(cols) >= 2:
                keys[t.tests_model] = [c.lower() for c in cols]
        elif t.kind == "unique" and t.column and t.tests_model not in keys:
            keys[t.tests_model] = [t.column.lower()]
    return keys


def joins_parent_on_partial_key(project, digests: dict[str, Digest],
                                facts: list[EdgeFact]) -> list[Finding]:
    """NOT IN the default run. Needs per-join relation resolution first.

    *** ALL THREE FINDINGS INSPECTED ON ITS FIRST RUN WERE FALSE POSITIVES, EACH FOR A DIFFERENT
    STRUCTURAL REASON. ***
      1. the join target was a SUBQUERY that already aggregated (`select wdid, any_value(..)
         group by 1`), so the grain was collapsed before the join and there was no fan-out;
      2. the fan-out was deliberate and consumed by `count(distinct ...)`;
      3. the "parent" was the model's FROM relation and was never joined to at all.

    The shared cause is this function matching join keys GLOBALLY within a model instead of
    resolving which relation each individual join targets. That resolution needs sqlglot's
    `qualify()` fed a real schema, built from inferred output columns in DAG order. Until that
    exists the check cannot tell a fan-out from an aggregate, and a check that cannot tell them
    apart must not be shipped.

    A child joins a parent on SOME of that parent's declared key. That is a fan-out.

    The parent's own `unique_combination_of_columns` test says one row per (a, b). A child joining
    on `a` alone therefore matches many parent rows per child row, and any aggregate over the result
    is inflated. Every model in the chain reviews clean on its own; the defect lives on the edge.
    """
    keys = declared_keys(project)
    by_child: dict[str, list[EdgeFact]] = defaultdict(list)
    for f in facts:
        by_child[f.child].append(f)

    found = []
    for uid, d in digests.items():
        if not d.ok:
            continue
        m = project.models[uid]
        used = d.join_keys()
        refd = set(d.referenced_columns)
        for f in by_child.get(uid, []):
            key = keys.get(f.parent)
            if not key or len(key) < 2:
                continue
            hit = {k for k in key if k in used}
            missing = [k for k in key if k not in used]
            # Joined on part of the key, and the rest is nowhere in this model at all. If the
            # missing columns ARE referenced here the join may be qualified in a second clause,
            # which the per-join view cannot see, so those are left alone rather than guessed at.
            if hit and missing and not (set(missing) & refd):
                found.append(Finding(
                    check="partial_key_join",
                    subject=uid, subject_name=m.name, file=m.path,
                    summary=(f"joins `{f.parent_name}` on {sorted(hit)}, but its declared key is "
                             f"{key}"),
                    detail=(f"`{f.parent_name}` declares one row per {key} via its own "
                            f"unique_combination_of_columns test. This model joins on "
                            f"{sorted(hit)} and never references {missing}, so each row here can "
                            f"match several parent rows. Any count or sum over the result is "
                            f"inflated by that factor."),
                    base=3,
                    evidence={"parent": f.parent_name, "declared_key": key,
                              "joined_on": sorted(hit), "missing": missing},
                ))
    return found


def columns_dropped_at_boundary(project, facts: list[EdgeFact],
                                min_dropped: int = 1) -> list[Finding]:
    """Columns a parent offers that a child never touches. Informational: this is usually correct."""
    found = []
    for f in facts:
        if len(f.dropped) >= min_dropped and f.available:
            share = len(f.dropped) / len(f.available)
            if share >= 0.8 and len(f.available) >= 5:
                m = project.models[f.child]
                found.append(Finding(
                    check="narrow_read",
                    subject=f.child, subject_name=f.child_name, file=m.path,
                    summary=f"reads {len(f.carried)} of {len(f.available)} columns from {f.parent_name}",
                    detail=("Most of what this parent offers is unused here. Usually deliberate. "
                            "Worth a glance when the parent is a staging model written from a "
                            "sample, which is how a column goes missing for everyone downstream."),
                    base=1,
                    evidence={"parent": f.parent_name, "dropped": f.dropped[:12],
                              "share_dropped": round(share, 2)},
                ))
    return found


# `narrow_read` is informational and fired 124 times on a real project, so it is opt-in rather
# than part of the default run. Volume without a verdict is how a findings list gets muted.
def run_all(project, digests: dict[str, Digest], *,
            include_informational: bool = False) -> tuple[list[EdgeFact], list[Finding]]:
    facts = edge_facts(project, digests)
    findings: list[Finding] = []
    if include_informational:
        findings += joins_parent_on_partial_key(project, digests, facts)
        findings += columns_dropped_at_boundary(project, facts)
    for f in findings:
        b = project.blast_radius(f.subject)
        f.descendants, f.marts = b["descendants"], b["marts"]
    return facts, sorted(findings, key=lambda f: -f.weight)
