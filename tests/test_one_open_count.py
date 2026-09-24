"""Every surface that says what is open says the same number. (Field report, after 0.51.0.)"""
from __future__ import annotations

import json

from typer.testing import CliRunner

from dbt_assay.cli import app
from dbt_assay.mcp_server import Backend

runner = CliRunner()


def test_check_mcp_and_history_agree_and_a_dismissal_counts_nowhere(project_dir, tmp_path):
    store = tmp_path / "s.duckdb"
    args = ["-t", str(project_dir), "--store", str(store), "--config", str(tmp_path)]
    runner.invoke(app, ["check", *args])
    first = json.loads(runner.invoke(app, ["check", *args, "--json"]).stdout)["findings"][0]
    r = runner.invoke(app, ["review", "--store", str(store), "--finding", first["finding"],
                            "--verdict", "disagree", "--note", "wrong", "-t", str(project_dir)])
    assert r.exit_code == 0, r.output
    ck = json.loads(runner.invoke(app, ["check", *args, "--json"]).stdout)["findings"]
    assert first["finding"] not in {f["finding"] for f in ck}
    mcp = Backend(str(project_dir), str(store), str(tmp_path)).findings(limit=10_000)
    assert len(mcp["findings"]) == len(ck), (len(mcp["findings"]), len(ck))
    assert first["finding"] not in {f["finding"] for f in mcp["findings"]}
    assert mcp.get("not_shown_because_ruled_or_waived", 0) >= 1
    hist = json.loads(runner.invoke(app, ["history", *args, "--json"]).stdout)
    assert hist["open_findings"] == len(ck)
