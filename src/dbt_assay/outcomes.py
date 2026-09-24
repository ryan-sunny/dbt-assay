"""What the last build actually DID, as opposed to what the code says it would do.

*** ASSAY READS STRUCTURE AND THE FAILING POPULATION. IT NEVER READ OUTCOMES. ***
`manifest.json` for structure, `dbt_test__audit` for the rows a test failed on, `catalog.json` for
columns, `sources.json` for freshness. `run_results.json` appeared in exactly one place, to warn
that `dbt compile` overwrites it.

That file answers things structure cannot, and one of them is live on the field warehouse right
now: dbt applies a configured `limit` to the test query, so a failure count EQUAL to the limit is
the cap rather than the count. `assert_water_address_resolves` reported 500 against a real 6,251,
and every one of that project's 1,291 tests carries `+limit: 500`, so every failure count it has
ever printed may be an understatement and nothing says so.

*** OUTCOMES LIVE WHEREVER dbt RAN, WHICH IS NOT WHERE ASSAY RUNS. ***
On a real setup dbt runs on a box and assay runs on a laptop. So the file arrives by path --
`--run-results` -- rather than by assay being installed next to production to read a JSON file it
could read anywhere.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# dbt's own vocabulary. `pass`/`fail`/`warn` are test outcomes; `success`/`error` are node ones;
# `skipped` is both and is the one that matters most.
FAILED = ("fail", "error", "runtime error")
PASSED = ("pass", "success")


@dataclass
class Outcome:
    unique_id: str
    status: str
    failures: int | None = None
    message: str = ""


@dataclass
class Build:
    """One `run_results.json`, read."""
    generated_at: str = ""
    dbt_version: str = ""
    outcomes: dict = field(default_factory=dict)      # unique_id -> Outcome

    @property
    def n(self) -> int:
        return len(self.outcomes)


def read(path: str | Path) -> Build:
    """Load a run_results.json. Raises with the reason, because a silent empty Build would read
    exactly like a build in which nothing ran."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"no run_results.json at {p}. dbt writes it into its target directory on every "
            f"invocation, and `dbt compile` OVERWRITES it with a compile-only result -- so the "
            f"one you want is from the build you are asking about, not from a later compile.")
    data = json.loads(p.read_text())
    if "results" not in data:
        raise ValueError(f"{p} has no `results` key, so it is not a run_results.json.")
    meta = data.get("metadata") or {}
    b = Build(generated_at=str(meta.get("generated_at") or ""),
              dbt_version=str(meta.get("dbt_version") or ""))
    for r in data["results"]:
        uid = r.get("unique_id")
        if not uid:
            continue
        b.outcomes[uid] = Outcome(uid, str(r.get("status") or ""),
                                  r.get("failures"), str(r.get("message") or "")[:300])
    return b


def configured_limit(project, test_uid: str):
    """The `limit` dbt applied to this test's query, from the manifest. None when unset."""
    node = (project.raw.get("nodes", {}) or {}).get(test_uid) or {}
    lim = (node.get("config") or {}).get("limit")
    try:
        return int(lim) if lim is not None else None
    except (TypeError, ValueError):
        return None


def capped(project, build: Build) -> list[dict]:
    """Tests whose reported failure count IS the configured cap.

    *** "GOT 500 RESULTS" AGAINST A REAL 6,251 IS AN ORDER OF MAGNITUDE, REPORTED AS A FACT. ***
    dbt applies `limit` to the test query, so the number it prints is `min(real, limit)`. When
    those are equal the count carries no information about the real size except that it is at
    least that. Exact, free, and needs nothing but the two files.

    Equality is the whole test. A count BELOW the limit is a true count and is left alone.
    """
    out = []
    for uid, o in sorted(build.outcomes.items()):
        if o.failures is None:
            continue
        lim = configured_limit(project, uid)
        if lim is None or o.failures != lim:
            continue
        node = (project.raw.get("nodes", {}) or {}).get(uid) or {}
        out.append({"test": node.get("name", uid), "unique_id": uid,
                    "reported": o.failures, "limit": lim,
                    "model": node.get("attached_node", "") or "",
                    "file": node.get("original_file_path", "")})
    return out


