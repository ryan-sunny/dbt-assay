"""Which judged answers are about SQL that has since changed.

*** THE ONLY WAY TO FIND OUT USED TO BE TO PAY TO RE-ASK. ***
`live_decisions` counts an answer stale by `prompt_version`, which asks whether the QUESTION
changed. Nothing asked whether the CODE changed, so a model edited after being judged served its
old answer with no signal at all.

dbt already hashes every model's source file and records it in the manifest -- 358 of 358 on the
field warehouse -- and assay read none of them. `file_checksum` is that hash, copied onto the
decision at decide time. Comparing it to the manifest is a dict lookup: no call, no parse, no
warehouse connection.

*** IT IS NECESSARY AND NOT SUFFICIENT, AND SAYS SO. ***
A comment edit moves the checksum and changes no meaning. A change to a PARENT moves nothing and
can change everything. Both are stated rather than papered over.

*** STALE IS REPORTED, NEVER HIDDEN. ***
`live_decisions` already argues this and is right: hiding a dated answer leaves the caller with
nothing, which is strictly worse than serving it dated. This module returns counts and lists; it
removes nothing from anybody's read path.

*** AND THE THIRD ANSWER IS NOT "CURRENT". ***
A decision with no checksum -- written before this column existed, or filed under a key that names
no model (`pair::`, `bank`: 190 of 19,707 on the field store) -- is one assay CANNOT CHECK. It is
counted in its own column. An absent measurement never reads as a pass.
"""
from __future__ import annotations

from collections import defaultdict

MOVED, CURRENT, UNCHECKABLE = "moved", "current", "uncheckable"


def _latest(store) -> list:
    """The current answer to each question, one row per (decision_key, question).

    Same rule `live_decisions` settled on and for the same reason: the newest answer wins, with no
    family resolution anywhere near it.
    """
    return store.con.execute(
        """select decision_key, question, prompt_version, file_checksum, call_id, decided_at
           from (select *, row_number() over (partition by decision_key, question
                                              order by decided_at desc) as rn
                 from model_decisions) where rn = 1""").fetchall()


def survey(store, project) -> dict:
    """Every judged answer, split three ways, with reach and what re-asking would cost."""
    from .contracts import family_index, family_of
    try:
        index = family_index()
    except Exception:                                            # noqa: BLE001
        index = {}

    rows = _latest(store)
    by_state: dict = defaultdict(list)
    for key, question, version, stored, call_id, at in rows:
        uid = str(key).split("::")[0]
        model = project.models.get(uid)
        now = getattr(model, "checksum", "") if model else ""
        if not stored or not now:
            state = UNCHECKABLE
        elif stored != now:
            state = MOVED
        else:
            state = CURRENT
        by_state[state].append({
            "key": key, "question": question, "prompt_version": version, "uid": uid,
            "model": model.name if model else uid.split(".")[-1],
            "family": (family_of(question, index) or "(unclaimed)"),
            "call_id": call_id, "decided_at": at,
            "was": stored, "now": now,
        })

    moved = by_state[MOVED]
    reach = {}
    for d in moved:
        if d["uid"] not in reach:
            reach[d["uid"]] = (project.blast_radius(d["uid"]) if d["uid"] in project.models
                               else {"descendants": 0, "marts": 0})
        d.update(reach[d["uid"]])

    return {
        "judged": len(rows),
        "moved": moved,
        "current": len(by_state[CURRENT]),
        "uncheckable": by_state[UNCHECKABLE],
        "by_family": _count(moved, "family"),
        "by_model": _by_model(moved),
        "why_uncheckable": _why(by_state[UNCHECKABLE], project),
    }


def _count(rows: list, field: str) -> list:
    out: dict = defaultdict(int)
    for r in rows:
        out[r[field]] += 1
    return sorted(out.items(), key=lambda kv: (-kv[1], kv[0]))


def _by_model(rows: list) -> list:
    """Ordered by blast radius, because that is what decides where an afternoon goes.

    Ties break on the name, so two runs over one store print one order.
    """
    out: dict = {}
    for r in rows:
        e = out.setdefault(r["uid"], {"model": r["model"], "answers": 0,
                                      "descendants": r.get("descendants", 0),
                                      "marts": r.get("marts", 0), "families": set()})
        e["answers"] += 1
        e["families"].add(r["family"])
    for e in out.values():
        e["families"] = sorted(e["families"])
    return sorted(out.values(), key=lambda e: (-e["marts"], -e["descendants"], e["model"]))


def _why(rows: list, project) -> list:
    """Why assay cannot check these, named rather than lumped into one number."""
    out: dict = defaultdict(int)
    for r in rows:
        if r["uid"] not in project.models:
            out["the decision key does not name a model"] += 1
        elif not r["now"]:
            out["dbt recorded no checksum for that model"] += 1
        else:
            out["decided before assay recorded a checksum"] += 1
    return sorted(out.items(), key=lambda kv: (-kv[1], kv[0]))


def quote(store, moved: list) -> dict:
    """What re-asking the stale answers would cost, from what their own calls cost.

    *** A MEASUREMENT OF THOSE CALLS, NOT AN AVERAGE STANDING IN FOR THEM. ***
    Each stale answer came from a call whose token count is recorded, so the quote is the sum of
    those calls at their own stored rate rather than a mean applied to a count. Tokens are summed
    as integers per rate and multiplied once, so the figure does not move between runs.

    A stale answer whose call is unpriced -- the provider returned no usage -- is counted and left
    out of the total, the same way `assay cost` handles it.
    """
    ids = sorted({d["call_id"] for d in moved if d["call_id"]})
    if not ids:
        return {"usd": 0.0, "calls": 0, "input_tokens": 0, "unpriced": len(moved)}
    marks = ",".join("?" * len(ids))
    rows = store.con.execute(
        f"select input_tokens, usd_per_input_token from model_calls where call_id in ({marks})",
        ids).fetchall()
    per_rate: dict = defaultdict(int)
    unpriced = 0
    for tok, rate in rows:
        if tok is None:
            unpriced += 1
            continue
        per_rate[rate or 0.0] += int(tok)
    return {"usd": sum(tok * rate for rate, tok in sorted(per_rate.items())),
            "calls": len(rows), "input_tokens": sum(per_rate.values()),
            "unpriced": unpriced + len([d for d in moved if not d["call_id"]])}
