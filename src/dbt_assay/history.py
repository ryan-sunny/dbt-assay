"""What the repository's own history says: when a finding first appeared, and what SQL a past
version of a model actually compiled to.

*** THE COMPILED SQL WAS WRITTEN ON EVERY BUILD AND THROWN AWAY ON THE NEXT. *** (25.24d)
`backtest` replays history by stripping Jinja, because historical compiled SQL "does not exist" --
and 26 of 85 replays on the field repo could not be read that way. But it does exist: dbt writes
`target/compiled/` on every build, and the manifest records a checksum per model. Kept in the store
keyed on that checksum, any later replay of a commit whose model file hashes to it reads the real
compiled body, exactly, with no warehouse and no compile.

*** THE CHECKSUM IS dbt's OWN, SO A GIT BLOB CAN BE LOOKED UP BY IT. ***
dbt hashes a model file as sha256 of its text with the surrounding whitespace stripped. Measured on
the field manifest: 328 of 328 models, and a `git show` blob of one of them, all match. So a blob
from any commit hashes to the key its compiled body was stored under -- no second copy of anything.

*** A FINDING'S AGE IS WHEN IT WAS FIRST SEEN, NOT WHEN IT WAS INTRODUCED. ***
Every full `check` run records the repository's HEAD. The first run holding a finding says when it
was first SEEN and at which commit; the commit that introduced it may be earlier, and that is what
`backtest` is for. The difference is stated wherever an age is printed.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


def checksum(text: str) -> str:
    """dbt's checksum of a model file: sha256 of its text, surrounding whitespace stripped."""
    return hashlib.sha256((text or "").strip().encode()).hexdigest()


def _git(repo, *args: str) -> str:
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                           check=False, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout if p.returncode == 0 else ""


def head(repo) -> str:
    """The commit the working tree is at, with `+dirty` when it has uncommitted changes. Empty
    outside a git repository, which is not an error -- plenty of projects are copied, not cloned."""
    sha = _git(repo, "rev-parse", "HEAD").strip()
    if not sha:
        return ""
    dirty = bool(_git(repo, "status", "--porcelain", "--", ".").strip())
    return sha + ("+dirty" if dirty else "")


def harvest(store, project) -> int:
    """Keep the compiled body of every model of this project under its dbt checksum. Returns how
    many versions were new. Only COMPILED bodies -- a Jinja strip is not what the warehouse ran."""
    rows = []
    for m in project.models.values():
        if m.is_installed_package or not m.checksum or not m.compiled:
            continue
        if m.compiled_from not in ("disk", "manifest"):
            continue
        rows.append((m.checksum, m.name, m.compiled))
    if not rows:
        return 0
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)                  # the clock `runs` is on, not the session's
    before = store.con.execute("select count(*) from compiled_sql").fetchone()[0]
    store.con.executemany(
        "insert or ignore into compiled_sql (checksum, model, sql, first_seen) "
        "values (?, ?, ?, ?)", [(*r, now) for r in rows])
    return store.con.execute("select count(*) from compiled_sql").fetchone()[0] - before


def remember(store, text: str, model: str, sql: str) -> None:
    """Keep a body `backtest --compile` paid for, under the checksum of the file it came from."""
    store.con.execute(
        "insert or ignore into compiled_sql (checksum, model, sql, first_seen) "
        "values (?, ?, ?, current_timestamp)", [checksum(text), model, sql])


def compiled_for(store, text: str) -> str | None:
    """The compiled body recorded for this exact model file, or None."""
    row = store.con.execute("select sql from compiled_sql where checksum = ?",
                            [checksum(text)]).fetchone()
    return row[0] if row else None


