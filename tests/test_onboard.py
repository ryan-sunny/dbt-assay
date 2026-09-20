"""`onboard` is the first thing anyone runs, so it is where a wrong answer does the most damage.

It must work with no key, no catalog, no store and no audit.yml, and it must SAY which of those is
missing rather than quietly producing a thinner answer that looks the same.
"""
from pathlib import Path

from typer.testing import CliRunner

from dbt_assay.cli import app

runner = CliRunner()


def test_it_runs_with_nothing_configured_and_writes_a_config(project_dir, tmp_path):
    cfg = tmp_path / "cfg"
    r = runner.invoke(app, ["onboard", "-t", str(project_dir), "--config", str(cfg),
                            "--store", str(tmp_path / "nope.duckdb")])
    assert r.exit_code == 0, r.output
    assert (cfg / "audit.yml").exists()
    for section in ("what assay found", "what it can see", "no key and no spend", "next"):
        assert section in r.output


def test_it_does_not_clobber_an_audit_yml_that_is_already_there(project_dir, tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "audit.yml").write_text("# mine\n")
    r = runner.invoke(app, ["onboard", "-t", str(project_dir), "--config", str(cfg)])
    assert r.exit_code == 0, r.output
    assert (cfg / "audit.yml").read_text() == "# mine\n"


def test_it_names_the_dialect_it_is_about_to_parse_with(project_dir, tmp_path):
    """A user whose warehouse is Snowflake must be able to SEE that assay agreed, in the first
    panel, before it shows anything it found."""
    r = runner.invoke(app, ["onboard", "-t", str(project_dir), "--config", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "duckdb" in r.output


def test_the_agent_flag_writes_the_skill_where_an_agent_will_look(project_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["onboard", "-t", str(project_dir), "--config", str(tmp_path),
                            "--agent"])
    assert r.exit_code == 0, r.output
    p = Path(tmp_path) / ".claude/skills/dbt-assay/SKILL.md"
    assert p.exists()
    assert "assay" in p.read_text()
    assert "claude mcp add assay" in r.output
