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


def joins_parent_on_partial_key(project, digests: dict[str, Digest], schema) -> list[Finding]:
    """A child JOINS a parent on only part of that parent's declared key.

    *** THIS REPORTS A FACT, NOT A VERDICT, AND THAT IS DELIBERATE. ***
    The fan-out itself is exact and code establishes it. Whether it is a DEFECT is not structurally
    decidable, because a fan-out is frequently the mechanism rather than the mistake. Two real
    examples from one project:
      * a roll-up joins structures to parties on `wdid` alone ON PURPOSE, to reach one row per
        (reach, party), and collapses the two grains separately afterwards -- correct, and its
        header documents the double-count it already survived;
      * another joins on `(building_key, geography)` while the parent's own test declares the key
        as `(building_key, owner_key)`, with a comment asserting a third grain. Something there is
        wrong and nothing in the SQL says which.
    So severity is informational and the finding states the disagreement. Deciding it needs either
    the data (is the parent unique on the joined columns?) or a judgment about whether the
    downstream aggregation accounts for the inflation. That is the handoff to the judgment tier.

    The parent's own `unique_combination_of_columns` test says one row per (a, b). A child joining
    on `a` alone matches several parent rows per child row, and any count or sum over the result is
    inflated. Every model in the chain reviews clean on its own; the defect lives on the edge.

    *** AN EARLIER VERSION OF THIS FUNCTION WAS WRONG AND THE FIX IS WHY THIS ONE TAKES `schema`. ***
    It matched join keys GLOBALLY inside a model, which produced three false positives with three
    different causes: a join to a subquery that had already aggregated, a fan-out deliberately
    absorbed by `count(distinct ..)`, and a "parent" that was the model's FROM relation and was
    never joined to at all. Each one is now excluded by construction:
      * only relations that appear as an actual JOIN TARGET are considered, so a FROM relation and
        an aggregating subquery (whose target relation is None) are both out;
      * only the keys on the TARGET SIDE of that specific join count;
      * `count(distinct ..)` anywhere in the model drops the severity, because the inflation is
        absorbed rather than shipped.
    """
    keys = declared_keys(project)
    found = []
    seen: set = set()   # a model may join the same parent in several CTEs; that is one finding
    for uid, d in digests.items():
        if not d.ok:
            continue
        m = project.models[uid]
        targets: dict[str, list] = defaultdict(list)
        for j in d.joins:
            if j.target_relation and not j.target_aggregates:
                targets[j.target_relation.lower()].append(j)
        if not targets:
            continue
        refd = set(d.referenced_columns)

        for parent in m.parents:
            key = keys.get(parent)
            if not key or len(key) < 2:
                continue
            rel = (schema.relation.get(parent) or "").lower()
            for j in targets.get(rel, []):
                on_target = set(j.target_keys)
                hit = [k for k in key if k in on_target]
                missing = [k for k in key if k not in on_target]
                # If the missing key columns are referenced elsewhere in the model the join may be
                # qualified by a second clause this per-join view cannot see. Left alone, not guessed.
                if not hit or not missing or (set(missing) & refd):
                    continue
                sig = (uid, parent, tuple(hit))
                if sig in seen:
                    continue
                seen.add(sig)
                absorbed = d.absorbs_fanout
                found.append(Finding(
                    check="join_fans_out",
                    subject=uid, subject_name=m.name, file=m.path,
                    summary=(f"joins `{project.name_of(parent)}` on {hit}, but its declared key is "
                             f"{key}"),
                    detail=(f"`{project.name_of(parent)}` declares one row per {key} via its own "
                            f"unique_combination_of_columns test. This {j.kind} join matches on "
                            f"{hit} and the model never references {missing}, so each row here can "
                            f"match several parent rows."
                            + (" A DISTINCT in this model collapses the duplicates again, so "
                               "the fan-out is probably the intended mechanism here."
                               if absorbed else
                               " Nothing in this model collapses the duplicates. Check that "
                               "the aggregation accounts for the inflation, or that the "
                               "parent's declared key is still correct.")),
                    base=1,
                    evidence={"parent": project.name_of(parent), "declared_key": key,
                              "joined_on": hit, "missing": missing, "join_kind": j.kind,
                              "fanout_absorbed": absorbed},
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
def run_all(project, digests: dict[str, Digest], schema=None, *,
            include_informational: bool = False) -> tuple[list[EdgeFact], list[Finding]]:
    facts = edge_facts(project, digests)
    findings: list[Finding] = []
    if schema is not None:
        findings += joins_parent_on_partial_key(project, digests, schema)
    if include_informational:
        findings += columns_dropped_at_boundary(project, facts)
    for f in findings:
        b = project.blast_radius(f.subject)
        f.descendants, f.marts = b["descendants"], b["marts"]
    return facts, sorted(findings, key=lambda f: -f.weight)


def edge_state(f, cd, declared: dict, vocab: dict | None = None) -> dict:
    """What one hop looks like to a judge. Built HERE rather than in the command.

    *** IT LIVED INSIDE `assay traverse`'s LOOP, WHICH IS WHY IT COULD NOT BE REBUILT. ***
    Forty lines of dict assembly at a call site is a state only that call site can produce, and
    `assay stale --exact` has to produce it again from the same edge months later. Nothing about
    the logic changed on the way here.
    """
    st = {
        "parent": {"model": f.parent_name,
                   "declared_key": declared.get(f.parent) or None,
                   "columns": list(f.carried or [])[:25]},
        "child": {"model": f.child_name,
                  "declared_key": declared.get(f.child) or None,
                  "joins_on": list(f.joined_on or [])[:10],
                  "groups_by": list(cd.group_by or [])[:10] or None,
                  "uses_qualify": bool(getattr(cd, "has_qualify", False)) or None},
        "columns_the_child_drops": sorted(f.dropped or [])[:20] or None,
    }
    # *** WITHOUT THIS, EVERY WORD OF THE STATE IS TRUE AND THE CONCLUSION IS WRONG. ***
    # A parent collapsed inside a subquery before the join cannot fan the join out. 33% of 543
    # hops read `silently_multiplied` on a real warehouse, and the top one was exactly this shape.
    pre = (cd.pre_aggregated or {}).get(f.parent_name)
    if pre is not None:
        st["the_child_already_collapsed_the_parent_before_joining"] = {
            "relation": f.parent_name,
            "to_one_row_per": pre or "a distinct",
        }
    if f.parent_name in (cd.union_members or set()):
        st["the_child_reads_this_parent_as_one_arm_of_a_UNION"] = (
            "so one row of the parent is one row of the child. The child having more rows than "
            "this parent is the union, not a fan-out on this hop.")
    st = {k: v for k, v in st.items() if v}
    st["parent"] = {k: v for k, v in st["parent"].items() if v}
    st["child"] = {k: v for k, v in st["child"].items() if v}
    if vocab:
        st["vocabulary"] = vocab
    return st
