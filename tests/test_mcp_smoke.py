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


def _hold_lock(path):
    """A second PROCESS holding the store, as `assay verify` did in the field. DuckDB lets a
    second connection in the same process through, so an in-process lock proves nothing."""
    import subprocess
    import sys
    code = ("import duckdb,sys,time; c=duckdb.connect(sys.argv[1]); "
            "print('held', flush=True); time.sleep(60)")
    p = subprocess.Popen([sys.executable, "-c", code, str(path)],
                         stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held"
    return p


def test_a_locked_store_is_said_on_every_tool_and_no_tool_breaks(project_dir, tmp_path,
                                                                    monkeypatch):
    """*** A LOCKED STORE PRESENTED AS TWELVE BROKEN TOOLS. ***

    Reported from the field (25.20): with `assay verify` holding the store, twelve tools returned
    "Error executing tool <name>" and five worked -- the five that do not open the store. The
    cause was one store-open in `state()` with no handling, and a wrapper that dropped the text.
    """
    pytest.importorskip("mcp")
    from dbt_assay.mcp_server import build_app
    store = tmp_path / "s.duckdb"
    r = CliRunner().invoke(cli_app, ["check", "-t", str(project_dir), "--store", str(store),
                                     "--config", str(tmp_path)])
    assert r.exit_code in (0, 1), r.output
    monkeypatch.setenv("ASSAY_LOCK_TIMEOUT", "0")
    holder = _hold_lock(store)
    try:
        server = build_app(str(project_dir), str(store))
        for name, args in (("contract", {"model": "int_bad_unique"}),
                           ("findings", {}), ("plan", {}), ("suggestions", {})):
            res = asyncio.run(server.call_tool(name, args))
            assert not (getattr(res, "isError", False) or getattr(res, "is_error", False)), name
            body = json.loads(_content_text(res))
            said = body.get("store_locked") or body.get("store") or body.get("error") or ""
            assert "LOCKED" in str(said), (name, body)
        # The shape still comes from the manifest while the store is held.
        body = json.loads(_content_text(asyncio.run(
            server.call_tool("contract", {"model": "int_bad_unique"}))))
        assert body.get("grain") == ["section_id"], body
    finally:
        holder.kill()
        holder.wait()
    # And once the lock is gone, nothing was cached from the locked read.
    body = json.loads(_content_text(asyncio.run(
        server.call_tool("contract", {"model": "int_bad_unique"}))))
    assert "store_locked" not in body, body


def test_a_short_lock_is_waited_out_without_a_message(project_dir, tmp_path, monkeypatch):
    """A CLI command holds the store for seconds; the default wait absorbs it."""
    pytest.importorskip("mcp")
    import threading
    import time

    from dbt_assay.mcp_server import build_app
    store = tmp_path / "s.duckdb"
    CliRunner().invoke(cli_app, ["check", "-t", str(project_dir), "--store", str(store),
                                 "--config", str(tmp_path)])
    monkeypatch.delenv("ASSAY_LOCK_TIMEOUT", raising=False)
    holder = _hold_lock(store)
    threading.Thread(target=lambda: (time.sleep(1.0), holder.kill()), daemon=True).start()
    server = build_app(str(project_dir), str(store))
    body = json.loads(_content_text(asyncio.run(
        server.call_tool("contract", {"model": "int_bad_unique"}))))
    holder.wait()
    assert "store_locked" not in body and body.get("grain") == ["section_id"], body


def test_an_unexpected_failure_names_the_exception_not_just_the_tool(project_dir, monkeypatch):
    pytest.importorskip("mcp")
    from dbt_assay import mcp_server
    monkeypatch.setattr(mcp_server.Backend, "blast_radius",
                        lambda self, m: (_ for _ in ()).throw(KeyError("boom")))
    server = mcp_server.build_app(str(project_dir))
    body = json.loads(_content_text(asyncio.run(
        server.call_tool("blast_radius", {"model": "x"}))))
    assert "KeyError" in body["error"] and "boom" in body["error"] and body["raised_at"]


def test_no_tool_leaves_the_store_open(project_dir, tmp_path):
    """*** THE SERVER LOCKED ITSELF OUT OF ITS OWN STORE. ***

    Reported from the field: after `plan`, `suggestions` or `evidence`, the server held the write
    lock for as long as it ran, and every CLI-backed tool -- and the person's terminal -- was
    locked out by it.
    """
    pytest.importorskip("mcp")
    import subprocess
    import sys

    from dbt_assay.mcp_server import build_app
    store = tmp_path / "s.duckdb"
    CliRunner().invoke(cli_app, ["check", "-t", str(project_dir), "--store", str(store),
                                 "--config", str(tmp_path)])
    server = build_app(str(project_dir), str(store))
    for name, args in (("plan", {}), ("suggestions", {}), ("evidence", {}),
                       ("findings", {}), ("violations", {}), ("review_queue", {})):
        asyncio.run(server.call_tool(name, args))
        got = subprocess.run([sys.executable, "-c",
                              "import duckdb,sys; duckdb.connect(sys.argv[1]).close(); print('ok')",
                              str(store)], capture_output=True, text=True, check=False)
        assert got.stdout.strip() == "ok", f"`{name}` left the store locked: {got.stderr[-200:]}"


def test_a_lock_held_by_this_process_says_it_is_a_bug_not_a_wait():
    import os

    from dbt_assay.store import lock_message
    msg = lock_message("s.duckdb", f"Conflicting lock is held in python (PID {os.getpid()})")
    assert "bug in assay" in msg and "Wait for it" not in msg
