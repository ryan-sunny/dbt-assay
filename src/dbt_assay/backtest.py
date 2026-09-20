"""Replay a repository's own history and measure whether the checks catch what it already fixed.

*** THE LABELLED SET NOBODY WROTE ON PURPOSE. ***
A commit that removed a defect is that defect and its repair, sitting in the history. Replaying the
pair asks the only question that matters about a check: did it fire before and go quiet after? That
is a regression suite, a way for a stranger to verify the tool on their OWN repo before adopting
anything, and a source of candidate questions, all from the same machinery.

*** THE COMMIT MESSAGE DOES NOT DECIDE WHAT IS A FIX, BECAUSE IT CANNOT. ***
A first version only replayed commits whose subject matched fix/bug/wrong/broken. Measured against
a real repo, THREE OF THE FOUR commits that removed a known degree-ranking defect did not match --
their subjects read "Two more models ranked spatial candidates in degrees, found by asking all 192"
and "The two pieces a reach screen was missing". Gating on message wording means only catching
teams who write `fix:` prefixes. So every commit touching model SQL is replayed and the TRANSITION
is the signal; the wording is kept as a label, never as a filter.

*** IT NEVER TOUCHES THE WORKING TREE. ***
`git show <sha>:<path>` reads a blob straight out of the object store. No checkout, no stash, no
detached HEAD, nothing that could collide with another session working in the same clone.

*** A LIGHT JINJA STRIP, NOT A COMPILE, AND IT SAYS SO. ***
Historical compiled SQL does not exist: `target/` is gitignored, and running `dbt compile` at every
commit needs that commit's packages, profiles and warehouse. So `ref()` and `source()` become plain
relation names and control blocks are removed. A model whose SQL is genuinely macro-GENERATED will
not survive that, and is reported as skipped rather than quietly counted as clean -- a file assay
could not read must never contribute to a passing result.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field

from .checks.structural import (
    bbox_used_as_distance,
    duckdb_tilde_is_full_match,
    ranks_by_degrees,
)
from .parse import digest

# Only the checks that need nothing but the SQL. Anything requiring the manifest (test definitions,
# declared keys, the DAG) cannot be reconstructed from a blob and is out of scope here.
SQL_ONLY_CHECKS = (ranks_by_degrees, bbox_used_as_distance, duckdb_tilde_is_full_match)

FIXY = re.compile(
    r"\b(fix(e[sd])?|bug|wrong|incorrect|corrects?|broken|repair|regression|revert)\b", re.IGNORECASE)

_REF = re.compile(r"\{\{\s*ref\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")
_SOURCE = re.compile(r"\{\{\s*source\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")
_BLOCK = re.compile(r"\{%-?.*?-?%\}", re.DOTALL)
_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)
_CONFIG = re.compile(r"\{\{\s*config\(.*?\)\s*\}\}", re.DOTALL)
# A Jinja expression ALONE ON ITS LINE is a statement, not a value. Substituting a
# literal for it leaves a bare `1` in front of the query, which parses as a complete
# expression and then collides with the `with` that follows -- 46 of 75 replays died
# on exactly that.
_STANDALONE = re.compile(r"^[ \t]*\{\{.*?\}\}[ \t]*$", re.MULTILINE | re.DOTALL)
_EXPR = re.compile(r"\{\{.*?\}\}", re.DOTALL)


def dejinja(sql: str) -> str:
    sql = _COMMENT.sub("", sql)
    sql = _REF.sub(lambda m: m.group(1), sql)
    sql = _SOURCE.sub(lambda m: f"{m.group(1)}.{m.group(2)}", sql)
    sql = _CONFIG.sub("", sql)
    sql = _BLOCK.sub("", sql)
    sql = _STANDALONE.sub("", sql)
    # What is left is inline: a macro call or a var sitting inside an expression, where a literal
    # keeps the statement parseable.
    sql = _EXPR.sub("1", sql)
    return sql


def _git(repo: str, *args: str, ok_fail: bool = False) -> str:
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=False)
    if p.returncode and not ok_fail:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()[:200]}")
    return p.stdout


@dataclass
class Replay:
    sha: str
    subject: str
    model: str
    path: str
    before: set = field(default_factory=set)   # checks firing at the PARENT commit
    after: set = field(default_factory=set)    # checks firing at this commit
    skipped: str = ""
    message_says_fix: bool = False             # a LABEL for context, never a filter

    @property
    def verdict(self) -> str:
        # *** TWO VERY DIFFERENT THINGS WERE SHARING ONE BUCKET. ***
        # A commit that ADDS a model has no parent blob to compare against, which is not a
        # limitation of anything. A blob assay could not parse IS one. Reporting 403 "skipped"
        # when 319 of them were new files made the tool look far blinder than it is.
        if self.skipped:
            return "no_pair" if "added or removed" in self.skipped else "unparseable"
        gone = self.before - self.after
        added = self.after - self.before
        if gone:
            return "caught"          # fired before the fix, quiet after: the check works
        if added:
            return "introduced"      # quiet before, fires after: the "fix" added something
        if self.before:
            return "still_firing"    # fires at both: the fix did not address what assay sees
        return "silent"              # assay saw nothing either side

    @property
    def checks_that_caught(self) -> list:
        return sorted(self.before - self.after)


def commits(repo: str, limit: int = 60, since: str | None = None,
            fix_like_only: bool = False, paths: str = "*.sql") -> list[tuple[str, str]]:
    """Every commit touching model SQL, newest first. The message is a label, not a filter."""
    args = ["log", "--format=%H%x1f%s", f"-n{max(limit * 4, 200)}"]
    if since:
        args.append(f"--since={since}")
    args += ["--", paths]
    out = []
    for line in _git(repo, *args).splitlines():
        if "\x1f" not in line:
            continue
        sha, subject = line.split("\x1f", 1)
        if fix_like_only and not FIXY.search(subject):
            continue
        out.append((sha, subject))
        if len(out) >= limit:
            break
    return out


def changed_sql(repo: str, sha: str) -> list[str]:
    out = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha, ok_fail=True)
    return [p for p in out.splitlines()
            if p.endswith(".sql") and "/models/" in f"/{p}"]


def blob(repo: str, sha: str, path: str) -> str | None:
    p = subprocess.run(["git", "-C", repo, "show", f"{sha}:{path}"],
                       capture_output=True, text=True, check=False)
    return p.stdout if p.returncode == 0 else None


def _fire(sql: str, name: str) -> tuple[set, str]:
    """Which SQL-only checks fire on this blob. Returns (checks, skip_reason)."""
    d = digest(dejinja(sql), name)
    if not d.ok:
        return set(), f"could not parse after a Jinja strip: {d.error}"
    uid = f"model.replay.{name}"
    shim_model = type("M", (), {"name": name, "path": name, "readable": True, "compiled": sql})()
    shim = type("P", (), {
        "models": {uid: shim_model},
        "blast_radius": staticmethod(lambda _u: {"descendants": 0, "marts": 0}),
    })()
    fired = set()
    for fn in SQL_ONLY_CHECKS:
        for f in fn(shim, {uid: d}):
            fired.add(f.check)
    return fired, ""


def run(repo: str, limit: int = 60, since: str | None = None,
        fix_like_only: bool = False) -> list[Replay]:
    out: list[Replay] = []
    for sha, subject in commits(repo, limit, since, fix_like_only):
        for path in changed_sql(repo, sha):
            name = path.rsplit("/", 1)[-1].removesuffix(".sql")
            after_sql = blob(repo, sha, path)
            before_sql = blob(repo, f"{sha}~1", path)
            r = Replay(sha=sha[:9], subject=subject[:90], model=name, path=path,
                       message_says_fix=bool(FIXY.search(subject)))
            if after_sql is None or before_sql is None:
                r.skipped = "the file was added or removed by this commit"
                out.append(r)
                continue
            r.before, skip_b = _fire(before_sql, name)
            r.after, skip_a = _fire(after_sql, name)
            if skip_b or skip_a:
                r.skipped = skip_b or skip_a
            out.append(r)
    return out


def tally(replays: list[Replay]) -> dict:
    """*** THE DENOMINATOR IS REPLAYS WHERE SOMETHING WAS FIRING, NOT ALL OF THEM. ***

    Dividing catches by every commit measures how often people touch models that never had the
    defect, which is near zero by construction and says nothing. The question a check has to answer
    is: when it WAS firing, did a later commit silence it.
    """
    counts: dict = {}
    for r in replays:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    counts["skipped"] = counts.get("no_pair", 0) + counts.get("unparseable", 0)
    had_something = counts.get("caught", 0) + counts.get("still_firing", 0)
    counts["_silenced_rate"] = (counts.get("caught", 0) / had_something) if had_something else None
    counts["_had_something"] = had_something
    return counts


def skip_reasons(replays: list[Replay]) -> dict:
    out: dict = {}
    for r in replays:
        if r.skipped:
            key = r.skipped.split(":")[0][:60]
            out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