def skipped(project, build: Build) -> list[dict]:
    """Nodes the build never ran.

    *** A SKIPPED TEST IS NOT A PASS, AND IT IS THE SENTENCE THIS WHOLE TIER OPENS WITH. ***
    Reported from the field: two model errors skipped 108 models in a 643-node build, and the
    alerting said "112 of 645 failed" with no distinction between failed and never ran. A test
    that did not execute asserted nothing, and a summary that counts it as neither failing nor
    outstanding has quietly shrunk the denominator.
    """
    out = []
    for uid, o in sorted(build.outcomes.items()):
        if o.status != "skipped":
            continue
        node = (project.raw.get("nodes", {}) or {}).get(uid) or {}
        out.append({"unique_id": uid, "name": node.get("name", uid),
                    "kind": node.get("resource_type", "")})
    return out


def coverage(project, build: Build) -> dict:
    """Of the models in this project, how many had an assertion actually EXECUTE in this build.

    Nothing else here computes it. `assay tests` says which models have a test declared; this says
    which had one run, and the gap between those two numbers is what a skipped build hides.
    """
    tests = {u: n for u, n in (project.raw.get("nodes", {}) or {}).items()
             if n.get("resource_type") == "test"}
    ran, models_with_a_test, models_asserted = 0, set(), set()
    for uid, n in tests.items():
        m = n.get("attached_node") or ""
        if m:
            models_with_a_test.add(m)
        o = build.outcomes.get(uid)
        if o is not None and o.status in PASSED + FAILED:
            ran += 1
            if m:
                models_asserted.add(m)
    total = len(project.models)
    return {"models": total,
            "models_with_a_test_declared": len(models_with_a_test),
            "models_an_assertion_ran_on": len(models_asserted),
            "tests_declared": len(tests), "tests_that_ran": ran,
            "tests_that_did_not_run": len(tests) - ran}


# --------------------------------------------------------------------------------- the loop

def confirmed_and_fixed(store, findings, unchecked=(), project=None) -> dict:
    """Of the findings a PERSON agreed with, how many are gone.

    *** EVERY OTHER NUMBER HERE MEASURES THE TOOL. THIS ONE MEASURES THE LOOP. ***
    `check` already prints "N new, N resolved" against the previous run, and that number cannot
    answer the question worth asking. Four fewer findings might be the four somebody agreed about,
    or four unrelated ones that moved while those four sat there. From the outside those look
    identical, and the second is what it looks like when reviewing changes nothing.

    A verdict filed against `(model, check)` cannot settle it either: one model carries eight
    findings of one check, so "you agreed about this model" does not say which of the eight was
    real. It needs the finding, which is why `assay review --load` writes an `agree` against each
    finding id the card showed.

    Gone counts when the CODE moved -- fixed, refactored away, or the model deleted. It does not
    claim the edit caused it; it claims the thing somebody said was real is no longer reported.

    *** BUT NOT WHEN ONLY ASSAY MOVED. *** Reported from the field: 0.51.1 fixed a grain
    derivation, a finding a person had agreed with stopped firing on a model whose file had not
    changed, and it was counted as fixed -- on the screen that says a release cannot move this
    number. A finding that goes while its model's file is unchanged is `retired`: reported beside
    the loop, never in it. `project` gives today's checksums; without it nothing can be told apart
    and every gone finding counts, as before. `still_open` is the other half and it is the backlog: read, agreed
    with, and not yet dealt with.
    """
    agreed = store.ruled_findings("agree") if store is not None else {}
    # *** A RETRACTION IS NOT A FIX. ***
    # Agreeing and later dismissing the same finding removes it from the report, and counting
    # that as "gone" would let somebody close the loop by changing their mind. `apply_policy`
    # drops a dismissed finding, so it is absent from `findings` for a reason that has nothing to
    # do with anybody fixing anything.
    if agreed and store is not None:
        for fid in store.ruled_findings("disagree"):
            agreed.pop(fid, None)
    if not agreed:
        return {"agreed": 0, "fixed": 0, "still_open": 0, "rows": []}
    here = {f.id for f in findings}
    # *** A FINDING THAT WAS RENAMED IS NOT FIXED. *** (N5) Adding a sort key changed an
    # arbitrary pick's summary and so its id, and the agreed finding counted as gone. An agreed id
    # that a current finding carries on is still here.
    if store is not None:
        from .store import carried
        for olds in carried(store, findings).values():
            here |= set(olds)
    # A finding of a check this run did not evaluate is not gone: nobody looked.
    if unchecked and store is not None:
        ids = list(agreed)
        skip = {r[0] for r in store.con.execute(
            "select distinct finding_id from findings where finding_id in (select unnest(?)) "
            "and check_name in (select unnest(?))", [ids, list(unchecked)]).fetchall()}
        for fid in skip:
            agreed.pop(fid, None)
        if not agreed:
            return {"agreed": 0, "fixed": 0, "still_open": 0, "rows": []}
    rows = []
    for fid, (who, when, note) in sorted(agreed.items()):
        rows.append({"finding": fid, "by": who, "at": str(when)[:10] if when else "",
                     "note": note, "gone": fid not in here})
    retired = []
    if project is not None and store is not None:
        for r in rows:
            if r["gone"] and _code_unchanged(store, project, r["finding"]):
                r["gone"], r["retired"] = False, True
                retired.append(r)
    rows_in = [r for r in rows if not r.get("retired")]
    gone = sum(1 for r in rows_in if r["gone"])
    # *** A THIRD NUMBER: FIXED, AND BACK. *** (G-B) Still open, and worse than never fixed: the
    # loop worked once and something undid it. Counted inside `still_open`, named apart.
    back = {r["agreed_id"] for r in returned(store, findings, project)} if store is not None \
        else set()
    for r in rows_in:
        if not r["gone"] and r["finding"] in back:
            r["regressed"] = True
    return {"agreed": len(rows_in), "fixed": gone, "still_open": len(rows_in) - gone,
            "regressed": sum(1 for r in rows_in if r.get("regressed")),
            "rows": rows_in, "retired": retired}


