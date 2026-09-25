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

import json
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
    # *** THE COUNTED KEYS, OR MCP AND `check` COMPUTE DIFFERENT GRAINS. ***
    # Reported from the field: `check` built its entries with what `probe` observed and MCP never
    # passed it, so `contract` and `check` disagreed about what one row of a model is.
    if observed is None and store is not None:
        from . import probe as probe_mod
        observed = probe_mod.read(store)
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


def all_findings(project, digests, schema, entries=None, *,
                 threshold: float = 0.8, store=None, stored_evaluator: bool = True) -> list:
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

    *** `threshold` AND `store` ARE KEYWORD-ONLY, BECAUSE FIVE CALLERS HAD THEM SWAPPED. ***
    `plan`, `suggest`, `review --emit`, `read` and the resolved-cluster count passed the store as
    the threshold and 0.8 as the store. The key-change comparison below then raised on a float,
    the `except` read that as an old store, and `key_stopped_holding` -- the failure that
    corrupts a warehouse -- never reached any of them, silently.
    """
    # *** WITH THE SCHEMA, OR A DERIVED COLUMN IS INVISIBLE. ***
    # Reported from the field (25.13): `onboard` said 208 `column_has_no_description` and `check`
    # said 129 on the same manifest. The report took the stored 129 for the truth; it was the
    # wrong one. Without the schema a model's columns are only the DECLARED ones, so the 79
    # models whose undocumented columns are derived from SQL fell out of `check`, MCP and every
    # surface reading this stream, while `onboard` -- which passed it -- saw them.
    # *** EVERY PREMISE A CHECK LEANS ON IS RECORDED AS IT LEANS. *** The checks below read the
    # ledger through `ledger.active()`; `ledger.last()` holds it afterwards for `check` to write
    # and the page to show.
    from . import ledger as ledger_mod
    led = ledger_mod.build(project, schema, entries, store)
    ledger_mod.register_grains(led, entries)
    # A certificate `assay prove` wrote rests on its premises like any other dependent (L2).
    if store is not None:
        from . import prove as prove_mod
        prove_mod.register(led, prove_mod.stored(store))
    with ledger_mod.collecting(led):
        fs = _all_findings(project, digests, schema, entries, threshold, store)
    # *** EVIDENCE OF HARM TODAY, AS FINDINGS. *** A guarantee that stopped holding and a test
    # that fails reached no decision while they lived only on their own tabs.
    if store is not None:
        from . import harm
        extra = harm.guarantee_findings(project, store, led) + harm.failing_test_findings(
            project, store)
        if extra:
            fs = _distinct(_reach(project, fs + extra))
    if store is not None and stored_evaluator:
        fs = with_stored_warehouse(fs, store, project)
    return fs


# Findings only `check --verify` can produce: it read them from the warehouse through dbt.
# (The evaluator's cards are found by their evidence instead: some share assay's own names.)
WAREHOUSE_CHECKS = ("monitor_declared_but_never_run", "monitor_ran_then_stopped",
                    "test_declared_but_never_run", "test_skipped_rather_than_passed",
                    "volume_is_not_being_watched", "hop_drops_most_rows")


def with_stored_warehouse(fs: list, store, project) -> list:
    """The latest full run's warehouse findings, on a surface that computes offline.

    *** THE PAGE AND THE FORM NEVER SAW WHAT ONLY THE WAREHOUSE KNOWS. *** They rebuild the
    findings from the manifest and the store's answers. The monitoring findings (Elementary's
    tables), `hop_drops_most_rows` (counted rows) and dbt-project-evaluator's cards are what
    `check --verify` read through dbt, so on the page and the form they did not exist. They are
    read back from that run: a finding assay does not produce offline is rebuilt from its row, and
    a card of assay's own that the evaluator also flagged gets that evidence back (the id is the
    same, since the evidence is not in it). `check --verify` reads them fresh and passes
    `stored_evaluator=False`; a `check` without it carries them and says so.
    """
    import json

    from .checks.structural import Finding
    try:
        run = store.latest_run(getattr(project, "project_name", None) or None)
        rows = store.con.execute(
            """select check_name, subject, subject_name, file, summary, detail, base,
                      descendants, marts, evidence, exposures, finding_id
               from findings where run_id = ? and (evidence like '%"evaluator"%'
                                                   or check_name in ?)""",
            [run, list(WAREHOUSE_CHECKS)]).fetchall() if run else []
    except Exception:                                            # noqa: BLE001
        return fs
    by_id = {f.id: f for f in fs}
    for (check, subject, name, file, summary, detail, base, desc, marts, ev, exp,
         fid) in rows:
        try:
            evidence = json.loads(ev or "{}")
        except ValueError:
            continue
        mine = by_id.get(fid)
        if mine is not None:
            if "evaluator" in evidence and "evaluator" not in (mine.evidence or {}):
                mine.evidence = {**(mine.evidence or {}), "evaluator": evidence["evaluator"]}
                note = detail.split("\n\ndbt-project-evaluator flags this too", 1)
                if len(note) == 2:
                    mine.detail += "\n\ndbt-project-evaluator flags this too" + note[1]
            continue
        if check not in WAREHOUSE_CHECKS and "rules" not in (evidence.get("evaluator") or {}):
            continue                  # assay's own card, and assay no longer raises it
        f = Finding(check=check, subject=subject or "", subject_name=name or "", file=file or "",
                    summary=summary, detail=detail or "", base=int(base or 1),
                    evidence=evidence)
        f.descendants, f.marts = int(desc or 0), int(marts or 0)
        try:
            f.exposures = json.loads(exp or "[]")
        except ValueError:
            f.exposures = []
        fs.append(f)
        by_id[f.id] = f
    return fs


def _with_source_readings(fs: list, store) -> None:
    """A source's `source_volume_not_monitored` carries the answer `volume --judge` gave about
    it, the way a judged finding carries its own: asked, answered, how sure. (U2)"""
    from .contracts import QUESTIONS
    q = QUESTIONS.get("monitor_covers_what_matters") or {}
    prefix = q.get("id_prefix", "mcov")
    for f in fs:
        if f.check != "source_volume_not_monitored":
            continue
        try:
            rows = store.live_decisions("decision_key = ? and question = ?",
                                        [f"{f.subject}::unwatched", prefix])
        except Exception:                                        # noqa: BLE001
            return
        if not rows:
            continue
        _q, answer, conf, probs, ctx = rows[0][:5]
        try:
            p_ = float((json.loads(probs or "{}") or {}).get(answer, conf or 0) or 0)
        except (TypeError, ValueError):
            p_ = float(conf or 0)
        f.evidence = {**(f.evidence or {}), "asked": "monitor_covers_what_matters",
                      "answer": answer, "probability": round(p_, 3), "context": ctx or ""}


def _all_findings(project, digests, schema, entries, threshold, store) -> list:
    fs = structural_checks(project, digests, schema)
    if store is not None:
        _with_source_readings(fs, store)
    fs += relate.run_all(project, digests, schema)[1]
    # *** THE BRANCH `dbt compile` NEVER RENDERS. *** (G-D) Incremental models, read from their raw
    # code and config; the lateness and per-batch premises land in the ledger in force.
    from .checks import incremental as inc_mod
    fs += inc_mod.run_all(project, digests, schema, entries, store)
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
        # A measure summed in floating point: needs the judged roles, so it lives here. (G-C)
        from .checks.floats import float_sum_is_not_reproducible
        fs += float_sum_is_not_reproducible(project, digests, schema, entries)
    # *** WHAT CHANGED, WHICH NEEDS TWO OBSERVATIONS AND SO NEEDS THE STORE. ***
    # A key that held last week and does not now is the failure that corrupts a warehouse, and it
    # is invisible to every other check here: they all describe the present. A column with one
    # observation produces nothing, which is correct -- an absent comparison is not a clean bill.
    # *** A CLUSTER READ AS ONE RULE LANDS ON EACH MEMBER, NEVER ON THE CLUSTER. *** (25.24a)
    if store is not None:
        from . import clusters
        fs += clusters.findings(project, digests, store)
    if store is not None:
        from . import probe as probe_mod
        try:
            fs += probe_mod.changes(store, project)
        except Exception:                                  # noqa: BLE001, S110
            # A store too old to hold a series still produces every other finding. The comparison
            # is absent, which is honest: `assay probe` twice is what makes it possible.
            pass
        # *** LAST, BECAUSE IT READS EVERY OTHER FINDING. *** (G-B) An agreed finding that was
        # fixed and is here again.
        # `returned` reads a store too old to answer as nothing returned; anything else raises.
        from . import outcomes
        fs += outcomes.fixed_finding_returned(store, fs, project)
    return _distinct(_reach(project, fs))


def _reach(project, fs: list) -> list:
    """Stamp each finding with the exposures its subject reaches, in the ONE stream.

    Four producers copy `descendants` and `marts` onto their findings themselves. Exposures are
    stamped here instead, once, so no producer can forget -- and before `_distinct`, whose order is
    by weight and whose weight now counts them.
    """
    # The rules proven in Lean say so on their findings (L1).
    from .proofs import stamp
    stamp(fs)
    if not getattr(project, "exposures", None):
        return fs
    for f in fs:
        if f.subject:
            f.exposures = [e.title for e in project.exposures_of(f.subject)]
    return fs


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


def open_findings(project, digests, schema, entries, store, cfg, config_path=None) -> tuple:
    """(open findings, waived, {finding id: (action, why)}): what `check` reports, from ONE place.

    *** FOUR SURFACES, FOUR COUNTS. ***
    Reported from the field: `check` 978, MCP `findings` 931, `history` 468. `history` passed no
    entries, so every judged finding was absent; MCP `findings` had no store, so the cluster and
    config findings were absent, and applied no policy, so findings a person had DISMISSED were
    still listed. The same stream, the same self-audit, the same policy -- or two surfaces answer
    "what is open" differently and both are believed.
    """
    from . import judged
    fs = all_findings(project, digests, schema, entries, store=store,
                      threshold=getattr(cfg, "row_loss_threshold", 0.8))
    if store is not None and config_path is not None:
        from . import selfaudit
        fs += selfaudit.config_findings(config_path, store, cfg)
    kept, waived = judged.apply_policy(fs, cfg, store, project)
    return ([f for f, _a, _w in kept], waived, {f.id: (a, w) for f, a, w in kept})


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
        # *** DECLARED IS NOT THE SAME AS TRUE. *** (G-A) Whether the key's own test ran and
        # passed, was never run, or broke: {"status", "label", "why"} from the premise ledger.
        "grain_firm": e.grain.firm if e.grain else False,
        "grain_premise": (e.grain.premise or None) if e.grain else None,
        # How it loads after its first build, when it is incremental. (G-D)
        **({"incremental": _incremental_of(state, e.uid)} if e.materialized == "incremental"
           else {}),
        "reads": e.reads,
        "descendants": e.descendants,
        "marts_downstream": e.marts,
        # What outside the warehouse this model feeds, by the project's own exposures: the reason
        # an edit here is or is not somebody's product breaking.
        "exposures": list(getattr(e, "exposures", []) or []),
        "description": inventory.describe(e),
        "columns": [
            {"name": c.name, "role": c.role.value if c.role else None,
             "comes_from": c.provenance.value, "in_key": c.in_key}
            for c in e.columns
        ],
    }


def _incremental_of(state, uid: str) -> dict | None:
    from .checks import incremental as inc_mod
    got = inc_mod.read(state.project, state.digests).get(uid)
    return got.as_dict() if got is not None else None


def premises_report(project, digests, schema, entries, store, model: str = "",
                    status: str = "", include_packages: bool = False) -> dict:
    """What the findings rest on, for one model or all of them: the same rows the page's
    Guarantees tab shows, so `assay premises`, MCP `premises()` and the page cannot disagree."""
    from . import ledger as ledger_mod
    fs = all_findings(project, digests, schema, entries, store=store)
    led = ledger_mod.last()
    rows = ledger_mod.to_rows(led, store)
    raised: dict = {}
    for f in fs:
        back = (f.evidence or {}).get("why_it_is_back") or {}
        if back.get("premise_id"):
            raised.setdefault(back["premise_id"], []).append(
                {"finding": f.id, "check": f.check, "model": f.subject_name})
    for r in rows:
        r["raised"] = raised.get(r["id"], [])
    in_packages = sum(1 for r in rows if r.get("package"))
    if not include_packages:
        rows = [r for r in rows if not r.get("package")]
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ledger_mod.STATUSES}
    if model:
        uid = next((u for u, m in project.models.items() if m.name == model), None)
        if uid is None:
            return {"error": f"no model named {model}"}
        rows = [r for r in rows if r["relation"] == uid
                or any(u["model"] == uid for u in r["uses"])]
    if status:
        rows = [r for r in rows if r["status"] == status]
    return {"premises": rows, "counts_in_project": counts,
            "in_installed_packages": in_packages, "packages_included": include_packages,
            "counted": ledger_mod.counted_from(store) if store is not None else {},
            "tests_read": bool(led and led.tests_read),
            "note": ("" if led and led.tests_read else
                     "no test results were read, so every declared key is unchecked. `assay "
                     "volume` reads each test's last result from Elementary; a `dbt build` "
                     "leaves them in target/.")}


def proofs_report(project, digests, schema, entries, store, model: str = "") -> dict:
    """Every certificate `assay prove` wrote, with its premises' statuses now and the guarantee
    they make: the same rows the page shows. Lean does not run here."""
    from . import ledger as ledger_mod
    from . import prove as prove_mod
    all_findings(project, digests, schema, entries, store=store)
    led = ledger_mod.last()
    rows = prove_mod.with_guarantees(prove_mod.stored(store), led, project, store) \
        if store is not None else []
    if model:
        rows = [r for r in rows if r["model_name"] == model]
        if not rows and not any(m.name == model for m in project.models.values()):
            return {"error": f"no model named {model}"}
    for r in rows:
        pf = ledger_mod.parse_faithful(led, r["model"], store)
        r["parse"] = {"status": pf.status, "label": ledger_mod.label(pf), "why": ledger_mod.why(pf)}
    counts: dict = {}
    for r in rows:
        counts[r["guarantee"]] = counts.get(r["guarantee"], 0) + 1
    return {"proofs": rows, "by_guarantee": counts,
            "counted": ledger_mod.counted_from(store) if store is not None else {},
            "note": ("" if rows else "nothing has been proven here yet: `assay prove` writes the "
                                     "certificates (it needs Lean, which `assay prove --setup` "
                                     "installs)"),
            "trust": ("proven from the parsed structure: each certificate holds for every input "
                      "satisfying its premises; that the parse is the SQL is the `parse` premise")}


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
