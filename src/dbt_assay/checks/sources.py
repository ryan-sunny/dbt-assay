"""Completeness at the edge of the warehouse: what was declared and never arrived, or never left.

*** assay ALREADY FOUND COMPLETENESS DEFECTS BY ACCIDENT, WHICH IS THE ARGUMENT FOR THE TIER. ***
`test_cannot_fail` flagged a `not_null` on `coalesce(ca.dwr_analysis_status, 'not looked up')` as
unable to fire -- a semantics finding. Reading it produced a completeness fact: 170,730 of 172,695
rows are that default, so the lookup has effectively never run. Four more columns on the same
warehouse are 71-96%.

The line that keeps this in scope and out of observability: **assay can say a column is 99% its
default. It cannot say whether that is bad.** The first is a fact about code and rows. The second
is intent, it refuses to guess at intent, and the ruling loop already exists for exactly that.

So no funnels, no conversion rates, no "row count fell 12% week over week". Every check here is
coverage of the project's OWN DECLARED INTENT: it declared a source, so something should read it;
it declared a freshness, so something should meet it.
"""
from __future__ import annotations

import json
from pathlib import Path

from .structural import Finding

EVALUATOR = "dbt_project_evaluator"


def _child_uids(project, uid: str) -> list[str]:
    """Raw children INCLUDING tests. `Source.children` is filtered to models, which is right for
    the DAG and wrong here: a source only a test reads is a different fact from an unread one."""
    return list((project.raw.get("child_map", {}) or {}).get(uid, []) or [])


def installed(project, package: str) -> bool:
    """Is this package part of the project? Pure manifest, no warehouse round trip."""
    return any(n.get("package_name") == package
               for n in (project.raw.get("nodes", {}) or {}).values())


READ_BY = "read_by"


def _declared_reader(project, uid: str) -> str:
    """`meta.read_by` on the source: a consumer OUTSIDE dbt, written down where it is declared.

    *** A MANIFEST CHECK CANNOT SEE A PYTHON READER, AND THE OBVIOUS ACTION IS TO DELETE. ***
    Reported from the field: `enriched_wells.well_documents` came back as read by nothing, which
    is true of the dbt graph and false of the warehouse -- `enrichment/well_scans.py` reads it.
    Its own description already says "the document index the scan reader works from", so a person
    had written it down and nothing could act on prose.

    `meta: {read_by: enrichment/well_scans.py}` is the same fact in a place code can read, and it
    stays inside the rule this whole tier follows: coverage of what the project ITSELF declares.
    """
    n = (project.raw.get("sources", {}) or {}).get(uid) or {}
    meta = {**(n.get("meta") or {}), **((n.get("config") or {}).get("meta") or {})}
    v = meta.get(READ_BY)
    return ", ".join(map(str, v)) if isinstance(v, (list, tuple)) else (str(v) if v else "")


def source_reaches_nothing(project, _digests=None) -> list[Finding]:
    """Declared, loaded on every run, and read by no model and no test IN THIS PROJECT."""
    out = []
    for uid, s in project.sources.items():
        if _child_uids(project, uid) or _declared_reader(project, uid):
            continue
        out.append(Finding(
            check="source_reaches_nothing", subject=uid,
            subject_name=f"{s.source_name}.{s.name}", file="",
            # *** SAY WHAT WAS ACTUALLY CHECKED. *** "Nothing reads it" is a claim about the
            # warehouse and only the dbt graph was looked at.
            summary=f"nothing in this dbt project reads `{s.source_name}.{s.name}`",
            detail=("No model and no test refers to this source. Either something was meant to "
                    "read it and does not -- a gap nothing else in this project reports -- or "
                    "the declaration outlived what used it.\n\n"
                    "A reader OUTSIDE dbt is invisible here: a Python enricher or a notebook "
                    "consuming this table looks exactly the same as nothing at all. Do not "
                    "delete on the strength of this finding. If something out of band reads it, "
                    f"record that once with `meta: {{{READ_BY}: path/to/reader.py}}` on the "
                    "source and this stops firing."),
            base=1, evidence={"source": s.source_name, "table": s.name, "schema": s.schema,
                              "checked": "the dbt graph only"}))
    return out