def returned(store, findings, project=None) -> list:
    """Agreed findings that were FIXED and are here again. (G-B)

    Fixed means what the loop counts as fixed: a person agreed it was real, a later full run no
    longer had it, and the model's file had changed -- not retired by an assay release, not
    absent because its check did not run. Returned means the current findings have it again,
    under its id or a reworded one (`store.finding_key`). The latest such gap is the one named.

    One dict per returned finding: the finding now, the id that was agreed, who agreed and when,
    the run and commit where it was gone, and the run and commit where it came back (`now` when
    it is this computation). Nothing here is written; `check` writes the findings as usual.
    """
    if store is None:
        return []
    from .store import finding_key
    agreed = store.ruled_findings("agree")
    for fid in store.ruled_findings("disagree"):
        agreed.pop(fid, None)
    if not agreed:
        return []
    now = {finding_key(f.check, f.subject, f.evidence): f for f in findings}
    try:
        rows = store.con.execute(
            "select distinct finding_id, check_name, subject, evidence, summary from findings "
            "where finding_id in (select unnest(?))", [list(agreed)]).fetchall()
        runs = store.con.execute(
            "select run_id, started_at, coalesce(git_sha, ''), unchecked from runs "
            "where scope is null order by started_at, run_id").fetchall()
    except Exception:                                            # noqa: BLE001
        return []
    order = {r[0]: i for i, r in enumerate(runs)}
    out, seen = [], set()
    for fid, check, subject, ev, summary in sorted(rows):
        key = finding_key(check, subject, ev)
        f = now.get(key)
        if f is None or key in seen:
            continue
        who, when, _note = agreed[fid]
        # Where this finding was, run by run: the runs that had it, and the checksum it had.
        had: dict = {}
        for rid, cs, e2 in store.con.execute(
                "select run_id, file_checksum, evidence from findings "
                "where check_name = ? and subject = ?", [check, subject]).fetchall():
            if rid in order and finding_key(check, subject, e2) == key:
                had[rid] = cs or ""
        gap = None
        for i in range(1, len(runs)):
            rid, at, sha, unchecked = runs[i]
            prev = runs[i - 1][0]
            if rid in had or prev not in had:
                continue
            if when is not None and at < when:
                continue                  # gone before anybody agreed: not a fix of the loop
            if check in (json.loads(unchecked) if unchecked else []):
                continue                  # the check did not run: nobody looked
            gap = (i, prev)
        if gap is None:
            continue
        i, prev = gap
        # Fixed means the FILE changed between the run that had it and the run that did not.
        # The gone run has no row for this finding, but any other finding on the model carries
        # the file's checksum then. Unknown counts as changed, as `confirmed_and_fixed` does.
        then = had.get(prev) or ""
        at_gone = store.con.execute(
            "select max(file_checksum) from findings where run_id = ? and subject = ?",
            [runs[i][0], subject]).fetchone()
        at_gone = (at_gone[0] if at_gone else "") or ""
        if not at_gone:
            # No finding on the model then (the usual shape of a fix): the version `check` kept
            # of it, the newest one first seen before the NEXT full run started -- the run that
            # saw it harvests it a moment after it starts.
            name = subject.split(".")[-1]
            nxt = runs[i + 1][1] if i + 1 < len(runs) else None
            got = store.con.execute(
                "select checksum from compiled_sql where model = ?"
                + (" and first_seen < ?" if nxt is not None else "")
                + " order by first_seen desc limit 1",
                [name, nxt] if nxt is not None else [name]).fetchone()
            at_gone = (got[0] if got else "") or ""
        if then and at_gone and then == at_gone:
            continue                      # the same file when it went: assay moved, not code
        back = next((runs[j] for j in range(i + 1, len(runs)) if runs[j][0] in had), None)
        seen.add(key)
        out.append({
            "finding": f, "agreed_id": fid, "agreed_by": who,
            "agreed_at": str(when)[:10] if when else "", "summary": summary,
            "gone_run": runs[i][0], "gone_at": str(runs[i][1])[:10], "gone_commit": runs[i][2],
            "back_run": back[0] if back else "now",
            "back_at": str(back[1])[:10] if back else "",
            "back_commit": back[2] if back else "",
        })
    return out


