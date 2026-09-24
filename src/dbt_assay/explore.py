"""Everything assay knows about one warehouse, as one serialisable object.

*** THE KNOWLEDGE WAS ALWAYS THERE. IT WAS SPREAD ACROSS NINE COMMANDS. ***
`contract` knows the grain, `trace` knows where a column came from, `traverse` knows what each
hop carries, `claims` knows what the project says about itself, and the store knows what was
answered and who ruled on it. A person asking "what IS this model" had to run four commands and
hold the answers in their head.

This assembles all of it once, in a shape a page can render without asking anything further. It
is the half that has nothing to do with HTML: the same object serves a file, a server, or a
future surface nobody has written yet.

*** DETERMINISTIC OR IT CANNOT BE COMMITTED. ***
Every list here is sorted on a stable key and no wall clock is read. The same store and the same
manifest produce byte-identical output, which is the property the whole file-over-server argument
rests on -- and a dict iterating in insertion order is not a sort, so the sorts are explicit.
"""
from __future__ import annotations

import json
from typing import Any


def _fact(f) -> dict | None:
    """A Fact as data, with its provenance, because a value without its source is a rumour."""
    if f is None:
        return None
    return {"value": f.value, "source": f.source,
            "confidence": round(f.confidence, 4) if f.confidence is not None else None,
            "resting_on": getattr(f, "resting_on", None) or None,
            "note": getattr(f, "note", "") or "",
            # Which relation a passed-through value was read from. Empty for anything computed
            # here, which is correct: there is no upstream to name.
            "origin": getattr(f, "origin", "") or "",
            "root": getattr(f, "root", "") or ""}


# *** THE DISTRIBUTION IS THE SINGLE BIGGEST THING IN THE FILE AND THE LEAST READ. ***
# Measured on a 358-model warehouse: the full `probabilities` blob on every stored answer is a
# large share of the embedded payload, and it matters only when somebody is auditing one specific
# answer. The chosen answer, its confidence and the RUNNER-UP carry the part a reader acts on --
# "it said X at 0.62 and the next best was Y at 0.31" is the sentence people actually want -- at a
# fraction of the bytes. `assay review -i` still shows the whole distribution.
def _runner_up(probs: dict, answer) -> list | None:
    if not probs:
        return None
    rest = sorted(((v, k) for k, v in probs.items() if k != answer), reverse=True)
    if not rest:
        return None
    return [rest[0][1], round(float(rest[0][0]), 4)]


def _json(s, empty):
    """Parse a stored JSON column WITHOUT changing its type.

    *** `json.loads(s) or {}` TURNS AN EMPTY LIST INTO AN EMPTY DICT. ***
    `edge_facts.joined_on` is a list. A hop with no join key stores `"[]"`, which parses to `[]`,
    which is falsy, which the `or` replaced with `{}`. Every such hop then carried a dict where
    the reader expected a list, and the whole chain view threw on the first one. Caught by driving
    the page in a real DOM; nothing about the shape of the code looked wrong.

    The caller says what empty means here, because only the caller knows the column's type.
    """
    if s in (None, ""):
        return empty
    try:
        v = json.loads(s)
    except (TypeError, ValueError):
        return empty
    return empty if v is None else v


