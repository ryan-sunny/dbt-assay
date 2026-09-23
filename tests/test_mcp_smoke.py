"""Every semantic MCP tool, called once through the real server.

*** 17 TOOLS, 16 WORKING, AND THE DEAD ONE WAS DEAD UNCONDITIONALLY. ***
Reported from the field (25.22): `suggestions` raised on every project and nothing noticed,
because every MCP test called the Backend's methods or listed the tools and none called a tool
the way an agent does. This calls each one once, with the fewest arguments its schema requires,
on a project with a store that a real `check` wrote. No warehouse, no key, no spend.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from typer.testing import CliRunner

from dbt_assay import cli_tools
from dbt_assay.cli import app as cli_app

# The fewest arguments each required parameter needs to reach the tool's real work.
ARGS = {"model": "int_bad_unique", "column": "section_id", "verdict": "unclear",
        "why": "smoke test", "path": "no-such-handback.json", "job": "no-such-job"}


def _content_text(res) -> str:
    content = getattr(res, "content", None)
    if content is None and isinstance(res, tuple):
        content = res[0]
    return "".join(getattr(c, "text", "") for c in (content or []))


def test_every_semantic_tool_answers_without_raising(project_dir, tmp_path):
    pytest.importorskip("mcp")
    from dbt_assay.mcp_server import build_app
    store = tmp_path / "s.duckdb"
    r = CliRunner().invoke(cli_app, ["check", "-t", str(project_dir), "--store", str(store),
                                     "--config", str(tmp_path)])
    assert r.exit_code in (0, 1), r.output
    server = build_app(str(project_dir), str(store))
    generated = {cli_tools.tool_name(c["name"]) for c in cli_tools.commands()}
    tools = [t for t in asyncio.run(server.list_tools()) if t.name not in generated]
    assert len(tools) >= 17, [t.name for t in tools]
    broken = []
    for t in tools:
        schema = t.inputSchema if hasattr(t, "inputSchema") else t.input_schema
        need = {p: ARGS[p] for p in (schema.get("required") or [])}
        try:
            res = asyncio.run(server.call_tool(t.name, need))
        except Exception as e:                                   # noqa: BLE001
            broken.append(f"{t.name}: raised {type(e).__name__}: {e}")
            continue
        text = _content_text(res)
        if getattr(res, "isError", False) or getattr(res, "is_error", False):
            broken.append(f"{t.name}: {text[:200]}")
            continue
        if text.startswith("{"):
            body = json.loads(text)
            if "Traceback" in json.dumps(body):
                broken.append(f"{t.name}: returned a traceback")
    assert not broken, "\n".join(broken)