def fixed_finding_returned(store, findings, project=None) -> list:
    """The check: a finding somebody agreed with, that was fixed, is back. Base 3, and queued by
    default: a regression of something a person already paid to have fixed."""
    from .checks.structural import Finding
    out = []
    head = ""
    if project is not None:
        try:
            from . import history as history_mod
            repo = history_mod.repo_of(project)
            head = history_mod.head(repo) if repo else ""
        except Exception:                                        # noqa: BLE001
            head = ""
    for r in returned(store, findings, project):
        f = r["finding"]
        out.append(Finding(
            check="fixed_finding_returned",
            subject=f.subject, subject_name=f.subject_name, file=f.file,
            summary=f"came back after it was fixed: {f.check}: {f.summary[:120]}",
            detail=("A person agreed this finding was real, a later run no longer had it after "
                    "the model's file changed, and it is here again. Whatever fixed it was undone, "
                    "or a later edit reintroduced the same defect. It is counted as regressed, "
                    "and not as fixed again until it goes again."),
            base=3,
            evidence={"returned": r["agreed_id"], "check_returned": f.check,
                      # when and where, which move with every run and are not what the finding is
                      "timeline": {"agreed_by": r["agreed_by"], "agreed_at": r["agreed_at"],
                                   "gone_run": r["gone_run"], "gone_at": r["gone_at"],
                                   "gone_commit": r["gone_commit"], "back_run": r["back_run"],
                                   "back_at": r["back_at"],
                                   "back_commit": r["back_commit"] or head,
                                   "finding_now": f.id}},
            descendants=f.descendants, marts=f.marts,
        ))
    return out


def _code_unchanged(store, project, fid: str) -> bool:
    """True when the model a finding was last seen on has the same file now as then.

    The checksum is on the finding's own row from 0.51.2; before that, today's checksum having been
    kept in `compiled_sql` no later than that run says the same thing. Anything that cannot be
    told -- no model, no checksum, no record -- is NOT unchanged: it counts as gone, as it did.
    """
    try:
        row = store.con.execute("""
            select f.subject, f.file_checksum, r.started_at
            from findings f join runs r on r.run_id = f.run_id
            where f.finding_id = ? and r.scope is null
            order by r.started_at desc limit 1""", [fid]).fetchone()
    except Exception:                                            # noqa: BLE001
        return False
    if not row:
        return False
    uid, then, seen_at = row
    m = project.models.get(uid)
    now = getattr(m, "checksum", "") if m is not None else ""
    if not now:
        return False
    if then:
        return then == now
    # Kept by the run that last saw the finding (a moment after it started) or earlier: bounded by
    # the NEXT full run, since the run that saw it is also the one that harvested it.
    try:
        kept = store.con.execute("select first_seen from compiled_sql where checksum = ?",
                                 [now]).fetchone()
        nxt = store.con.execute(
            "select min(started_at) from runs where scope is null and started_at > ?",
            [seen_at]).fetchone()
    except Exception:                                            # noqa: BLE001
        return False
    if not kept or kept[0] is None:
        return False
    return nxt is None or nxt[0] is None or kept[0] < nxt[0]
