"""One dbt for a whole command (sunny-data box, RC 62c18a6: twelve warm `dbt show` calls at 16 to
19 seconds each, nearly all of it dbt starting). The held session must return exactly what a
`dbt show` subprocess returns, fail the way it fails, and hand back to the subprocess when it
cannot run."""
import os
import shutil
import subprocess

import pytest

from dbt_assay import dbtsession, probe

JAFFLE_SQL = "select 1 as n, 'a' as s union all select 2, null"
# How dbt runs here. `uv run dbt` when dbt-duckdb is in this environment; otherwise point it at one
# that has it, e.g. ASSAY_TEST_DBT="uv run --project ../jaffle dbt".
DBT = os.environ.get("ASSAY_TEST_DBT", "uv run dbt")


def _project(tmp_path):
    """A one-model dbt-duckdb project, or skip where dbt-duckdb is not installed for `uv run`."""
    if not shutil.which("uv"):
        pytest.skip("no uv")
    ok = subprocess.run([*dbtsession.interpreter(DBT), "-c", "import dbt.adapters.duckdb"],
                        capture_output=True, cwd=tmp_path, check=False)
    if ok.returncode != 0:
        pytest.skip("dbt-duckdb is not installed here")
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "m.sql").write_text("select 1 as id")
    (tmp_path / "dbt_project.yml").write_text(
        "name: p\nversion: '1.0.0'\nprofile: p\nconfig-version: 2\n")
    (tmp_path / "profiles.yml").write_text(
        "p:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n      path: p.duckdb\n")
    return tmp_path


def test_the_session_answers_what_a_subprocess_answers(tmp_path, monkeypatch):
    proj = _project(tmp_path)
    monkeypatch.setenv("ASSAY_DBT_SESSION", "0")
    probe._REACHED.clear()
    one = probe._execute(JAFFLE_SQL, str(proj), str(proj), DBT)
    monkeypatch.setenv("ASSAY_DBT_SESSION", "1")
    probe._REACHED.clear()
    held = probe._execute(JAFFLE_SQL, str(proj), str(proj), DBT)
    assert not one.failed and not held.failed, (one.why, held.why)
    assert held.rows == one.rows == [{"n": 1, "s": "a"}, {"n": 2, "s": None}]
    # a statement dbt refuses is a failure with dbt's words, not an empty table
    bad = probe._execute("select * from no_such_table", str(proj), str(proj), DBT)
    assert bad.failed and "no_such_table" in bad.why.lower()
    # ...and the session is still up for the next one, which is the whole point
    again = probe._execute("select 3 as n", str(proj), str(proj), DBT)
    assert again.rows == [{"n": 3}]
    assert len(dbtsession._SESSIONS) == 1
    dbtsession.close_all()


def test_a_dbt_it_cannot_run_in_process_keeps_the_subprocess(tmp_path, monkeypatch):
    sh = tmp_path / "dbt"
    sh.write_text("#!/bin/sh\nexec real-dbt \"$@\"\n")
    sh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    assert dbtsession.interpreter("dbt") is None
    assert dbtsession.interpreter("uv run dbt") == ["uv", "run", "python"]
    assert dbtsession.interpreter("poetry run dbt") == ["poetry", "run", "python"]
    assert dbtsession.interpreter("make") is None