def source_only_a_test_reads(project, _digests=None) -> list[Finding]:
    """*** REPORTED APART FROM `reaches_nothing`, ON PURPOSE. ***

    They are different populations and different facts: 5 and 2 on the warehouse this was built
    against. This one says you are paying to TEST data that nothing consumes, which is a weaker
    finding and a real one.
    """
    out = []
    for uid, s in project.sources.items():
        kids = _child_uids(project, uid)
        if not kids or not all(k.startswith("test.") for k in kids):
            continue
        if _declared_reader(project, uid):
            continue
        out.append(Finding(
            check="source_only_a_test_reads", subject=uid,
            subject_name=f"{s.source_name}.{s.name}", file="",
            summary=f"`{s.source_name}.{s.name}` is tested and nothing reads it",
            detail=("Tests run against this source on every build and no model consumes it. The "
                    "tests are guarding data nothing downstream depends on."),
            base=1, evidence={"source": s.source_name, "table": s.name,
                              "tests": len(kids)}))
    return out


def source_freshness_undeclared(project, _digests=None) -> list[Finding]:
    """*** TIERED: SILENT WHEN `dbt_project_evaluator` IS IN THE PROJECT. ***

    The evaluator ships `fct_sources_without_freshness` and printing the same finding twice is
    worse than not printing it -- a reader cannot tell whether two tools agree or whether one is
    echoing the other. assay defers where somebody else already answers, and covers the case where
    nobody does, which is a project without the package.
    """
    if installed(project, EVALUATOR):
        return []
    raw = project.raw.get("sources", {}) or {}
    out = []
    for uid, s in project.sources.items():
        f = (raw.get(uid) or {}).get("freshness") or {}
        if (f.get("warn_after") or {}).get("count") or (f.get("error_after") or {}).get("count"):
            continue
        out.append(Finding(
            check="source_freshness_undeclared", subject=uid,
            subject_name=f"{s.source_name}.{s.name}", file="",
            summary=f"`{s.source_name}.{s.name}` declares no freshness",
            detail=("Nothing says how current this is supposed to be, so `dbt source freshness` "
                    "cannot check it and a feed going quiet would look exactly like a feed that "
                    "is up to date."),
            base=1, evidence={"source": s.source_name, "table": s.name}))
    return out


def source_freshness_stale(project, target_dir=None) -> list[Finding]:
    """A source whose OWN declared freshness it is not meeting, read from `sources.json`.

    *** THE FILE'S ABSENCE IS NOT A PASS. *** No `sources.json` means `dbt source freshness` has
    not run, which is different from every source being current, and the caller says which.
    """
    p = Path(target_dir or project.target_dir) / "sources.json"
    if not p.exists():
        return []
    try:
        results = (json.loads(p.read_text()) or {}).get("results") or []
    except (OSError, ValueError):
        return []
    out = []
    for r in results:
        status = str(r.get("status") or "").lower()
        if status not in ("warn", "error", "fail", "runtime error"):
            continue
        uid = r.get("unique_id") or ""
        s = project.sources.get(uid)
        name = f"{s.source_name}.{s.name}" if s else uid
        ago = r.get("max_loaded_at_time_ago_in_s")
        out.append(Finding(
            check="source_freshness_stale", subject=uid, subject_name=name, file="",
            summary=f"`{name}` is not meeting the freshness it declares ({status})",
            detail=("The project states how current this should be and the last load does not "
                    "meet it. Everything downstream is computing on data the project itself "
                    "says is too old."),
            base=2 if status != "warn" else 1,
            evidence={"status": status, "max_loaded_at": r.get("max_loaded_at"),
                      **({"hours_behind": round(float(ago) / 3600, 1)} if ago else {})}))
    return out


SOURCE_CHECKS = (source_reaches_nothing, source_only_a_test_reads, source_freshness_undeclared)
