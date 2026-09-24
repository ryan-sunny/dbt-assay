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
    for r in rows:
        fids = (list(r.get("findings") or [])
                if r["verdict"] in ("disagree", "agree", "accept") else [])
        fams[_record_one_verdict(store, r["subject"], r["question"], r["verdict"],
                                 r["correction"], r["note"], who, findings=fids,
                                 until=r.get("until", ""))] += 1
        for _fid in fids:
            if r["verdict"] == "disagree":
                dismissed += 1
            elif r["verdict"] == "accept":
                accepted += 1
            else:
                agreed += 1
    return {"recorded": len(rows), "by": who, "as": "human",
            "findings_agreed": agreed, "findings_accepted": accepted,
            "findings_dismissed": dismissed,
            "findings_ruled": agreed + accepted + dismissed,
            "by_family": dict(sorted(fams.items())),
            "recorded_nothing": bad[:12], "recorded_nothing_total": len(bad)}
