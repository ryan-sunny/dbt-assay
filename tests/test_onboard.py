"""`onboard` is the first thing anyone runs, so it is where a wrong answer does the most damage.

It must work with no key, no catalog, no store and no audit.yml, and it must SAY which of those is
missing rather than quietly producing a thinner answer that looks the same.
"""
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbt_assay.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_spend(monkeypatch):
    """*** THE TESTS MUST NOT BILL A DEVELOPER WHO HAPPENS TO HAVE A KEY. ***

    onboard now runs the judgment tier by default, which is the point of it. That makes a bare
    `pytest` on a machine with TYPESAFE_API_KEY in the environment a real charge against a real
    account, for no test value. Clearing both names covers every provider assay resolves.
    """
    for name in ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_it_runs_with_nothing_configured_and_writes_a_config(project_dir, tmp_path):
    cfg = tmp_path / "cfg"
    r = runner.invoke(app, ["onboard", "-t", str(project_dir), "--config", str(cfg),
                            "--store", str(tmp_path / "nope.duckdb")])
    assert r.exit_code == 0, r.output
    assert (cfg / "audit.yml").exists()
    for section in ("what assay found", "what it can see", "no key and no spend",
                    "what only judgment can see", "next"):
        assert section in r.output


def test_without_a_key_it_shows_the_question_rather_than_selling_the_tier(project_dir, tmp_path):
    """Someone deciding whether a key is worth it should see the real state and the real question,
    on their own model, for free."""
    r = runner.invoke(app, ["onboard", "-t", str(project_dir), "--config", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "no API key" in r.output
    assert "would ask about" in r.output or "no model in this project carries a description" \
        in r.output


def test_no_judge_asks_nothing_even_where_a_key_exists(project_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-not-a-real-key")
    r = runner.invoke(app, ["onboard", "-t", str(project_dir), "--config", str(tmp_path),
                            "--no-judge"])
    assert r.exit_code == 0, r.output
    assert "--no-judge was passed" in r.output


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


def test_a_description_many_models_share_is_not_judged(project_dir):
    """*** MEASURED: REPEATED PROSE PRODUCED HALF THE FINDINGS AND NONE OF THE VALUE. ***

    On a 265-model warehouse, 84 of 343 descriptions were shared by two or more models, and they
    were 8 of the 16 description findings on a first judged run. Each one was true and useless:
    "Staging model: light cleanup of one raw source" reads as contradicting any model that also
    filters, because template prose never mentions what the model does.
    """
    from dbt_assay import inventory as inv
    from dbt_assay import semantics as sem
    from dbt_assay.infer import Schema, derive_columns
    from dbt_assay.manifest import Project
    from dbt_assay.parse import digest

    p = Project.load(project_dir)
    d = {u: digest(m.compiled, m.name, p.dialect) for u, m in p.models.items() if m.compiled}
    sch = Schema.load(p, project_dir)
    derive_columns(p, d, sch)

    shared = sem.boilerplate(p)
    assert any("light cleanup" in t for t in shared)

    judged = {s.name for s in sem.subjects(p, d, sch, inv.build(p, d, sch, None, {})) if s.purpose}
    assert "stg_bad_notnull" in judged            # its description is its own
    for name in ("stg_ok_notnull", "stg_bad_accepted", "stg_bad_tilde"):
        assert name not in judged, f"{name} shares its description with two other models"


def test_it_does_not_tell_you_to_export_a_key_you_already_have(project_dir, tmp_path, monkeypatch):
    """*** --no-judge WITH A KEY IS A CHOICE, NOT A MISSING CAPABILITY. ***

    Telling someone to export what they already exported is the same defect this tool exists to
    find, in its own output. It has now been that defect four times.
    """
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-not-real")
    r = runner.invoke(app, ["onboard", "-t", str(project_dir), "--config", str(tmp_path),
                            "--no-judge"])
    assert r.exit_code == 0, r.output
    assert "export TYPESAFE_API_KEY" not in r.output
    assert "--no-judge was passed" in r.output


def test_compiling_is_offered_and_never_automatic(project_dir, tmp_path):
    """*** `dbt compile` NEEDS A WAREHOUSE CONNECTION AND CAN TAKE MINUTES. ***

    Running it because assay felt like it, on someone else's first invocation, is how a tool gets
    uninstalled. Without the flag, onboard says the option exists and does nothing.
    """
    r = runner.invoke(app, ["onboard", "-t", str(project_dir), "--config", str(tmp_path),
                            "--no-judge"])
    assert r.exit_code == 0, r.output
    assert "compiling:" not in r.output         # never without the flag

    # ...and the fixture has no gap, so the offer correctly stays silent too. The offer is tied to
    # the gap, not printed unconditionally: a project with nothing missing should hear nothing.
    assert "have no compiled SQL" not in r.output

    h = runner.invoke(app, ["onboard", "--help"])
    assert "--compile" in h.output
    assert "never automatic" in " ".join(h.output.split())


def test_it_refuses_to_compile_where_there_is_no_project():
    """A manifest copied somewhere for inspection has no project above it, and running dbt in the
    wrong directory is worse than not running it."""
    from pathlib import Path

    from dbt_assay.cli import _project_dir_for, _run_dbt_compile
    ok, why = _run_dbt_compile(Path("/tmp"))
    assert ok is False
    assert "dbt_project.yml" in why
    assert _project_dir_for(Path("/tmp")) is None


def test_a_missing_dbt_binary_is_an_instruction_not_a_traceback(tmp_path):
    (tmp_path / "dbt_project.yml").write_text("name: p\n")
    t = tmp_path / "target"
    t.mkdir()
    from dbt_assay.cli import _run_dbt_compile
    ok, why = _run_dbt_compile(t, dbt_bin="definitely-not-a-real-dbt")
    assert ok is False
    assert "--dbt" in why