def assemble(project, digests, schema, entries, findings, store, cfg,
             generated_at: str, version: str, monitoring: dict | None = None) -> dict:
    """One object holding every fact assay has about this project.

    *** THE MONITORING NUMBERS REACHED THE FORM AND NOT THE REPORT. ***
    "why doesnt the main report talk about like testing and monitoring stuff and just the form?"
    Because `assay volume` wrote a JSON that only `review --emit` read. The report is the artifact
    a person opens to find out what is known about the warehouse, and whether anything is watching
    it is exactly that -- so it takes the same file, by the same flag name, and renders it whole
    rather than as the two numbers the form needed.
    """
    by_uid = {e.uid: e for e in entries}
    # *** WHAT EACH FINDING WOULD DO ON A BUILD, NOT JUST THAT IT EXISTS. ***
    # `findings` says what is wrong; the policy says which of it stops CI. Deciding that by
    # reading the list is exactly the judgment a reader should not be making, so it travels with
    # the finding -- the same argument `violations()` makes to an agent.
    acted: dict = {}
    try:
        from . import judged as _judged
        kept, waived = _judged.apply_policy(findings, cfg, store, project)
        for f, act, why in kept:
            acted[f.id] = (act, why)
        # *** A WAIVED FINDING IS NOT AN UNCONFIGURED ONE, AND A BLANK CANNOT TELL THEM APART. ***
        # It never reaches the findings table on a normal run, so anything seeing it here is
        # reading the pre-policy list and needs to know which it is holding.
        for f, why in waived:
            acted[f.id] = ("waived", why)
    except Exception:                                            # noqa: BLE001
        acted = {}

    read_by: dict = {}
    for e in entries:
        for p in e.reads:
            read_by.setdefault(p, []).append(e.name)

    claims = _claims(store, entries)
    claim_by_subject: dict = {}
    for c in claims:
        claim_by_subject.setdefault(c["subject"], []).append(c["id"])

    from . import groups as groups_mod
    find_rows = _findings(findings, store, acted,
                          groups_mod.membership(groups_mod.build(project, findings)))
    # When each was first SEEN, and at which commit -- never "introduced", which is backtest's.
    from . import history as history_mod
    seen = history_mod.first_seen(store) if store is not None else {}
    for r in find_rows:
        if r["id"] in seen:
            t, sha = seen[r["id"]]
            c = history_mod.commit(store, sha) or {}
            r["first_seen"] = {"at": str(t)[:10], "commit": (sha or "")[:9],
                               "subject": c.get("subject", "")}
    find_by_subject: dict = {}
    for f in find_rows:
        find_by_subject.setdefault(f["subject"], []).append(f["id"])

    decisions = _decisions(store)
    dec_by_subject: dict = {}
    for i, d in enumerate(decisions):
        # A decision key is the model uid, or `<uid>::<family>::<id>` for the families that ask
        # per claim or per hop. Both belong to the model.
        dec_by_subject.setdefault(d["key"].split("::")[0], []).append(i)

    models = []
    for e in sorted(entries, key=lambda x: x.uid):
        models.append({
            "uid": e.uid, "name": e.name, "path": e.path, "layer": e.layer,
            # Whose model this is. An installed package's models are in the manifest and are not
            # yours; a filter on `unreadable` would hide exactly the signal you want.
            "package": getattr(project.models.get(e.uid), "package", "") or "",
            "yours": not getattr(project.models.get(e.uid),
                                 "is_installed_package", False),
            "materialized": e.materialized, "description": e.description or "",
            "unreadable": bool(e.unreadable),
            "grain": _fact(e.grain), "derived_grain": list(e.derived_grain or []),
            "columns": [{"name": c.name, "in_key": bool(c.in_key),
                         "provenance": _fact(c.provenance), "role": _fact(c.role),
                         "null_meaning": _fact(c.null_meaning)}
                        for c in sorted(e.columns, key=lambda c: c.name)],
            "reads": sorted(e.reads), "read_by": sorted(read_by.get(e.name, [])),
            "descendants": e.descendants, "marts": e.marts,
            "exposures": list(e.exposures or []),
            "filters_rows": bool(e.filters_rows), "aggregates": bool(e.aggregates),
            "union_parents": sorted(e.union_parents or []),
            "driving_parents": sorted(e.driving_parents or []),
            "unique_key_parents": sorted(e.unique_key_parents or []),
            "join_keys": {k: sorted(v) for k, v in sorted((e.join_keys or {}).items())},
            "join_kind": dict(sorted((e.join_kind or {}).items())),
            "pre_aggregated": {k: sorted(v) for k, v
                               in sorted((e.pre_aggregated_parents or {}).items())},
            "row_loss": {k: list(v) for k, v in sorted((e.row_loss or {}).items())},
            "parent_rows": dict(sorted((e.parent_rows or {}).items())),
            "doc_conflict": _fact(e.doc_conflict),
            "fanout_hops": [[c, round(float(p), 4)] for c, p in sorted(e.fanout_hops or [])],
            "claims": sorted(claim_by_subject.get(e.uid, [])),
            "findings": sorted(find_by_subject.get(e.uid, [])),
            "decisions": sorted(dec_by_subject.get(e.uid, [])),
        })

    return {
        "meta": {
            "project": project.project_name or "this project",
            "models": len(project.models), "sources": len(project.sources),
            "version": version,
            # *** NEVER A WALL CLOCK. *** A page that churns cannot be committed.
            "generated_at": generated_at,
            "coverage": project.coverage(),
            "yours": sum(1 for m in models if m["yours"]),
            "packaged": sum(1 for m in models if not m["yours"]),
            # *** AN EMPTY STORE AND A CLEAN WAREHOUSE RENDER THE SAME PAGE. ***
            # Every zero on this page -- ruled, spent, answered -- reads as "nothing is wrong"
            # when it can equally mean "nothing has run". The page says which.
            "new_store": (store.new_store_warning() if store is not None else
                          "no store was read, so every count that comes from one is absent "
                          "rather than zero."),
        },
        "models": models,
        "edges": _edges(store, by_uid),
        "claims": claims,
        "findings": find_rows,
        "decisions": decisions,
        "questions": _questions(),
        "adjudications": _adjudications(store),
        "config": _config(cfg),
        # *** CHECKS THAT FIRED AND audit.yml DOES NOT NAME. ***
        # "It reported nothing" and "it is not configured" read identically from the outside, and
        # this is the direction that grows by itself: every release adds checks.
        "unconfigured": [{"check": c, "shipped": a}
                         for c, a in cfg.unconfigured({f["check"] for f in find_rows})],
        # *** WHAT TO CONFIGURE, BESIDE WHAT WAS FOUND, IN THE SAME ARTIFACT. ***
        # A findings list and a config file in two different places is the gap `suggest` exists to
        # close, and putting the suggestions anywhere else would reopen it one layer out. They are
        # derived from the same store the rest of this object comes from, so they diff alongside
        # it: a suggestion that appears is a candidate that arrived, and one that disappears was
        # either configured or stopped being true, which is the "did accepting it move a number"
        # question the work order asks for.
        "suggestions": _suggestions(store, cfg, find_rows, project),
        "runs": _runs(store),
        # *** THE TWO THINGS THE RECORD SAID THAT NOTHING ELSE DID. ***
        # Everything else on the record duplicates a section the Overview now renders natively, so
        # keeping it as an iframe was the same numbers twice in one scroll. These two were the
        # reason it was still there, and they belong in the data rather than behind a frame.
        # *** WHAT IT COST, BESIDE WHAT IT BOUGHT. ***
        # The page held every answer and no dollar figure, so "is this worth running" had to be
        # asked at a terminal against a different command. It is one row per CALL out of
        # `model_calls` -- never a sum over the answer rows, which counts a batched call once per
        # answer and reads $4.25 for a store that spent $1.32.
        "cost": _cost(store),
        "effectiveness": _effectiveness(store),
        "moved": _moved(store, project),
        "unreadable": _unreadable(store, project),
        # *** ABSENT AND FINE ARE DIFFERENT, AND `{}` HAS TO MEAN THE FIRST ONE. ***
        # Empty here means nobody passed `--monitoring`, never that the monitoring is healthy.
        # The tab says which, the same way `new_store` does for the store.
        "monitoring": monitoring or {},
        # *** AREAS RATHER THAN FINDINGS. *** (25.23a) One filter written in several models, the
        # one that differs, one claim made about several models -- the same assembly `assay
        # clusters` prints, so the page and the command cannot describe them differently.
        "areas": _areas(project, digests, store),
    }