def sync_commits(store, repo, project, since: str | None = None, limit: int = 2000) -> int:
    """Record every commit touching this project: sha, date, message, files, models. Returns how
    many were new. The message is kept whole -- written with a model in the loop, it has become
    signal rather than "fix" and "yep", and replaying it one-shot threw it away every time."""
    args = ["log", "--name-only", f"-n{limit}", "--format=\x1e%H\x1f%cI\x1f%s\x1f%b\x1f"]
    if since:
        args.append(f"--since={since}")
    out = _git(repo, *args)
    if not out:
        return 0
    by_path = {m.path: m.name for m in project.models.values() if m.path}
    rows = []
    for chunk in out.split("\x1e"):
        if "\x1f" not in chunk:
            continue
        sha, when, subject, body, files = (chunk.split("\x1f") + [""] * 5)[:5]
        paths = [p.strip() for p in files.splitlines() if p.strip()]
        models = sorted({name for p in paths for path, name in by_path.items()
                         if p == path or p.endswith("/" + path)})
        rows.append((sha, when, subject.strip(), body.strip(), json.dumps(paths),
                     json.dumps(models)))
    before = store.con.execute("select count(*) from commits").fetchone()[0]
    store.con.executemany(
        "insert or ignore into commits (sha, committed_at, subject, body, files, models) "
        "values (?, cast(? as timestamptz), ?, ?, ?, ?)", rows)
    return store.con.execute("select count(*) from commits").fetchone()[0] - before


def first_seen(store) -> dict:
    """{finding_id: (first full run's time, its git sha)} over every full run in the store.

    *** A REWORDED FINDING KEEPS ITS DATE. *** (C1) A finding's id hashes its summary, so when
    0.51.3 stopped cutting summaries mid-word, 283 findings got new ids and `history` said they
    were first seen that day instead of four days earlier. A new id inherits the date of the id
    it replaced: the one with the same check, subject and evidence in the run before, which is
    gone in the run the new id appears in. One to one, so two findings never share a history.
    """
    from .store import finding_key
    try:
        rows = store.con.execute("""
            select f.finding_id, f.check_name, f.subject, f.evidence, r.run_id, r.started_at,
                   coalesce(r.git_sha, '') as sha
            from findings f join runs r on r.run_id = f.run_id
            where r.scope is null and f.finding_id is not null
            order by r.started_at, r.run_id""").fetchall()
    except Exception:                                            # noqa: BLE001
        return {}
    runs: list = []                    # [(run_id, t, sha, {fid: key})] in time order
    for fid, chk, subj, ev, run, t, sha in rows:
        if not runs or runs[-1][0] != run:
            runs.append((run, t, sha, {}))
        runs[-1][3][fid] = finding_key(chk, subj, ev)
    seen: dict = {}
    prev: dict = {}
    for _run, t, sha, ids in runs:
        gone = {}
        for fid, key in prev.items():
            if fid not in ids:
                gone.setdefault(key, []).append(fid)
        for fid, key in sorted(ids.items()):
            if fid in seen:
                continue
            olds = gone.get(key)
            seen[fid] = seen[olds.pop(0)] if olds else (t, sha)
        prev = ids
    return seen


def commit(store, sha: str) -> dict | None:
    sha = (sha or "").split("+")[0]
    if not sha:
        return None
    try:
        row = store.con.execute(
            "select sha, committed_at, subject from commits where sha = ?", [sha]).fetchone()
    except Exception:                                            # noqa: BLE001
        return None
    return {"sha": row[0], "at": str(row[1])[:10], "subject": row[2]} if row else None


def churn(store, top: int = 12) -> list[tuple[str, int]]:
    """Models by how many recorded commits touched them, most first."""
    from collections import Counter
    try:
        rows = store.con.execute("select models from commits").fetchall()
    except Exception:                                            # noqa: BLE001
        return []
    c: Counter = Counter()
    for (models,) in rows:
        c.update(json.loads(models or "[]"))
    return sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:top]


def repo_of(project) -> Path | None:
    root = getattr(project, "project_root", None)
    return Path(root) if root and Path(root).exists() else None
