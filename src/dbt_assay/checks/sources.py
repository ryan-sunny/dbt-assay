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


# *** A DEFERRAL NOBODY IS TOLD ABOUT IS A CHECK THAT STOPPED LOOKING. ***
# `source_freshness_undeclared` goes silent when dbt-project-evaluator is INSTALLED, on the
# argument that printing the same finding twice is worse than printing it once. The argument is
# right and the test was wrong: `installed` reads the MANIFEST, and a package being in the
# manifest is not its models being BUILT.
#
# Measured on the field warehouse: the evaluator is installed, `fct_sources_without_freshness` is
# NOT built -- 4 of its tables exist -- and 0 of 212 sources declare freshness. assay was silent
# because somebody else was covering it, that somebody said nothing, and nobody was told.
#
# `practices.py` already learned this for its own reads: "an absent table and an empty one are not
# the same fact, and only one of them is a pass." It learned it for reads and not for deferrals.
#
# The fix is not to stop deferring -- assay cannot see whether the table is built without a
# warehouse round trip, and this check is in the free tier. The fix is to SAY SO.
DEFERRED: list[tuple[str, str]] = []


def installed(project, package: str) -> bool:
    """Is this package part of the project? Pure manifest, no warehouse round trip.

    NOTE: this answers "declared in packages.yml and fetched", NOT "its models are built". A
    caller deferring on this must record the deferral in `DEFERRED` so the silence is visible.
    """
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


def seed_reaches_nothing(project, _digests=None) -> list[Finding]:
    """A seed loaded on every build that no model and no test reads.

    *** ASSAY'S OWN CHECK, POINTED AT ASSAY'S OWN OUTPUT, AND IT COULD NOT FIRE. ***
    `source_reaches_nothing` iterates `project.sources`. A seed is a node, not a source, so 80% of
    the payload `assay export` writes -- `model_decisions`, `edge_facts`, `observed_keys` on a real
    warehouse -- was loaded on every build, read by nothing, and invisible to the check built to
    find exactly that. Reported from the field, about this tool.

    A seed is costlier to leave unread than a source, not cheaper: it is a file in the repository
    that somebody maintains and `dbt seed` rebuilds, so an unread one is work being done twice.
    """
    out = []
    for uid, n in sorted((project.raw.get("nodes", {}) or {}).items()):
        if n.get("resource_type") != "seed":
            continue
        # A seed from an installed package is not the user's to delete, and saying so about
        # somebody else's file is noise. Same rule the model list uses.
        if n.get("package_name") and n.get("package_name") != project.project_name:
            continue
        if _child_uids(project, uid) or _declared_reader(project, uid):
            continue
        name = n.get("name", uid)
        out.append(Finding(
            check="seed_reaches_nothing", subject=uid, subject_name=name,
            file=n.get("original_file_path", ""),
            summary=f"nothing in this dbt project reads the seed `{name}`",
            detail=("A seed is a file somebody maintains and `dbt seed` loads on every build. No "
                    "model and no test refers to this one, so the load and the maintenance are "
                    "both being paid for and neither is being used.\n\n"
                    "The same limit applies as for a source: a reader OUTSIDE dbt is invisible "
                    "here. If something out of band reads it, say so with "
                    "`meta: {read_by: path/to/it.py}` and this stops reporting it."),
            base=1, evidence={"path": n.get("original_file_path", "")}))
    return out


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
    raw = project.raw.get("sources", {}) or {}
    if installed(project, EVALUATOR):
        n = sum(1 for uid in project.sources
                if not ((raw.get(uid) or {}).get("freshness") or {}).get("warn_after", {}).get("count")
                and not ((raw.get(uid) or {}).get("freshness") or {}).get("error_after", {}).get("count"))
        if n:
            DEFERRED.append((
                "source_freshness_undeclared",
                (f"{n} of {len(project.sources)} source(s) declare no freshness. Not reported "
                 f"here because dbt-project-evaluator is installed and ships "
                 f"`fct_sources_without_freshness` -- but only if that model is BUILT. If it is "
                 f"not, nobody is checking this.")))
        return []
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


