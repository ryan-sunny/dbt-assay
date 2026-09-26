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


# what the form downloads and what a save keeps: `decisions-*.json`; `handback*.json` is the name
# the form used before 0.54.1, still found so a file already in Downloads loads
PATTERNS = ("decisions-*.json", "handback*.json")


def _found(d: Path) -> list[Path]:
    return [p for pat in PATTERNS for p in d.glob(pat) if p.is_file()]


def newest(where: Path | list | None = None) -> Path | None:
    """The newest decisions file in the folder(s), or None."""
    dirs = where if isinstance(where, list) else [where or DOWNLOADS]
    found = []
    for d in dirs:
        d = Path(d).expanduser()
        if d.is_dir():
            found += _found(d)
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

    Returns what was recorded, for the caller to print or return, and under `undo` what each
    write replaced, which `save` keeps in the decisions file and `withdraw` puts back. The config
    section is never written from here: writing audit.yml is `assay review --load --apply`, which
    shows the diff.
    """
    store.journal = []
    try:
        got = _record(store, payload, by)
        got["undo"] = store.journal
        return got
    finally:
        store.journal = None


def _record(store, payload, by: str) -> dict:
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


# ---------------------------------------------------------------- the decisions file
# *** WHO DECIDED WHAT, IN GIT, BESIDE THE CHANGE IT CAUSED. *** (Ryan: "being able to track the
# changes made and by who is important ... agent should be able to use those too so unapplying or
# rolling back is easy") A save is recorded in the store AND kept as one file: the handback as the
# form wrote it, plus what was recorded and what each write replaced. The agent that applies the
# approved fixes commits the file with them, so `git log` says who decided, and `withdraw` reads
# the same file to take the save back.

COMMIT_DIR = "assay_decisions"


def decisions_name(folder: Path, by: str, when=None) -> str:
    """`decisions-2026-09-26-1432-ryan.json`, in UTC, never one that already exists."""
    import re
    from datetime import datetime, timezone
    when = when or datetime.now(timezone.utc)
    who = re.sub(r"[^\w-]+", "-", str(by or "anonymous"))[:32].strip("-") or "anonymous"
    base = f"decisions-{when:%Y-%m-%d-%H%M}-{who}"
    name, n = base + ".json", 1
    while (Path(folder) / name).exists():
        n += 1
        name = f"{base}-{n}.json"
    return name


def is_saved(payload) -> bool:
    """A decisions file already recorded somewhere carries `undo`."""
    return isinstance(payload, dict) and isinstance(payload.get("undo"), list)


def write_decisions(path: Path, payload: dict, got: dict) -> Path:
    """The handback, plus what was recorded and what it replaced. Written whole, then moved into
    place, so a reader never sees half a file."""
    from datetime import datetime, timezone

    from . import __version__
    doc = {k: v for k, v in payload.items() if k not in ("recorded", "undo", "saved_at")}
    doc["saved_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc["assay"] = __version__
    doc["recorded"] = {k: v for k, v in got.items() if k != "undo"}
    doc["undo"] = got.get("undo") or []
    path = Path(path)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, default=str))
    tmp.replace(path)
    return path


def save(store, folder: Path, payload: dict, by: str = "") -> tuple[Path, dict]:
    """Record a handback and keep it as a decisions file in `folder`."""
    got = record(store, payload, by)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    name = decisions_name(folder, got["by"])
    return write_decisions(folder / name, payload, got), got


def listing(folder: Path) -> list[dict]:
    """Every decisions file in the folder, newest first: who, when, and what it holds."""
    folder = Path(folder)
    out = []
    for p in sorted(_found(folder) if folder.is_dir() else [], key=lambda p: p.name,
                    reverse=True):
        try:
            doc = json.loads(p.read_text())
        except (OSError, ValueError) as e:
            out.append({"name": p.name, "error": f"cannot be read: {e}"})
            continue
        pv = preview(doc)
        fx = fixes_in(doc)
        out.append({"name": p.name, "by": pv["by"], "saved_at": doc.get("saved_at", ""),
                    "recorded": is_saved(doc), "verdicts": pv["verdicts"],
                    "fixes_approved": sum(1 for f in fx if f["status"] == "approved"),
                    "config_edits": len(pv["config_edits"]),
                    "withdrawn": bool(doc.get("withdrawn"))})
    return out


def fixes_in(doc) -> list[dict]:
    from . import reviewform
    fixed, _bad = reviewform.load_fixes(doc if isinstance(doc, dict) else {})
    return fixed


def config_diff(doc, config_path) -> dict:
    """The file's config edits as a diff against `audit.yml` in `config_path`: what the agent
    applies in its checkout and commits. Nothing is written."""
    from . import configpatch, reviewform
    changes, bad = reviewform.load_config(doc if isinstance(doc, dict) else {})
    out = {"edits": refused_config(doc), "diff": "", "not_placed": [], "unreadable": bad}
    if not changes:
        return out
    target = Path(config_path or ".") / "audit.yml"
    if not target.exists():
        out["not_placed"] = [f"{c.dotted}: there is no {target}" for c in changes]
        return out
    before = target.read_text()
    res = configpatch.apply(before, changes)
    out["diff"] = "\n".join(configpatch.diff(before, res.text)) + "\n" if res.applied else ""
    out["not_placed"] = [f"{ch.dotted}: {why}. Paste it yourself: {ch.value}"
                         for ch, why in res.refused]
    return out


def describe(path: Path, config_path=".") -> dict:
    """One decisions file, for the agent that turns it into a branch: the fixes approved (apply
    each with `apply_plan_item`, leaving out what the person excluded), the audit.yml diff, and
    where to commit the file itself."""
    path = Path(path)
    doc = json.loads(path.read_text())
    pv = preview(doc)
    fx = fixes_in(doc)
    return {"name": path.name, "by": pv["by"], "saved_at": doc.get("saved_at", ""),
            "recorded": is_saved(doc), "withdrawn": doc.get("withdrawn") or None,
            "verdicts": {k: pv[k] for k in ("verdicts", "by_verdict", "by_question")},
            "fixes": fx, "approved": [f["fix"] for f in fx if f["status"] == "approved"],
            "config": config_diff(doc, config_path),
            "commit_as": f"{COMMIT_DIR}/{path.name}",
            "steps": [
                ("in a branch: apply_plan_item(<id>) for each id in `approved`, writing the "
                 "files it returns; a fix's `excluded` items are the ones the person left out"),
                ("apply `config.diff` to audit.yml (git apply); anything in "
                 "`config.not_placed` is edited by hand"),
                (f"copy this file to {COMMIT_DIR}/{path.name} and commit it with the fixes and "
                 "audit.yml, one PR"),
                "dbt parse, then verify_plan_item(<id>) for each fix"]}


def withdraw(store, doc: dict, by: str = "", name: str = "") -> dict:
    """Take a save back: every verdict and fix decision it recorded returns to what it replaced.

    *** ONLY WHAT IS STILL THIS SAVE'S. *** A verdict somebody gave again since belongs to that
    later decision, and putting the old one back would undo it without anyone seeing. So each row
    is restored only while it is still the one this save wrote, and the rest are listed.
    """
    from . import fixes as fixes_mod
    if not is_saved(doc):
        return {"error": "this file carries no `undo`: it was never recorded through a save, so "
                         "there is nothing to put back. Record the opposite verdicts instead."}
    who = by or "unknown"
    label = name or "a decisions file"
    restored, skipped = 0, []
    for u in reversed(doc["undo"]):
        after = u.get("after") or {}
        if u.get("table") == "adjudications":
            key = [u["subject"], u["question"], u.get("prompt_version", "")]
            now = store.con.execute(
                "select verdict, decided_by, decided_at from adjudications where subject = ? "
                "and question = ? and prompt_version = ?", key).fetchone()
            if now is None or (now[0], now[1], str(now[2])) != (
                    after.get("verdict"), after.get("decided_by"), after.get("decided_at")):
                skipped.append(f"{u['subject']} {u['question']}: ruled again since")
                continue
            b = u.get("before")
            if b and [b.get("subject"), b.get("question"), b.get("prompt_version") or ""] != key:
                skipped.append(f"{u['subject']} {u['question']}: the row to put back is not "
                               f"this one's")
                continue
            store.con.execute("delete from adjudications where subject = ? and question = ? "
                              "and prompt_version = ?", key)
            if b:
                cols = list(b)
                store.con.execute(
                    f"insert into adjudications ({', '.join(cols)}) "
                    f"values ({', '.join('?' for _ in cols)})", [b[c] for c in cols])
            restored += 1
        elif u.get("table") == "fix_decisions":
            now = store.con.execute(
                "select status, decided_by, decided_at from fix_decisions where fix_id = ? "
                "order by decided_at desc limit 1", [u["fix_id"]]).fetchone()
            if now is None or (now[0], now[1], str(now[2])) != (
                    after.get("status"), after.get("by"), after.get("at")):
                skipped.append(f"fix {u['fix_id']}: decided again since")
                continue
            b = u.get("before") or {}
            excl = b.get("excluded") or []
            fixes_mod.record(store, u["fix_id"], b.get("status") or "proposed",
                             kind=u.get("kind", ""), key=u.get("key", ""),
                             title=u.get("title", ""), by=who,
                             note=f"withdrawn: {label}" + (f"; {b['note']}" if b.get("note")
                                                            else ""),
                             detail=json.dumps({"excluded": excl}) if excl else "")
            restored += 1
    return {"restored": restored, "skipped": skipped, "by": who}


def mark_withdrawn(path: Path, out: dict) -> None:
    """The file stays, saying when it was taken back and by whom: the record keeps both."""
    from datetime import datetime, timezone
    path = Path(path)
    doc = json.loads(path.read_text())
    doc["withdrawn"] = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "by": out.get("by", ""), "restored": out.get("restored", 0),
                        "skipped": out.get("skipped") or []}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, default=str))
    tmp.replace(path)