def _cost(store) -> dict:
    """The ledger, small enough to embed: totals plus the three breakdowns, no per-call rows."""
    if store is None:
        return {}
    try:
        from . import cost as cost_mod
        led = cost_mod.ledger(store)
    except Exception:                                            # noqa: BLE001
        # A store written before `model_calls` existed still renders a page. The tab says the
        # ledger is absent rather than showing a zero, which would read as "this was free".
        return {}
    keep = ("usd", "input_tokens", "calls", "output_tokens", "output_calls",
            "calls_without_usage", "id_source", "by_caller", "by_family", "by_day")
    out = {k: led[k] for k in keep}
    out["days"] = _spend_days(store, led)
    try:
        wh = cost_mod.warehouse_ledger(store)
        out["warehouse"] = {k: wh[k] for k in
                            ("calls", "bytes_estimated", "usd", "wall_ms", "failed",
                             "unestimated", "bytes_measured_calls", "rate_cards",
                             "by_caller", "by_kind", "by_day")}
    except Exception:                                            # noqa: BLE001
        out["warehouse"] = {}
    return out


def _spend_days(store, led: dict) -> list:
    """One row per day this project RAN, whether or not it spent anything.

    *** A DAY WITH RUNS AND NO CALLS WAS SIMPLY ABSENT, AND THAT READS AS BROKEN RECORDING. ***
    Reported from the field: seven runs on 2026-09-22, zero `model_calls` rows on 2026-09-22, and
    the Spend tab silently skipped the day. The truth was that every judged state was cached and
    nothing needed asking -- a good story the tab could not tell, because an omitted row and a
    zero row look the same once the day is gone.

    So the days come from `runs` UNION the days anything was spent, and a zero is rendered as a
    zero with the reason beside it.
    """
    if store is None:
        return []
    spent = {str(d): {"usd": usd, "calls": calls, "tokens": tok}
             for d, calls, tok, usd in (led.get("by_day") or [])}
    runs_by_day: dict = {}
    try:
        for day, n in store.con.execute(
                "select cast(started_at as date) as d, count(*) from runs "
                "where started_at is not null group by 1").fetchall():
            runs_by_day[str(day)] = int(n)
    except Exception:                                            # noqa: BLE001
        runs_by_day = {}
    wh_by_day: dict = {}
    try:
        from . import cost as cost_mod
        for day, calls, by, usd, ms, bad in (
                cost_mod.warehouse_ledger(store).get("by_day") or []):
            wh_by_day[str(day)] = {"calls": calls, "bytes": by, "usd": usd,
                                   "wall_ms": ms, "failed": bad}
    except Exception:                                            # noqa: BLE001
        wh_by_day = {}

    out = []
    for day in sorted(set(spent) | set(runs_by_day) | set(wh_by_day), reverse=True):
        s = spent.get(day) or {"usd": 0.0, "calls": 0, "tokens": 0}
        w = wh_by_day.get(day) or {}
        runs = runs_by_day.get(day, 0)
        # The reason a day is zero, said rather than left to be assumed. Only one of these is
        # "nothing happened"; the other two are the interesting ones.
        if s["calls"]:
            why = ""
        elif runs:
            why = ("ran, asked nothing: every judged state was already answered and served from "
                   "the cache")
        else:
            why = "no run recorded on this day"
        out.append({"day": day, "usd": s["usd"], "calls": s["calls"], "tokens": s["tokens"],
                    "runs": runs, "why": why,
                    "warehouse_calls": w.get("calls", 0), "warehouse_usd": w.get("usd", 0.0),
                    "warehouse_bytes": w.get("bytes", 0), "warehouse_ms": w.get("wall_ms", 0)})
    return out


