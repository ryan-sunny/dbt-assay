"""History: compiled SQL kept by dbt's own checksum, commits recorded, and when a finding was
first seen -- never claimed as when it was introduced.
"""
from __future__ import annotations

import hashlib
import json
import subprocess

from typer.testing import CliRunner

from dbt_assay import backtest, history
from dbt_assay.cli import _load, app
from dbt_assay.store import Store

runner = CliRunner()


def test_the_checksum_is_dbts_own():
    """sha256 of the file's text, surrounding whitespace stripped: 328 of 328 on the field."""
    text = "\n  select 1 as x\n\n"
    assert history.checksum(text) == hashlib.sha256(b"select 1 as x").hexdigest()


def test_check_keeps_every_compiled_version_and_the_commit_it_ran_at(project_dir, tmp_path):
    store = tmp_path / "s.duckdb"
    r = runner.invoke(app, ["check", "-t", str(project_dir), "--store", str(store),
                            "--config", str(tmp_path)])
    assert r.exit_code in (0, 1), r.output
    s = Store(str(store))
    try:
        project, *_ = _load(project_dir, None)
        n = s.con.execute("select count(*) from compiled_sql").fetchone()[0]
        assert n == sum(1 for m in project.models.values() if m.checksum and m.compiled)
        # and a second harvest of the same versions adds nothing
        assert history.harvest(s, project) == 0
    finally:
        s.close()


def _repo(tmp_path):
    """A git repo whose model changes once, with a CASE the Jinja strip cannot read."""
    repo = tmp_path / "repo"
    (repo / "models").mkdir(parents=True)

    def git(*a):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True,
                       env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"})
    git("init", "-q")
    v1 = "{% for x in [1] %}{% endfor %}\nselect {{ weird('a' ~ 'b') }} as c from t\n"
    v2 = "{% for x in [1] %}{% endfor %}\nselect {{ weird('a' ~ 'c') }} as c from t\n"
    (repo / "models" / "m.sql").write_text(v1)
    git("add", "."); git("commit", "-q", "-m", "first")
    (repo / "models" / "m.sql").write_text(v2)
    git("add", "."); git("commit", "-q", "-m", "the window now breaks ties on the id")
    return repo, v1, v2


def test_a_replay_reads_the_compiled_version_kept_for_it_and_compiles_nothing(tmp_path):
    repo, v1, v2 = _repo(tmp_path)
    s = Store(str(tmp_path / "s.duckdb"))
    try:
        history.remember(s, v1, "m", "select a as c from t")
        history.remember(s, v2, "m", "select b as c from t")
        cached = [r for r in backtest.run(str(repo), limit=5, cache=s)
                  if "added or removed" not in r.skipped]
        assert [r.via for r in cached] == ["cached"] and not cached[0].skipped
    finally:
        s.close()


def test_one_side_cached_is_not_enough(tmp_path):
    """A compiled `before` against a stripped `after` differs by the strip and reads as a catch."""
    repo, v1, _v2 = _repo(tmp_path)
    s = Store(str(tmp_path / "s.duckdb"))
    try:
        history.remember(s, v1, "m", "select a as c from t")
        got = [r for r in backtest.run(str(repo), limit=5, cache=s)
               if "added or removed" not in r.skipped]
        assert all(r.via != "cached" for r in got)
    finally:
        s.close()


def test_commits_are_recorded_with_the_models_they_touched(tmp_path):
    repo, _v1, _v2 = _repo(tmp_path)
    project = type("P", (), {"models": {"model.p.m": type("M", (), {
        "path": "models/m.sql", "name": "m"})()}})()
    s = Store(str(tmp_path / "s.duckdb"))
    try:
        assert history.sync_commits(s, repo, project) == 2
        assert history.sync_commits(s, repo, project) == 0, "a second sync adds nothing"
        rows = s.con.execute("select subject, models from commits order by committed_at").fetchall()
        assert {r[0] for r in rows} == {"first", "the window now breaks ties on the id"}
        assert all(json.loads(r[1]) == ["m"] for r in rows)
        assert history.churn(s) == [("m", 2)]
    finally:
        s.close()


def test_history_says_first_seen_and_never_introduced(project_dir, tmp_path):
    store = tmp_path / "s.duckdb"
    runner.invoke(app, ["check", "-t", str(project_dir), "--store", str(store),
                        "--config", str(tmp_path)])
    r = runner.invoke(app, ["history", "-t", str(project_dir), "--store", str(store), "--json"])
    assert r.exit_code == 0, r.output
    doc = json.loads(r.stdout)
    assert doc["open_findings_dated"] == doc["open_findings"] > 0
    assert doc["compiled_versions_kept"] > 0
    human = runner.invoke(app, ["history", "-t", str(project_dir), "--store", str(store)])
    assert "not necessarily when it was introduced" in " ".join(human.output.split())
