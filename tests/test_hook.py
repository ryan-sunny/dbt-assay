"""The edit gate: `assay hook`, and `onboard --agent` installing it rather than describing it."""
import json
from pathlib import Path

from typer.testing import CliRunner

from dbt_assay.cli import app
from dbt_assay.hook import install, settings_entry

runner = CliRunner()


def _payload(path: Path) -> str:
    return json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(path)}})


def _project(project_dir: Path) -> Path:
    root = project_dir.parent
    (root / "dbt_project.yml").write_text("name: p\nmodel-paths: ['models']\n")
    return root


def _args(project_dir, tmp_path):
    return ["hook", "post-edit", "-t", str(project_dir), "--store", str(tmp_path / "s.duckdb"),
            "--config", str(tmp_path), "--no-compile"]


def test_a_file_that_is_not_a_model_passes_untouched(project_dir, tmp_path):
    root = _project(project_dir)
    r = runner.invoke(app, _args(project_dir, tmp_path), input=_payload(root / "README.md"))
    assert r.exit_code == 0


def test_no_baseline_stops_the_agent_and_says_why(project_dir, tmp_path):
    root = _project(project_dir)
    r = runner.invoke(app, _args(project_dir, tmp_path),
                      input=_payload(root / "models/staging/stg_ok_tilde.sql"))
    assert r.exit_code == 2
    assert "baseline" in r.output


def test_an_edit_that_introduces_a_finding_is_stopped_with_the_finding_as_reason(
        project_dir, tmp_path):
    root = _project(project_dir)
    store = str(tmp_path / "s.duckdb")
    assert runner.invoke(app, ["check", "-t", str(project_dir), "--store", store,
                               "--config", str(tmp_path)]).exit_code == 0
    edited = root / "models/staging/stg_ok_tilde.sql"
    r = runner.invoke(app, _args(project_dir, tmp_path), input=_payload(edited))
    assert r.exit_code == 0, r.output                       # untouched: nothing new

    next((project_dir / "compiled").rglob("stg_ok_tilde.sql")).write_text(
        "select id from raw.t where name ~ 'Boulder'")
    r = runner.invoke(app, _args(project_dir, tmp_path), input=_payload(edited))
    assert r.exit_code == 2
    assert "stg_ok_tilde" in r.output and "introduced 1 finding" in r.output
    # an existing finding on ANOTHER model never blocks this one
    r = runner.invoke(app, _args(project_dir, tmp_path),
                      input=_payload(root / "models/staging/stg_bad_tilde.sql"))
    assert r.exit_code == 0, r.output


def test_install_merges_and_never_clobbers(tmp_path):
    s = tmp_path / ".claude" / "settings.json"
    s.parent.mkdir()
    theirs = {"permissions": {"allow": ["Bash(ls)"]},
              "hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [
                  {"type": "command", "command": "echo theirs"}]}]}}
    s.write_text(json.dumps(theirs))
    assert install(s, "assay hook post-edit --target t") == "added"
    assert install(s, "assay hook post-edit --target t2") == "updated"
    doc = json.loads(s.read_text())
    assert doc["permissions"] == theirs["permissions"]
    cmds = [h["command"] for e in doc["hooks"]["PostToolUse"] for h in e["hooks"]]
    assert cmds == ["echo theirs", "assay hook post-edit --target t2"]


def test_install_refuses_a_settings_file_it_cannot_read(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text("{not json")
    try:
        install(s, "x")
    except ValueError as e:
        assert "left alone" in str(e)
    else:
        raise AssertionError("an unreadable settings file must not be overwritten")
    assert s.read_text() == "{not json"


def test_onboard_agent_installs_the_gate(project_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    r = runner.invoke(app, ["onboard", "-t", str(project_dir), "--agent", "--config", str(tmp_path),
                            "--store", str(tmp_path / "s.duckdb")])
    assert r.exit_code == 0, r.output
    doc = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    entry = doc["hooks"]["PostToolUse"][0]
    assert entry["matcher"] == settings_entry("x")["matcher"]
    assert "assay hook post-edit" in entry["hooks"][0]["command"]