def _latest_run(store, table: str) -> str | None:
    """The run to read `table` from, chosen by a TOTAL order.

    *** `order by count(*) desc limit 1` IS AN ARBITRARY PICK, AND IT CAUGHT ME. ***
    The first version of this took the fullest run, copying a shipped example that says a tie
    "would mean two runs found exactly the same thing". On the field store six runs hold exactly
    573 edge facts each, so the tie is the NORMAL case and duckdb returned a different winner
    between two invocations of the same command. Two runs of `assay page` against an unchanged
    store wrote different files, which is the one property the file is supposed to have.

    `arbitrary_pick` and `first_match_pick` are checks this tool runs against other people's SQL.
    This is the same defect, in the code that renders their results.

    So: most recent by the clock, and `run_id` breaks the tie, because a comparison that can tie
    is not an order. The store's `runs` table carries `started_at`; a run_id present in `table`
    but absent from `runs` still sorts, at the end, rather than vanishing.
    """
    if store is None:
        return None
    try:
        row = store.con.execute(
            f"select t.run_id from {table} t "
            "left join runs r on r.run_id = t.run_id "
            "group by t.run_id, r.started_at "
            "order by r.started_at desc nulls last, t.run_id desc limit 1").fetchone()
    except Exception:                                            # noqa: BLE001
        return None
    return row[0] if row else None


