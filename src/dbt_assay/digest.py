"""What changed since the last full run that somebody should hear about today, and nothing else.

*** THE MORNING EMAIL HAD NOTHING FROM assay IN IT. *** (sunny-data, assay-loops.md gap 3) Every
number lived on a page somebody had to open. A digest is the opposite of a page: only what moved,
only what matters, and an empty object on a day when nothing did, so the email says nothing rather
than "0 new, 0 resolved, 1,852 open".

Read from the store alone (the two latest full `check` runs, the premise table, the cost ledger),
so it costs nothing and runs in a second after the daily run.
"""
from __future__ import annotations

import json

# the checks whose appearance is news, and how the email says it
NEWS = {
    "guarantee_lost": "guarantee lost",
    "guarantee_does_not_hold": "rows multiply",
    "test_is_failing": "test failing",
    "key_stopped_holding": "key stopped holding",
    "fixed_finding_returned": "a fixed problem came back",
    "monitor_ran_then_stopped": "monitor stopped",
    "source_freshness_stale": "source behind its freshness",
    "source_freshness_not_run": "freshness not being checked",
}


def _runs(store, project: str | None) -> list:
    q = "select run_id, started_at from runs where scope is null"
    args: list = []
    if project:
        q += " and project = ?"
        args.append(project)
    return store.con.execute(q + " order by started_at desc, run_id desc limit 2",
                             args).fetchall()


def _rows(store, run_id: str) -> dict:
    out = {}
    for fid, check, subj, name, summary, ev, exp in store.con.execute(
            """select finding_id, check_name, subject, subject_name, summary, evidence,
                      coalesce(exposures, '[]')
               from findings where run_id = ?""", [run_id]).fetchall():
        try:
            exposures = json.loads(exp) if isinstance(exp, str) else list(exp or [])
        except ValueError:
            exposures = []
        out[fid or f"{check}|{subj}|{summary}"] = {
            "check": check, "subject": subj, "model": name, "summary": summary,
            "exposures": exposures}
    return out


def build(store, project: str | None = None, spend_over_usd: float = 1.0) -> dict:
    """{} when nothing worth telling happened, else the events, most urgent first."""
    runs = _runs(store, project)
    if len(runs) < 2:
        return {}
    (cur, cur_at), (prev, prev_at) = runs[0], runs[1]
    now, before = _rows(store, cur), _rows(store, prev)
    # an id that moved because a summary was reworded is not news (live.new_findings)
    from types import SimpleNamespace

    from .live import new_findings

    def objs(d):
        return [SimpleNamespace(id=k, **v) for k, v in d.items()]

    def rows(d):
        return [(k, v["check"], v["subject"]) for k, v in d.items()]
    new = [vars(o) for o in new_findings(objs(now), rows(before))]
    resolved = [vars(o) for o in new_findings(objs(before), rows(now))]
    events: list[dict] = []
    for f in new:
        what = NEWS.get(f["check"])
        if what is None:
            continue
        events.append({"kind": f["check"], "what": what, "model": f["model"],
                       "summary": f["summary"][:200], "reaches": f["exposures"][:3]})
    # premises that broke at this run
    try:
        from . import ledger
        for name, cols, prop, was, is_, _ev, _rel in ledger.changes(store, cur):
            if is_ == "broken":
                p = ledger.Premise("", name, tuple(json.loads(cols or "[]")), prop)
                events.append({"kind": "premise_broke", "what": "premise broke",
                               "model": name, "summary": f"{p.statement()} (was {was})",
                               "reaches": []})
    except Exception:                                            # noqa: BLE001, S110
        pass
    # customer-facing first, then the order NEWS lists them in
    order = list(NEWS) + ["premise_broke"]
    events.sort(key=lambda e: (not e["reaches"], order.index(e["kind"]), e["model"]))
    out: dict = {}
    if events:
        out["events"] = events
    if new or resolved:
        out["findings"] = {"new": len(new), "resolved": len(resolved), "open": len(now)}
    try:
        # priced as the cost ledger prices it: input tokens at the rate recorded on each call
        got = store.con.execute(
            "select coalesce(sum(input_tokens * coalesce(usd_per_input_token, 0)), 0), count(*) "
            "from model_calls where called_at > ?", [prev_at]).fetchone()
        usd = float(got[0] or 0)
        if usd > spend_over_usd:
            out["spend"] = {"usd": round(usd, 4), "calls": int(got[1] or 0),
                            "over": spend_over_usd}
    except Exception:                                            # noqa: BLE001, S110
        pass
    if not out:
        return {}
    # "0 new, 3 resolved" alone is worth a line only when something resolved or appeared
    out["run"] = {"id": cur, "at": str(cur_at)[:16], "previous": prev,
                  "previous_at": str(prev_at)[:16]}
    return out


def lines(d: dict) -> list[str]:
    """The same, as sentences for a terminal or an email body."""
    if not d:
        return []
    out = []
    f = d.get("findings")
    if f:
        out.append(f"{f['new']} new, {f['resolved']} resolved, {f['open']} open "
                   f"(since {d['run']['previous_at']})")
    for e in d.get("events", [])[:20]:
        reach = f" (reaches {', '.join(e['reaches'])})" if e["reaches"] else ""
        out.append(f"{e['what']}: {e['model']}{reach}: {e['summary']}")
    if d.get("spend"):
        out.append(f"spent ${d['spend']['usd']:.2f} over {d['spend']['calls']} judged call(s) "
                   f"since the last run")
    return out


def trend(store, project: str | None = None, n: int = 60) -> list[dict]:
    """Per full run, oldest first: open findings, how many are evidence of harm, and premises
    broken. The Overview draws it; a fix batch shows as the drop after it."""
    q = "select run_id, started_at from runs where scope is null"
    args: list = []
    if project:
        q += " and project = ?"
        args.append(project)
    runs = store.con.execute(q + " order by started_at desc, run_id desc limit ?",
                             [*args, n]).fetchall()[::-1]
    if not runs:
        return []
    ids = [r[0] for r in runs]
    ph = ",".join("?" * len(ids))
    counts = {rid: (n_, h) for rid, n_, h in store.con.execute(
        f"""select run_id, count(*),
                   count(*) filter (where check_name in ({','.join('?' * len(NEWS))}))
            from findings where run_id in ({ph}) group by run_id""",
        [*NEWS, *ids]).fetchall()}
    broken: dict = {}
    try:
        broken = dict(store.con.execute(
            f"select run_id, count(*) from premises where run_id in ({ph}) and status = 'broken' "
            f"group by run_id", ids).fetchall())
    except Exception:                                            # noqa: BLE001, S110
        pass
    return [{"run": rid, "at": str(at)[:16], "open": counts.get(rid, (0, 0))[0],
             "harm": counts.get(rid, (0, 0))[1], "premises_broken": broken.get(rid, 0)}
            for rid, at in runs]
