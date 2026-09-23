"""`check --select` and `--new-only`: the two flags that let assay sit in an edit hook.

*** A TEN-MINUTE, WHOLE-PROJECT COMMAND CANNOT GATE ONE EDIT. ***
Every piece of assay's enforcement was skill prose asking an agent to remember, and the field
measured what that is worth. A hook stops the agent whether or not it read anything, and a hook
needs a command that answers about ONE model, against what was there before the edit.
"""
import json
from pathlib import Path

from typer.testing import CliRunner

from dbt_assay.cli import app
from dbt_assay.live import new_findings

runner = CliRunner()


def _compiled(target: Path, name: str) -> Path:
    return next((target / "compiled").rglob(f"{name}.sql"))


def test_select_reports_only_the_selected_model(project_dir, tmp_path):
    r = runner.invoke(app, ["check", "-t", str(project_dir), "--store", str(tmp_path / "s.duckdb"),
                            "--config", str(tmp_path), "--select", "stg_bad_tilde", "--json"])
    assert r.exit_code == 0, r.output
    doc = json.loads(r.output)
    assert doc["scope"] == ["stg_bad_tilde"]
    assert doc["findings"], "the fixture's `~ 'Denver'` is a finding on this model"
    assert {f["model"] for f in doc["findings"]} == {"stg_bad_tilde"}


def test_a_scoped_run_is_never_written_as_a_run(project_dir, tmp_path):
    """If it were, the next full run's `vs previous run` would call everything outside the
    selection resolved, and a hook's own run would become its baseline."""
    store = tmp_path / "s.duckdb"
    runner.invoke(app, ["check", "-t", str(project_dir), "--store", str(store),
                        "--config", str(tmp_path), "--select", "stg_bad_tilde"])
    import duckdb
    if store.exists():
        con = duckdb.connect(str(store))
        assert con.execute("select count(*) from runs").fetchone()[0] == 0
        con.close()


def test_a_selector_matching_nothing_is_an_error_not_a_clean_pass(project_dir, tmp_path):
    r = runner.invoke(app, ["check", "-t", str(project_dir), "--config", str(tmp_path),
                            "--store", str(tmp_path / "s.duckdb"), "--select", "no_such_model"])
    assert r.exit_code == 2
    assert "matches no model" in " ".join(r.output.split())


def test_new_only_without_a_baseline_refuses_rather_than_passing(project_dir, tmp_path):
    r = runner.invoke(app, ["check", "-t", str(project_dir), "--config", str(tmp_path),
                            "--store", str(tmp_path / "none.duckdb"), "--new-only"])
    assert r.exit_code == 2
    assert "baseline" in r.output


def test_new_only_catches_what_the_edit_introduced_and_nothing_else(project_dir, tmp_path):
    store = str(tmp_path / "s.duckdb")
    base = ["-t", str(project_dir), "--store", store, "--config", str(tmp_path)]
    assert runner.invoke(app, ["check", *base]).exit_code == 0          # the baseline

    # Unchanged: the existing `stg_bad_tilde` finding is old, so nothing is new.
    r = runner.invoke(app, ["check", *base, "--select", "stg_bad_tilde", "--new-only", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["findings"] == []

    # The edit: a pattern with no metacharacters under `~` in a model that was clean.
    _compiled(project_dir, "stg_ok_tilde").write_text("select id from raw.t where name ~ 'Boulder'")
    r = runner.invoke(app, ["check", *base, "--select", "stg_ok_tilde", "--new-only", "--json"])
    assert r.exit_code == 1, r.output
    doc = json.loads(r.output)
    assert doc["baseline"]["run_id"]
    assert [f["model"] for f in doc["findings"]] == ["stg_ok_tilde"]

    # And blocking once does not make it old: the scoped run wrote nothing.
    r = runner.invoke(app, ["check", *base, "--select", "stg_ok_tilde", "--new-only", "--json"])
    assert r.exit_code == 1


class _F:
    def __init__(self, fid, check, subject):
        self.id, self.check, self.subject = fid, check, subject


def test_a_reworded_finding_is_matched_by_count_not_called_new():
    base = [("a", "test_cannot_fail", "m"), ("b", "test_cannot_fail", "m")]
    now = [_F("a", "test_cannot_fail", "m"), _F("b2", "test_cannot_fail", "m")]
    assert new_findings(now, base) == []
    third = _F("c", "test_cannot_fail", "m")
    assert new_findings([*now, third], base) == [third]
    other = _F("d", "arbitrary_pick", "m")
    assert new_findings([*now, other], base) == [other]