def _edges(store, by_uid) -> list:
    """Every hop, with what it carries and drops, and the verdict on whether it kept the grain.

    The columns a hop DROPS are the part no other surface shows, and they are the answer to "why
    does this mart not have that field". Stored per run; the latest run is the live one.
    """
    out = []
    if store is None:
        return out
    try:
        run = _latest_run(store, "edge_facts")
        rows = store.con.execute(
            "select parent, child, parent_name, child_name, available, carried, dropped, "
            "joined_on, dropped_cols from edge_facts where run_id = ? "
            "order by child_name, parent_name, parent", [run]).fetchall()
    except Exception:                                            # noqa: BLE001
        return out
    for p, c, pn, cn, avail, carried, dropped, joined, dcols in rows:
        e = by_uid.get(c)
        out.append({
            "parent": p, "child": c, "parent_name": pn, "child_name": cn,
            "available": avail, "carried": carried, "dropped": dropped,
            "joined_on": _json(joined, []),
            "dropped_cols": _json(dcols, []),
            "kind": (e.join_kind or {}).get(pn, "") if e else "",
            "driving": bool(e and pn in (e.driving_parents or set())),
            "union_arm": bool(e and pn in (e.union_parents or set())),
            "unique_key": bool(e and pn in (e.unique_key_parents or set())),
            "row_loss": list((e.row_loss or {}).get(pn, ())) if e else [],
        })
    return out


def _claims(store, entries) -> list:
    """Every sentence this project says about itself, and what the code said back."""
    if store is None:
        return []
    # *** THE KEY IS THE CONTEXT STRING, NOT THE CLAIM ID. ***
    # `claim_conflicts` carries `"<model>: <text[:120]>"`, which is what the writer put in the
    # decision's context. Matching on the claim id finds nothing and every claim would read as
    # supported -- absence reporting as a pass, in the one table built to show disagreement.
    conflict: dict = {}
    for e in entries:
        for ctx, p in (e.claim_conflicts or []):
            conflict[ctx] = round(float(p), 4)
    out = []
    for r in store.claims():
        out.append({
            "id": r["claim_id"], "subject": r["subject"], "subject_name": r["subject_name"],
            "text": r["text"], "kind": r["kind"] or "",
            "kind_conf": round(r["kind_conf"], 4) if r["kind_conf"] is not None else None,
            "source_kind": r["source_kind"] or "", "source_ref": r["source_ref"] or "",
            "citation": r["citation"] or "", "status": r["status"] or "active",
            # *** THE CONTEXT KEY IS THE CLAIM TEXT, NOT THE CLAIM ID. ***
            # `claim_conflicts` carries `"<model>: <text>"`, so matching on the id alone finds
            # nothing. Both are tried and the absence of a match means UNASKED, never SUPPORTED.
            "contradicted": conflict.get(f"{r['subject_name']}: {(r['text'] or '')[:120]}"),
        })
    return sorted(out, key=lambda c: (c["subject_name"], c["id"]))


def _findings(findings, store, acted: dict | None = None, member: dict | None = None) -> list:
    acted = acted or {}
    member = member or {}
    ruled = store.ruled_subjects() if store is not None else set()
    out = []
    for f in findings:
        fid = f.id
        out.append({
            "id": fid, "check": f.check, "subject": f.subject, "model": f.subject_name,
            "file": f.file, "summary": f.summary, "detail": f.detail,
            "base": f.base, "weight": round(f.weight, 3), "weight_parts": f.weight_parts(),
            "descendants": f.descendants, "marts": f.marts,
            "exposures": list(f.exposures or []),
            # One construct written in several models: shown on the finding, never ruled as one.
            **({"group": {k: v for k, v in member[fid].as_dict().items()
                          if k in ("size", "models", "macro_at")}} if fid in member else {}),
            "rests_on": f.rests_on or "",
            "evidence": f.evidence or {},
            # *** RULED ON THIS FINDING, OR ON ITS MODEL, AND THEY ARE NOT THE SAME CLAIM. ***
            "ruled_finding": f"{f.subject}::finding::{fid}" in ruled,
            "ruled_model": f.subject in ruled,
            # fail | queue | annotate, and WHY it is that: `audit.yml` or `default by severity`.
            "action": acted.get(fid, ("", ""))[0],
            "action_why": acted.get(fid, ("", ""))[1],
        })
    return sorted(out, key=lambda f: (-f["weight"], f["check"], f["model"], f["id"]))


