"""A handback: what a person answered in the review form, and how it reaches the store.

*** ONE PATH FOR EVERY WAY A HANDBACK ARRIVES. ***
`assay review --load`, the MCP tool `load_handback` and `assay serve` all record a handback. Three
copies of the loop that files a verdict is how the family mapping drifted twice before, so they
all call `record` here, and they all find handbacks in the same `folder`.

*** A SERVER'S audit.yml COMES FROM GIT. *** (S3) So a handback loaded there records verdicts
only: its config section is refused and listed, to be made in the repository instead, and the
file on the box stays byte-identical.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

DOWNLOADS = Path.home() / "Downloads"


def folder(cfg=None, override: str | None = None) -> Path:
    """Where handbacks are looked for: the flag, then `review.handbacks` in audit.yml, then the
    browser's download folder -- which is where the form's download goes when it is opened from
    disk. (S4)"""
    if override:
        return Path(override).expanduser()
    configured = getattr(cfg, "handbacks", None) if cfg is not None else None
    if configured:
        return Path(configured).expanduser()
    return DOWNLOADS


def newest(where: Path | list | None = None) -> Path | None:
    """The newest `handback*.json` in the folder(s), or None."""
    dirs = where if isinstance(where, list) else [where or DOWNLOADS]
    found = []
    for d in dirs:
        d = Path(d).expanduser()
        if d.is_dir():
            found += [p for p in d.glob("handback*.json") if p.is_file()]
    return max(found, key=lambda p: (p.stat().st_mtime, p.name)) if found else None


def refused_config(payload) -> list[str]:
    """Every config edit a handback carries, as `dotted.path: value`, for a verdicts-only load to
    refuse by name. An edit refused silently would read as one that was made."""
    from . import reviewform
    rows, bad = reviewform.load_config(payload if isinstance(payload, dict) else {})
    out = []
    for ch in rows or []:
        path = ".".join(str(p) for p in getattr(ch, "path", []) or [])
        value = getattr(ch, "value", "")
        out.append(f"{path}: {json.dumps(value, default=str) if not isinstance(value, str) else value}")
    return out + [f"(could not be read) {b}" for b in bad or []]


def preview(payload) -> dict:
    """What loading this handback would do, without doing it."""
    from . import reviewform
    rows, bad = reviewform.load(payload if isinstance(payload, dict) else {})
    by_q = Counter(r["question"] for r in rows)
    verdicts = Counter(r["verdict"] for r in rows)
    return {"by": (payload.get("by") if isinstance(payload, dict) else "") or "unknown",
            "verdicts": len(rows), "by_verdict": dict(sorted(verdicts.items())),
            "by_question": dict(sorted(by_q.items())),
            "recorded_nothing": len(bad), "config_edits": refused_config(payload)}


def _count(verdict: str, fids, agreed: int, accepted: int, dismissed: int):
    n = len([f for f in fids or [] if f]) if verdict != "unclear" else 0
    if verdict == "disagree":
        return agreed, accepted, dismissed + n
    if verdict == "accept":
        return agreed, accepted + n, dismissed
    return agreed + n, accepted, dismissed


def record(store, payload, by: str = "") -> dict:
    """File every verdict the handback carries as a `human` verdict, and nothing it does not.

    Returns what was recorded, for the caller to print or return. The config section is never
    written from here: writing audit.yml is `assay review --load --apply`, which shows the diff.
    """
    from . import reviewform
    from .cli import _record_one_verdict
    rows, bad = reviewform.load(payload if isinstance(payload, dict) else {})
    who = by or (payload.get("by") if isinstance(payload, dict) else "") or "unknown"
    fams: Counter = Counter()
    agreed = accepted = dismissed = 0
    split: dict = {}
    for r in rows:
        if r.get("split"):
            split.setdefault((r["subject"], r["question"]), []).append(r)
            continue
        fids = (list(r.get("findings") or [])
                if r["verdict"] in ("disagree", "agree", "accept") else [])
        fams[_record_one_verdict(store, r["subject"], r["question"], r["verdict"],
                                 r["correction"], r["note"], who, findings=fids,
                                 until=r.get("until", ""))] += 1
        agreed, accepted, dismissed = _count(r["verdict"], fids, agreed, accepted, dismissed)
    # *** A CARD WHOSE FINDINGS WERE RULED APART HAS NO ONE ANSWER. *** Each finding is recorded
    # with its own verdict. The pair gets `unclear`, saying how it split: it is read (so the
    # card does not come back, and it counts toward the floor), and a check that was right on
    # some findings and wrong on others is not counted as agreeing or as disagreeing.
    for (subj, q), parts in sorted(split.items()):
        how = ", ".join(f"{len(p['findings'])} {p['verdict']}" for p in parts)
        for p in parts:
            _record_one_verdict(store, subj, q, p["verdict"], p["correction"], p["note"], who,
                                findings=p["findings"], until=p.get("until", ""), pair=False)
            agreed, accepted, dismissed = _count(p["verdict"], p["findings"],
                                                 agreed, accepted, dismissed)
        note = parts[0]["note"].strip()
        fams[_record_one_verdict(store, subj, q, "unclear", "",
                                 f"ruled finding by finding in the review form: {how}"
                                 + (f". {note}" if note else ""), who)] += 1
    # the decisions on the Fix cards: a person's approve / defer / reject, never an agent's
    from . import fixes as fixes_mod
    fixed, fbad = reviewform.load_fixes(payload if isinstance(payload, dict) else {})
    for fx in fixed:
        fixes_mod.record(store, fx["fix"], fx["status"], kind=fx["kind"], title=fx["title"],
                         note=fx["note"], by=who,
                         detail=json.dumps({"excluded": fx["exclude"]}) if fx.get("exclude")
                         else "")
    bad = bad + fbad
    return {"fixes_decided": {k: sum(1 for f in fixed if f["status"] == k)
                              for k in ("approved", "deferred", "rejected")},
            "recorded": len(rows) - sum(len(p) for p in split.values()) + len(split),
            "by": who, "as": "human", "split_cards": len(split),
            "findings_agreed": agreed, "findings_accepted": accepted,
            "findings_dismissed": dismissed,
            "findings_ruled": agreed + accepted + dismissed,
            "by_family": dict(sorted(fams.items())),
            "recorded_nothing": bad[:12], "recorded_nothing_total": len(bad)}
