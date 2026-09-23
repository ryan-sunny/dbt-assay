"""The working tree's current meaning, and what it changed. Shared by `watch` and the MCP server.

*** THEY ARE THE SAME QUESTION POINTED AT DIFFERENT READERS. ***
A person wants a pane that stays quiet until something means something different. An agent wants to
ask, after an edit and before moving on, "did that change any contract?". Both are: derive the
contracts for what is on disk right now, diff against a baseline, report only what MOVED.

*** THE BASELINE IS A SNAPSHOT, NOT THE PREVIOUS KEYSTROKE. ***
Diffing against the last save means breaking something and fixing it produces two alarms. Diffing
against a snapshot means it produces none, which is correct: nothing ended up different.

*** A FILE THAT DOES NOT PARSE IS NOT A FINDING. ***
Mid-edit SQL is mid-edit, not broken. It is reported as "still typing" and never as a change.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from . import contracts, diff, inventory, relate
from .checks import run_all as structural_checks
from .infer import Schema, derive_columns
from .manifest import Project
from .parse import digest


@dataclass
class Snapshot:
    entries: list = field(default_factory=list)
    taken_at: float = 0.0
    by_name: dict = field(default_factory=dict)

    @classmethod
    def of(cls, entries) -> Snapshot:
        return cls(entries=entries, taken_at=time.time(),
                   by_name={e.name: e for e in entries})


@dataclass
class LiveState:
    project: Project
    digests: dict
    schema: Schema
    entries: list
    unparsed: list = field(default_factory=list)   # models mid-edit; NOT findings


def read(target: str | Path, store=None, observed=None) -> LiveState:
    project, digests, failures, schema = _load(target)
    entries = inventory.build(project, digests, schema, store, observed or {})
    return LiveState(project, digests, schema, entries,
                     unparsed=[name for _uid, name, _p, _e in failures])


def _load(target):
    project = Project.load(target)
    digests, failures = {}, []
    for uid, m in project.models.items():
        if not m.readable:
            continue
        d = digest(m.compiled, m.name)
        digests[uid] = d
        if not d.ok:
            failures.append((uid, m.name, m.path, d.error))
    schema = Schema.load(project, target)
    derive_columns(project, digests, schema)
    return project, digests, failures, schema


def new_findings(findings: list, baseline_rows: list[tuple]) -> list:
    """The findings the baseline run did not have. `baseline_rows` is (finding_id, check, subject).

    *** AN ID THAT MOVED IS NOT A NEW DEFECT. ***
    A finding's id hashes its summary, and a summary carries names and counts: editing a
    description can re-word `section_id is described 16 different ways` into 17 ways without
    anything new being wrong. So an exact id match settles most, and the rest are matched by
    COUNT per (check, subject): a model that had two `test_cannot_fail` findings and still has
    two has none new, whatever their wording. A third one is new, and which of the unmatched
    ones is reported as new is the one listed last, which is arbitrary and says so here rather
    than pretending the count identifies it.
    """
    ids = {r[0] for r in baseline_rows}
    had: dict = {}
    for _fid, check, subject in baseline_rows:
        had[(check, subject)] = had.get((check, subject), 0) + 1
    matched: dict = {}
    for f in findings:
        if f.id in ids:
            matched[(f.check, f.subject)] = matched.get((f.check, f.subject), 0) + 1
    out = []
    for f in findings:
        if f.id in ids:
            continue
        k = (f.check, f.subject)
        if matched.get(k, 0) < had.get(k, 0):
            matched[k] = matched.get(k, 0) + 1
            continue
        out.append(f)
    return out


def changes_since(baseline: Snapshot, state: LiveState) -> list:
    """Only what MOVED. A reformat, a renamed CTE, a join rewritten as a subquery: nothing."""
    return diff.compare(baseline.entries, state.entries, state.project, state.digests)


def all_findings(project, digests, schema, entries=None,
                 threshold: float = 0.8, store=None) -> list:
    """Every finding, structural and judged, from ONE place.

    *** THE CLI SAW SEVEN FAMILIES AND MCP SAW TWO. ***
    Reported from the field: a run held 162 findings across 7 families and `findings()` returned
    20 across 2. `hop_multiplies_rows` (58) and `description_contradicts_the_code` (18) were
    absent entirely, reachable only by querying the store by hand -- and those are exactly the
    ones worth an agent's time, because a description contradicting its code needs prose read
    against SQL, which is what an agent is for and a parser is not.

    The cause is the one this codebase keeps finding: two paths computing the same fact. `check`
    added the judged stream itself and `findings_for` never did, so "what is wrong with this
    project" had two answers depending on which surface you asked. There is one path now and both
    callers use it.

    `entries` carries the judged stream. Without a store there are no judged answers, so the
    structural half stands alone -- which is correct, not a truncation.
    """
    fs = structural_checks(project, digests)
    fs += relate.run_all(project, digests, schema)[1]
    if entries:
        from . import judged as judged_mod
        from . import practices as prac_mod
        fs += judged_mod.run_all(project, entries, relate.declared_keys(project), digests)
        # Counted rather than judged, and absent unless `--verify` filled `row_loss`. An
        # uncounted hop produces nothing here, which is correct: an absent measurement is not a
        # pass and the caller says the count did not run.
        fs += prac_mod.hop_drops_most_rows(project, entries, threshold)
        from .checks.structural import test_outruns_its_source
        fs += test_outruns_its_source(project, digests, schema, entries)
    # *** WHAT CHANGED, WHICH NEEDS TWO OBSERVATIONS AND SO NEEDS THE STORE. ***
    # A key that held last week and does not now is the failure that corrupts a warehouse, and it
    # is invisible to every other check here: they all describe the present. A column with one
    # observation produces nothing, which is correct -- an absent comparison is not a clean bill.
    if store is not None:
        from . import probe as probe_mod
        try:
            fs += probe_mod.changes(store, project)
        except Exception:                                        # noqa: BLE001
            # A store too old to hold a series still produces every other finding. The comparison
            # is absent, which is honest: `assay probe` twice is what makes it possible.
            return _distinct(fs)
    return _distinct(fs)


def _distinct(fs: list) -> list:
    """One row per finding, highest weight first.

    *** THE SAME FINDING WAS BEING EMITTED TWICE AND THREE TIMES. ***
    `water_reach_screen` reported one `arbitrary_pick` three times -- identical id, identical
    evidence -- because the same window appears more than once in the compiled SQL and the check
    walks each occurrence. On the field warehouse that is 4 duplicate rows of 258, which inflates
    every count derived from the list: the headline number, the per-check breakdown, and the
    review form, where a card drew the same sentence three times.

    A finding IS its id -- the check, the subject, the summary and the non-measured evidence -- so
    two rows carrying one id are one finding by the project's own definition. Deduped here, in the
    single stream every surface reads, rather than in each check that might repeat one.

    The sort is stable and the id breaks ties, so two findings of equal weight come back in one
    order across runs. Ordering by weight alone left that to the order they were appended.
    """
    seen, out = set(), []
    for f in fs:
        if f.id in seen:
            continue
        seen.add(f.id)
        out.append(f)
    return sorted(out, key=lambda f: (-f.weight, f.id))


def findings_for(state: LiveState, model: str | None = None, store=None) -> list:
    fs = all_findings(state.project, state.digests, state.schema, state.entries, store=store)
    if model:
        fs = [f for f in fs if f.subject_name == model]
    return fs


def contract_of(state: LiveState, model: str) -> dict | None:
    """Fifteen lines instead of two hundred. This is the whole argument for the MCP server."""
    e = next((x for x in state.entries if x.name == model), None)
    if e is None:
        return None
    return {
        "model": e.name,
        "path": e.path,
        "materialized": e.materialized,
        "grain": e.grain.value if e.grain else None,
        "grain_source": e.grain.source if e.grain else "unsettled",
        "grain_confidence": e.grain.confidence if e.grain else None,
        "reads": e.reads,
        "descendants": e.descendants,
        "marts_downstream": e.marts,
        "description": inventory.describe(e),
        "columns": [
            {"name": c.name, "role": c.role.value if c.role else None,
             "comes_from": c.provenance.value, "in_key": c.in_key}
            for c in e.columns
        ],
    }


def sql_files(target: str | Path, project_root: str | Path | None = None) -> dict:
    """{path: mtime} for the model SQL a watcher should react to."""
    root = Path(project_root or Path(target).parent)
    out = {}
    for p in root.rglob("*.sql"):
        s = str(p)
        if "/target" in s or "/dbt_packages" in s or "/.venv" in s:
            continue
        try:
            out[s] = p.stat().st_mtime
        except OSError:
            continue
    return out


def grain_candidates(state: LiveState, model: str) -> dict | None:
    """What code alone thinks one row is, before any judgment. Useful to an agent about to edit."""
    uid = next((u for u, m in state.project.models.items() if m.name == model), None)
    if uid is None:
        return None
    declared = relate.declared_keys(state.project)
    c = contracts.candidates(uid, state.project, state.digests, state.schema, {}, declared)
    return {"model": model, "candidates": c.columns if c else None,
            "route": c.route if c else None, "why": c.reason if c else "nothing settles it"}