def _decisions(store) -> list:
    """The live answer to every question asked about this project.

    One row per (subject, question): the latest. Every version is kept in the store because that
    is what makes `effectiveness` possible, and serving all of them at once is how `traversal`
    once reported twelve verdicts for four hops.
    """
    if store is None:
        return []
    try:
        rows = store.live_decisions(
            "1 = 1", [],
            columns="decision_key, question, answer, confidence, probabilities, context, "
                    "prompt_version, model_version, caller")
    except Exception:                                            # noqa: BLE001
        return []
    out = []
    for key, q, ans, conf, probs, ctx, pv, mv, caller in rows:
        p = _json(probs, {})
        out.append({
            "key": key, "question": q, "answer": ans,
            "confidence": round(float(conf), 4) if conf is not None else None,
            "runner_up": _runner_up(p, ans),
            "context": ctx or "", "prompt_version": pv or "", "model_version": mv or "",
            "caller": caller or "",
        })
    return sorted(out, key=lambda d: (d["key"], d["question"]))


def _adjudications(store) -> list:
    """Who ruled what, and whether they were a person. Only `human` counts anywhere."""
    if store is None:
        return []
    try:
        rows = store.con.execute(
            "select subject, question, family, answered, verdict, correction, note, "
            "decided_by, source, prompt_version from adjudications "
            "order by subject, question, prompt_version").fetchall()
    except Exception:                                            # noqa: BLE001
        return []
    cols = ("subject", "question", "family", "answered", "verdict", "correction", "note",
            "decided_by", "source", "prompt_version")
    return [dict(zip(cols, r, strict=True)) for r in rows]


def _questions() -> list:
    """Every question assay will ask, in full.

    *** THE TEXT IS THE THING BEING MEASURED, SO IT BELONGS NEXT TO THE MEASUREMENT. ***
    `effectiveness` reports agreement per prompt_version and the version alone tells a reader
    nothing about what changed. The bank is small, static per release, and putting it in the file
    makes the file a complete record of what was asked as well as what came back.
    """
    from .contracts import load_all_banks
    out = []
    for name, q in sorted(load_all_banks().items()):
        crit = q.get("criteria") or {}
        out.append({
            "name": name,
            "id_prefix": q.get("id_prefix") or "",
            "prompt_version": q.get("prompt_version") or "",
            "kind": q.get("kind") or "choice",
            "subject": q.get("subject") or "",
            "origin": q.get("origin") or "shipped",
            "instructions": q.get("instructions") or {},
            "options": sorted(crit) if isinstance(crit, dict) else [],
            "criteria": crit,
        })
    return out


def _config(cfg) -> dict:
    """What was actually resolved, which is not always what the file says."""
    out: dict[str, Any] = {}
    for k in ("provider", "model", "max_spend_usd", "row_loss_threshold",
              "min_adjudications", "min_agreement"):
        v = getattr(cfg, k, None)
        if v is not None:
            out[k] = v
    for k in ("questions", "waivers", "vocab", "practices", "explanations"):
        v = getattr(cfg, k, None)
        if v:
            try:
                out[k] = json.loads(json.dumps(v, default=str, sort_keys=True))
            except (TypeError, ValueError):
                out[k] = str(v)
    return out


