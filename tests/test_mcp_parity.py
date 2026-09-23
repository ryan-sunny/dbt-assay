"""Every CLI command is an MCP tool, and a tool runs exactly what the command runs."""
import asyncio
import time

import pytest

from dbt_assay import cli_tools
from dbt_assay.mcp_server import Backend


def test_every_command_has_a_tool_on_the_real_server(project_dir):
    pytest.importorskip("mcp")
    from dbt_assay.mcp_server import build_app
    names = {t.name for t in asyncio.run(build_app(str(project_dir)).list_tools())}
    missing = [c["name"] for c in cli_tools.commands()
               if cli_tools.tool_name(c["name"]) not in names]
    assert not missing, f"commands with no tool: {missing}"
    assert {"job_status", "job_stop", "jobs"} <= names
    assert len(cli_tools.commands()) > 40, "the command reader found almost nothing"


def test_a_tool_returns_the_commands_own_json_with_the_servers_target(project_dir, tmp_path):
    be = Backend(str(project_dir), str(tmp_path / "s.duckdb"), str(tmp_path))
    r = be.run_cli("check", "--select stg_bad_tilde --json", wait_seconds=120)
    assert r["exit_code"] == 0, r
    assert [f["model"] for f in r["json"]["findings"]] == ["stg_bad_tilde"] * len(
        r["json"]["findings"]) and r["json"]["findings"]


def test_a_slow_run_becomes_a_job_and_the_job_finishes(project_dir, tmp_path):
    be = Backend(str(project_dir), str(tmp_path / "s.duckdb"), str(tmp_path))
    r = be.run_cli("check", "--json", wait_seconds=0)
    assert r["status"] == "running" and r["job"]
    for _ in range(120):
        s = cli_tools.status(r["job"])
        if s["status"] == "finished":
            break
        time.sleep(0.5)
    assert s["status"] == "finished" and s["exit_code"] == 0 and "json" in s
    assert any(j["job"] == r["job"] for j in cli_tools.listing()["jobs"])


def test_the_keyboard_command_is_refused_with_the_routes_that_work(project_dir):
    r = Backend(str(project_dir)).run_cli("review", "-i")
    assert "keypresses" in r["error"] and "rule" in r["error"]


def test_an_unknown_command_is_named(project_dir):
    assert "no command" in Backend(str(project_dir)).run_cli("nonesuch")["error"]


def test_every_command_answers_as_a_module_the_way_the_tools_run_it():
    """`python -m dbt_assay.cli <cmd>` is how every assay_<cmd> tool and the edit hook run a command."""
    import subprocess
    import sys
    bad = []
    for c in cli_tools.commands():
        r = subprocess.run([sys.executable, "-m", "dbt_assay.cli", c["name"], "--help"],
                           capture_output=True, text=True, timeout=120, check=False)
        if r.returncode != 0:
            bad.append(c["name"])
    assert not bad, f"not runnable as a module: {bad}"
