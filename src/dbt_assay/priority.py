"""One order for everything a person might act on: what customers see, then what is wrong now,
then how far it reaches, then how sure assay is.

*** FOUR SORTS, EACH ON REACH ALONE. *** (sunny-data, assay-loops.md) `plan`, the review form, the
page and the queue each ranked by exposures and marts, and none of them used the strongest
evidence assay holds: a test failing today, a guarantee that broke, a premise many things rest on.
So the most important finding could sit on page nine of one surface and first on another.

The order, and why:
  1. customer-facing: it reaches an exposure the project marks `meta: {paid: true}` (or
     `customer_facing`), because that is where a wrong number costs money;
  2. happening now: a failing test, a lost guarantee, a key that stopped holding, a fixed finding
     that came back, a source behind its freshness, a monitor that stopped, a premise it was held
     back on that broke. Evidence of harm today outranks a defect that might bite;
  3. reach: exposures, marts downstream, how many other things rest on a premise about this model,
     descendants;
  4. how sure: the judged probability, then the finding's own weight.

`why` carries the reasons as short sentences, in order, for cards, alerts and the overview.
"""
from __future__ import annotations

from dataclasses import dataclass, field

HARM: dict[str, str] = {
    "test_is_failing": "a dbt test on it fails",
    "guarantee_lost": "a proven guarantee stopped holding",
    "guarantee_does_not_hold": "rows multiply where the shape says they cannot",
    "key_stopped_holding": "a key that held no longer does",
    "fixed_finding_returned": "a fixed problem came back",
    "source_freshness_stale": "the source is behind its own freshness rule",
    "source_freshness_not_run": "nothing is checking the declared freshness",
    "monitor_ran_then_stopped": "a monitor on it stopped running",
    "hop_drops_most_rows": "a step loses most of its rows",
}

TIERS = ("customer-facing", "happening now", "wide reach", "the rest")


@dataclass
class Context:
    """What the priority needs beyond the finding, computed once per surface."""
    customer: dict = field(default_factory=dict)     # subject uid -> [paid exposure titles]
    rests_on: dict = field(default_factory=dict)     # model uid -> uses of premises about it

    @classmethod
    def of(cls, project, led=None) -> Context:
        ctx = cls()
        paid = [e for e in (getattr(project, "exposures", None) or {}).values()
                if getattr(e, "customer_facing", False)]
        if paid:
            for uid in list(getattr(project, "models", {})) + list(getattr(project, "sources",
                                                                            {})):
                hit = [e.title for e in project.exposures_of(uid) if e.customer_facing]
                if hit:
                    ctx.customer[uid] = hit
        if led is None:
            from . import ledger
            led = ledger.last()
        if led is not None:
            n: dict = {}
            for u in led.uses:
                p = led.premises.get(u.premise_id)
                if p is not None:
                    n[p.relation] = n.get(p.relation, 0) + 1
            ctx.rests_on = n
        return ctx


def _get(f, k, default=None):
    if isinstance(f, dict):
        return f.get(k, default)
    return getattr(f, k, default)


def _sure(ev: dict) -> float:
    for k in ("probability", "confidence"):
        v = (ev or {}).get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return 0.0


def of(f, ctx: Context | None = None) -> dict:
    """{tier, key, why} for a finding (object or dict). `key` sorts ascending, most urgent first."""
    ctx = ctx or Context()
    check = str(_get(f, "check", "") or "")
    subject = str(_get(f, "subject", "") or "")
    ev = _get(f, "evidence", None) or {}
    exposures = list(_get(f, "exposures", None) or [])
    marts = int(_get(f, "marts", 0) or 0)
    desc = int(_get(f, "descendants", 0) or 0)
    base = int(_get(f, "base", 2) or 2)
    why: list[str] = []
    paid = ctx.customer.get(subject) or []
    if paid:
        why.append(f"reaches {paid[0]}" + (f" and {len(paid) - 1} more customer-facing"
                                           if len(paid) > 1 else ""))
    harm = HARM.get(check)
    if not harm and isinstance(ev.get("why_it_is_back"), dict):
        harm = "a premise it was held back on broke"
    if harm:
        why.append(harm)
    rests = ctx.rests_on.get(subject, 0)
    if exposures and not paid:
        why.append(f"reaches {exposures[0]}" + (f" and {len(exposures) - 1} more"
                                                if len(exposures) > 1 else ""))
    if marts:
        why.append(f"{marts} mart{'s' if marts != 1 else ''} downstream")
    if rests >= 5:
        why.append(f"{rests} things rest on what is true of it")
    sure = _sure(ev)
    if sure >= 0.8:
        why.append(f"judged at {sure:.2f}")
    tier = 0 if paid else 1 if harm else 2 if (exposures or marts or rests >= 5) else 3
    key = (tier, 0 if paid else 1, 0 if harm else 1, -len(exposures), -marts, -rests, -desc,
           -sure, -base, str(_get(f, "id", "") or _get(f, "finding", "") or subject))
    return {"tier": TIERS[tier], "key": key, "why": why}


def sort(items: list, ctx: Context | None = None) -> list:
    """Most urgent first, by the one key. Stable across runs: ties break on the id."""
    ctx = ctx or Context()
    return sorted(items, key=lambda f: of(f, ctx)["key"])


def of_many(items: list, ctx: Context | None = None) -> dict:
    """The priority of a group (a card, a fix): its most urgent member's, plus how many."""
    ctx = ctx or Context()
    got = [of(f, ctx) for f in items]
    if not got:
        return {"tier": TIERS[3], "key": (3,), "why": []}
    best = min(got, key=lambda p: p["key"])
    return {**best, "key": (best["key"][0], -len(items), *best["key"][1:])}


# *** 2,202 NOTES ARE NOT 2,202 PROBLEMS. *** (Ryan: "telling me my 200 model warehouse is a sack
# of shit is insane") What a surface leads with is what the policy queues for a person, split into
# what is broken now and what is worth a look, the paid report first. The notes (annotate) are
# counted once, on one line, and stay reachable. No verdict on the warehouse as a whole.
BROKEN_NOW = frozenset(HARM) | {"values_lost_at_hop", "join_fans_out"}


def triage(findings, queued: set, ctx: Context | None = None) -> dict:
    """{queued, broken, broken_paid, look, look_paid, notes} over the open findings (objects or
    dicts); `queued` is the ids the policy queues."""
    ctx = ctx or Context()
    out = {"queued": 0, "broken": 0, "broken_paid": 0, "look": 0, "look_paid": 0, "notes": 0}
    for f in findings:
        fid = _get(f, "id", "")
        if fid not in queued:
            out["notes"] += 1
            continue
        out["queued"] += 1
        tier = _get(f, "tier", None) if isinstance(f, dict) else None
        paid = (tier or of(f, ctx)["tier"]) == "customer-facing"
        k = "broken" if str(_get(f, "check", "")) in BROKEN_NOW else "look"
        out[k] += 1
        out[k + "_paid"] += paid
    return out