def _suggestions(store, cfg, find_rows: list, project=None) -> list:
    """What this project should configure, derived from what was found. Never a meaning."""
    try:
        from . import suggest as _sug
        firing = {f["check"] for f in find_rows}
        live = _sug.live_pairs(find_rows)
        run_id = None
        if store is not None:
            run_id = store.latest_run(getattr(project, "project_name", None))
        return [s.as_dict() for s in _sug.build(store, cfg, firing, run_id, live, project)]
    except Exception:                                            # noqa: BLE001
        # A store too old to carry a signal still renders every other section. An empty list here
        # reads as "no candidates", which is why the page prints the rule that found nothing
        # rather than an empty panel.
        return []


def _effectiveness(store) -> list:
    """Per family, per version: how often people agreed, and what is still open.

    A verdict is about a VERSION of a question, so agreement is reported per version -- an answer
    about v1 says nothing about v4. Unclear is never in the denominator: disagreement means the
    criteria are wrong, unclear means the state does not carry what the question asks, and they
    are fixed by different edits.
    """
    if store is None:
        return []
    out = []
    for src in ("human", "label", "agent"):
        try:
            for r in store.effectiveness(src):
                out.append({**r, "source": src})
        except Exception:                                        # noqa: BLE001, S112
            # A source with no verdicts yields nothing; the other sources still report.
            continue
    return sorted(out, key=lambda r: (r["source"], str(r.get("family", "")),
                                      str(r.get("prompt_version", ""))))


def _moved(store, project) -> dict:
    """What appeared and what went away since the previous recorded run.

    One run recorded means nothing can have moved yet, which is different from nothing having
    moved -- so the caller gets an empty dict and says which.
    """
    if store is None:
        return {}
    try:
        run = store.latest_run(project.project_name)
        if not run:
            return {}
        prev = store.previous_run(project.project_name, run)
        if not prev:
            return {}
        d = store.diff(prev, run)
    except Exception:                                            # noqa: BLE001
        return {}
    return {"new": [list(x) for x in d.get("new", [])][:40],
            "gone": [list(x) for x in d.get("gone", [])][:40],
            "same": d.get("same", 0),
            "n_new": len(d.get("new", [])), "n_gone": len(d.get("gone", []))}


def _runs(store) -> list:
    if store is None:
        return []
    try:
        rows = store.con.execute(
            "select run_id, project, dbt_version, assay_version, models, sources, tests, edges, "
            "readable, unreadable from runs order by started_at").fetchall()
    except Exception:                                            # noqa: BLE001
        return []
    cols = ("run_id", "project", "dbt_version", "assay_version", "models", "sources", "tests",
            "edges", "readable", "unreadable")
    return [dict(zip(cols, r, strict=True)) for r in rows]


def _unreadable(store, project) -> list:
    """*** WHAT assay COULD NOT READ, WHICH IS NOT THE SAME AS WHAT IS FINE. ***

    A model absent from every table above because its SQL would not parse looks identical, from
    outside, to a model with nothing wrong with it. It is named here for that reason.
    """
    out = []
    for uid, m in sorted(project.models.items()):
        if not m.readable:
            out.append({"uid": uid, "name": m.name, "path": m.path,
                        "package": getattr(m, "package", "") or "",
                        "yours": not getattr(m, "is_installed_package", False),
                        "why": "no compiled SQL assay could reach"})
    if store is None:
        return out
    try:
        run = _latest_run(store, "unreadable")
        rows = store.con.execute(
            "select distinct subject_name, file, reason from unreadable where run_id = ? "
            "order by subject_name", [run]).fetchall()
    except Exception:                                            # noqa: BLE001
        return out
    seen = {r["name"] for r in out}
    for name, path, err in rows:
        if name not in seen:
            out.append({"uid": "", "name": name, "path": path or "", "why": err or "parse failed"})
    return out


# ------------------------------------------------------------------ the artifact that gets committed

