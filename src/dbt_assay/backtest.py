"""Replay a repository's own history and measure whether the checks catch what it already fixed.

*** THE LABELED SET NOBODY WROTE ON PURPOSE. ***
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

*** A STRIP BY DEFAULT, A REAL COMPILE WHERE THE STRIP FAILED. ***
Historical compiled SQL does not exist, so `ref()` and `source()` become relation names and control
blocks are removed. That is fast and covers 83% of blobs on a real repo. It is not a compile, and a
macro-GENERATED model will not survive it.

But the remaining 17% are not a wall, only a cost. `--compile` checks the commit out into a
DETACHED WORKTREE -- never the working tree, so nothing collides with other work in the clone --
links this checkout's `dbt_packages` so no network fetch is needed, and runs `dbt compile --select`
for just the models in question. It is used as a FALLBACK, not a replacement: only blobs the strip
could not read pay the seconds-per-commit that a project parse costs.

What compiling still cannot promise is that the packages and dbt version of today produce exactly
what that commit produced years ago. Fidelity is much better, not perfect, and a compile that fails
is reported rather than counted as clean.
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
    # *** AN IDENTIFIER, NOT A LITERAL. ***
    # What is left is a macro call or a var. Substituting `1` only parses where a VALUE belongs,
    # so anything standing in for a table name, a column or a clause died. Measured on a real
    # Snowflake project assay had never seen: `1` parsed 14 of 25 models, an identifier parsed 21.
    sql = _EXPR.sub("_jinja_", sql)
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
    via: str = "stripped"                      # stripped | compiled | cached

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


class Compiler:
    """A detached worktree that compiles one commit at a time.

    *** `git worktree`, NEVER A CHECKOUT IN THE MAIN CLONE. ***
    The working tree, the index and HEAD are untouched, so this is safe to run while somebody else
    is working in the same repository.
    """

    def __init__(self, repo: str, project_subdir: str = ".", dbt_bin: str = "dbt",
                 profiles_dir: str | None = None):
        import os
        import tempfile
        # *** ABSOLUTE, BECAUSE A SYMLINK'S TARGET RESOLVES FROM WHERE THE LINK LIVES. ***
        # `--repo .` wrote `dbt_packages -> ./transform/dbt_packages` into the temp worktree, which
        # resolves inside the temp dir and points at nothing, and the second commit died on it.
        self.repo = os.path.abspath(repo)
        self.project_subdir = project_subdir
        self.dbt_bin = dbt_bin
        # *** dbt NEEDS A CONNECTION EVEN TO COMPILE. ***
        # A fresh worktree has no warehouse file, so the project's real profile fails with an IO
        # error before a single model is rendered. Rather than point a replay at the live
        # warehouse -- which another session may be writing to -- assay generates a THROWAWAY
        # DuckDB profile under the project's own profile name. Compiling renders SQL; it does not
        # read data, so an empty database is enough.
        self.profiles_dir = profiles_dir or self._throwaway_profile(tempfile.mkdtemp())
        self.dir = tempfile.mkdtemp(prefix="assay-replay-")
        self.ok = False
        self.reason = ""
        try:
            _git(repo, "worktree", "add", "--detach", self.dir, "HEAD")
            self.ok = True
        except RuntimeError as e:
            self.reason = str(e)[:160]

    def _profile_name(self) -> str:
        import os

        import yaml
        pj = os.path.join(self.repo, self.project_subdir, "dbt_project.yml")
        try:
            with open(pj) as fh:
                return (yaml.safe_load(fh) or {}).get("profile") or "default"
        except Exception:                                               # noqa: BLE001
            return "default"

    def _throwaway_profile(self, d: str) -> str:
        """An empty DuckDB under the project's profile name.

        The caveat, stated rather than hidden: on a Snowflake or BigQuery project this compiles
        through DuckDB's adapter, so a macro that dispatches on the adapter can render differently
        than it did in production. Pass --profiles-dir to use the real one instead.
        """
        import os
        name = self._profile_name()
        with open(os.path.join(d, "profiles.yml"), "w") as fh:
            fh.write(f"{name}:\n  target: assay\n  outputs:\n    assay:\n"
                     f"      type: duckdb\n      path: {os.path.join(d, 'replay.duckdb')}\n"
                     f"      threads: 1\n")
        return d

    def _link_packages(self) -> None:
        import os
        src = os.path.join(self.repo, self.project_subdir, "dbt_packages")
        dst = os.path.join(self.dir, self.project_subdir, "dbt_packages")
        # `lexists`, not `exists`: `exists` follows a link, so a BROKEN one read as absent and
        # `symlink` then failed on the path that was there.
        if os.path.isdir(src) and not os.path.lexists(dst):
            os.symlink(src, dst)              # this era's packages, no network fetch

    def compiled(self, sha: str, model_names: list[str]) -> dict:
        """{model_name: compiled_sql} for what dbt could build at that commit."""
        import os
        if not self.ok:
            return {}
        try:
            _git(self.dir, "checkout", "--detach", "--force", sha)
        except RuntimeError:
            return {}
        self._link_packages()
        proj = os.path.join(self.dir, self.project_subdir)
        from .probe import profiles_args
        cmd = [*self.dbt_bin.split(), "compile", "--select", *model_names,
               "--target-path", "target", *profiles_args(self.profiles_dir)]
        subprocess.run(cmd, cwd=proj, capture_output=True, text=True,
                       timeout=900, check=False)
        out = {}
        root = os.path.join(proj, "target", "compiled")
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.endswith(".sql") and f[:-4] in model_names:
                    with open(os.path.join(dirpath, f)) as fh:
                        out[f[:-4]] = fh.read()
        return out

    def close(self) -> None:
        if self.ok:
            _git(self.repo, "worktree", "remove", "--force", self.dir, ok_fail=True)


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
        fix_like_only: bool = False, compiler: Compiler | None = None,
        on_commit=None, cache=None) -> list[Replay]:
    """`cache` is a store: a blob whose dbt checksum it holds is replayed from the compiled body
    recorded for it -- exact, free, no warehouse -- and a body `--compile` pays for is kept."""
    from . import history
    out: list[Replay] = []
    todo = commits(repo, limit, since, fix_like_only)
    for i, (sha, subject) in enumerate(todo):
        if on_commit:
            on_commit(i, len(todo), subject)
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
            # *** BOTH SIDES FROM ONE SOURCE, OR NEITHER. *** A compiled "before" against a
            # stripped "after" differs by the strip, and that difference would read as a catch.
            got_b = history.compiled_for(cache, before_sql) if cache is not None else None
            got_a = history.compiled_for(cache, after_sql) if cache is not None else None
            if got_b and got_a:
                r.before, skip_b = _fire_compiled(got_b, name)
                r.after, skip_a = _fire_compiled(got_a, name)
                r.via = "cached"
                if skip_b or skip_a:
                    r.skipped = skip_b or skip_a
                out.append(r)
                continue
            r.before, skip_b = _fire(before_sql, name)
            r.after, skip_a = _fire(after_sql, name)
            if (skip_b or skip_a) and compiler is not None:
                # The strip could not read it. Pay for a real compile, but only here.
                got_a = compiler.compiled(sha, [name]).get(name)
                got_b = compiler.compiled(f"{sha}~1", [name]).get(name)
                if got_a and got_b:
                    r.before, skip_b = _fire_compiled(got_b, name)
                    r.after, skip_a = _fire_compiled(got_a, name)
                    r.via = "compiled"
                    if cache is not None:
                        # Paid for once; the next replay of either version reads it for free.
                        history.remember(cache, before_sql, name, got_b)
                        history.remember(cache, after_sql, name, got_a)
            if skip_b or skip_a:
                r.skipped = skip_b or skip_a
            out.append(r)
    return out


def _fire_compiled(sql: str, name: str) -> tuple[set, str]:
    """Compiled SQL needs no Jinja strip; it is already what the warehouse ran."""
    d = digest(sql, name)
    if not d.ok:
        return set(), f"compiled output still did not parse: {d.error}"
    uid = f"model.replay.{name}"
    shim_model = type("M", (), {"name": name, "path": name, "readable": True, "compiled": sql})()
    shim = type("P", (), {"models": {uid: shim_model},
                          "blast_radius": staticmethod(lambda _u: {"descendants": 0, "marts": 0})})()
    fired = set()
    for fn in SQL_ONLY_CHECKS:
        for f in fn(shim, {uid: d}):
            fired.add(f.check)
    return fired, ""


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


def dark_models(replays: list[Replay]) -> list[tuple[str, int, int]]:
    """(model, unreadable replays, every replay of it), most unreadable first.

    *** "26 OF 85 COULD NOT BE READ" CANNOT BE ACTED ON. ***
    Reported from the field (25.24d): on a real repo that 31% was four much-edited provenance
    models, jinja-heavy because they enumerate sources -- not a broad blind spot. A blind spot is
    only judgeable when its shape is visible, which is assay's own argument pointed at itself.
    """
    total: dict = {}
    dark: dict = {}
    for r in replays:
        total[r.model] = total.get(r.model, 0) + 1
        if r.verdict == "unparseable":
            dark[r.model] = dark.get(r.model, 0) + 1
    return sorted(((m, n, total[m]) for m, n in dark.items()), key=lambda x: (-x[1], x[0]))