def exposure_undeclared(project, _digests=None) -> list[Finding]:
    """A model of this project that no model reads and no exposure says what does.

    *** THE CANDIDATES ARE EVIDENCE; THE DECLARATION IS THE PROJECT'S. ***
    A model nothing inside dbt reads is either dead or read from outside -- a dashboard, an app, a
    report -- and an exposure is where a project writes the second down. assay can find the
    candidates from the graph. It cannot know the name, the owner or the URL, and does not propose
    them: the same rule as the empty `means:`. Absence of a declaration is not a defect, so this is
    coverage, shaped like `column_has_no_description` (25.24c).

    Silent where `meta.read_by` already names the reader, as it is for a source.
    """
    out = []
    for uid, m in sorted(project.models.items()):
        if m.is_installed_package or m.children or project.exposures_of(uid):
            continue
        if (m.meta or {}).get(READ_BY):
            continue
        out.append(Finding(
            check="exposure_undeclared", subject=uid, subject_name=m.name, file=m.path,
            summary=f"no model reads `{m.name}` and no exposure says what does",
            detail=("Nothing inside this dbt project reads this model, so either it is dead or "
                    "something outside the warehouse reads it -- a dashboard, an app, a report. "
                    "An `exposures:` entry naming it under `depends_on` is where the project says "
                    "which, and it is what lets assay rank a finding by the product it reaches "
                    "rather than by how many marts sit downstream.\n\n"
                    "The name, the owner and the URL are yours to write; assay does not guess them. "
                    "If the reader is a script rather than a product, `meta: {read_by: path}` on the "
                    "model says so and this stops reporting it."),
            base=1, evidence={"layer": m.layer}))
    return out


def _tests_key(project) -> str:
    """`data_tests` from dbt 1.8, `tests` before it."""
    v = str(((project.raw or {}).get("metadata") or {}).get("dbt_version") or "")
    try:
        major, minor = (int(x) for x in v.split(".")[:2])
    except ValueError:
        return "data_tests"
    return "data_tests" if (major, minor) >= (1, 8) else "tests"


def source_volume_not_monitored(project, _digests=None) -> list[Finding]:
    """A source with no row-count monitor, whose change reaches a mart with nothing watching.

    *** ONE PER SOURCE, NEVER ONE PER MODEL. *** (sunny-data feedback U2) The judged question
    asked about 222 models, several of them built from a source that WAS monitored. Whether
    something is watched is in the manifest, and a gap is a source: add the monitor there and
    every model it feeds is covered. The marts it reaches and the yml to add come with it; the
    judgment left to a person is only whether this source is worth watching at all (a fixed list
    is not), and `assay volume --judge` asks that once per source and shows the reading here.

    Elementary's monitor, so silent where Elementary is not installed.
    """
    if not installed(project, "elementary"):
        return []
    from ..elementary import unmonitored_sources
    raw = (project.raw or {}).get("sources") or {}
    key = _tests_key(project)
    out = []
    for uid, name, marts, via in unmonitored_sources(project):
        s = project.sources[uid]
        yml = (f"sources:\n  - name: {s.source_name}\n    tables:\n      - name: {s.name}\n"
               f"        {key}:\n          - elementary.volume_anomalies")
        out.append(Finding(
            check="source_volume_not_monitored", subject=uid, subject_name=name,
            file=(raw.get(uid) or {}).get("original_file_path", "") or "",
            summary=(f"`{name}` reaches {len(marts)} mart(s) and no volume monitor watches it "
                     f"or any model between it and them"),
            detail=("If this feed stopped arriving or doubled, nothing would fail: every test "
                    "downstream checks each row, and none checks how many arrived. Worth "
                    "watching when it is loaded from outside this project; not when it is a "
                    "fixed list.\n\nTo watch it, in the source's yml:\n\n" + yml),
            base=1, evidence={"source": s.source_name, "table": s.name, "marts": marts[:25],
                              "marts_reached": len(marts), "models_on_the_way": len(via),
                              "yml": yml}))
    return out


SOURCE_CHECKS = (source_reaches_nothing, source_only_a_test_reads, source_freshness_undeclared,
                 seed_reaches_nothing, exposure_undeclared, source_volume_not_monitored)

# *** THE COMPLETENESS REPORT SELECTED ITS MEMBERS BY NAME PREFIX. ***
# `f.check.startswith("source_")`, which is a hand-written membership rule standing in for the
# real list -- the same shape as the debt model's hand-written class list that put 121 of 236 rows
# in `other`. `seed_reaches_nothing` was registered, fired seven times on the field warehouse, and
# reached the report as a zero, because its name does not begin with `source`.
#
# Derived from the functions themselves, so registering a check IS wiring it in.
def completeness_checks() -> set[str]:
    """Every check name the completeness tier owns."""
    return {f.__name__ for f in SOURCE_CHECKS} | {"source_freshness_stale", "hop_drops_most_rows"}