# *** THE HTML IS NOT THE THING WORTH KEEPING. THE DATA IS. ***
# An 8 MB page diffs as one unreadable blob. The same content as JSON Lines is the same size --
# measured: 8.12 MB either way, where pretty-printing costs 2 MB more and turns the models into a
# 110,000-line file nobody reads -- and it diffs as ONE LINE PER ENTITY. A commit then reads as
# "these 3 models changed, these 12 findings appeared", which is what the accrual argument was
# always about. So the artifact is committed and the page is regenerated from it.
#
# The page still EMBEDS this rather than fetching it, because browsers block `fetch` on `file://`.
# Reading the artifact is a build step, not a runtime load.
_LINES = ("models", "edges", "claims", "findings", "decisions", "questions",
          "adjudications", "unreadable", "runs", "effectiveness", "suggestions")
# *** AND ITS EMPTY VALUE, BECAUSE A LIST DEFAULTING TO `{}` IS THE SAME BUG AS `[]` -> `{}`. ***
# Caught by the round-trip guard: an artifact with no `unconfigured.json` handed back a dict where
# a list belongs, and `.length` on a dict is `undefined` rather than an error -- so the page would
# have shown nothing and looked fine. Second time this class has appeared in this file.
# `areas` was missing, so `--from` rendered a page with no Areas tab and nothing said why: the
# round-trip guard below compared the artifact against a fixture that lacked it too.
_WHOLE = (("meta", dict), ("config", dict), ("unconfigured", list),
          ("moved", dict), ("cost", dict), ("monitoring", dict), ("areas", dict))


def write_data(data: dict, directory, record: str = "") -> list:
    """The data artifact: one `.jsonl` per table, one line per entity, sorted keys.

    Returns [(path, rows)]. Small objects stay whole and pretty-printed, because `meta` and
    `config` are read by a person and you want a field-level diff on them; the big tables are one
    line per row, because you want an entity-level one.
    """
    from pathlib import Path
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for name in _LINES:
        rows = data.get(name) or []
        p = d / f"{name}.jsonl"
        # A trailing newline on every line, including the last: a file that ends without one makes
        # the next append show as a modification of the final entity rather than as an addition.
        p.write_text("".join(
            json.dumps(r, sort_keys=True, separators=(",", ":"), default=str) + "\n"
            for r in rows))
        out.append((p, len(rows)))
    for name, empty in _WHOLE:
        p = d / f"{name}.json"
        p.write_text(json.dumps(data.get(name) if data.get(name) is not None else empty(),
                                sort_keys=True, indent=2, default=str) + "\n")
        out.append((p, 1))
    # *** THE RECORD LIVES IN THE ARTIFACT TOO, AND IT IS THE HALF A PERSON READS. ***
    # 15 KB, diffs line by line, and it is the one file here you would open directly. Without it
    # `--from` would render an Understood tab that is silently empty, which is the same shape as
    # every other absence-reads-as-a-result defect in this codebase.
    if record:
        p = d / "record.html"
        p.write_text(record)
        out.append((p, 1))
    return out


def read_data(directory) -> dict:
    """Load an artifact back. `assay page --from` renders without a warehouse present, which also
    means any past commit's artifact can be rendered as the page it was."""
    from pathlib import Path
    d = Path(directory)
    if not d.is_dir():
        raise FileNotFoundError(f"no data artifact at {d}")
    data: dict = {}
    for name in _LINES:
        p = d / f"{name}.jsonl"
        data[name] = [json.loads(line) for line in p.read_text().splitlines() if line.strip()] \
            if p.exists() else []
    for name, empty in _WHOLE:
        p = d / f"{name}.json"
        data[name] = json.loads(p.read_text()) if p.exists() else empty()
    rec = d / "record.html"
    data["record"] = rec.read_text() if rec.exists() else ""
    # *** AN ARTIFACT MISSING A TABLE IS NOT AN EMPTY WAREHOUSE. ***
    # A directory that is not one of ours, or one written by a version that knew fewer tables,
    # would otherwise render as a project where nothing has been asked -- the absence-reads-as-a-
    # pass defect, one layer out. The page needs `meta` to say anything at all, so that is the
    # one whose absence is fatal.
    if not data["meta"]:
        raise ValueError(
            f"{d} has no meta.json, so it is not an assay data artifact. Rendering it would show "
            f"an empty warehouse, which is not the same as a warehouse with nothing in it.")
    return data


def _areas(project, digests, store) -> dict:
    from . import clusters
    return clusters.report(project, digests, store)
