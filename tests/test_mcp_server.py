import json

from dbt_assay.mcp_server import TOOLS, Backend


def test_the_backend_answers_without_any_mcp_dependency(project_dir):
    """The protocol layer is an optional extra; the answers are not."""
    be = Backend(str(project_dir))
    c = be.contract("int_bad_unique")
    assert c["grain"] == ["section_id"]
    assert be.contract("nope")["error"]


def test_blast_radius_names_the_consumers(project_dir):
    be = Backend(str(project_dir))
    b = be.blast_radius("stg_bad_notnull")
    assert b["descendants"] == 1 and "int_bad_unique" in b["consumers"]


def test_lineage_stops_and_says_so_at_a_source(project_dir):
    be = Backend(str(project_dir))
    out = be.lineage("stg_bad_notnull", "amount")
    assert out["hops"]
    assert out["hops"][-1]["what_happened"] in ("defaulted", "from_source", "carried")


def test_changed_contracts_reports_nothing_before_anything_changes(project_dir):
    """The baseline is taken on the first load, so the first call correctly says nothing moved."""
    be = Backend(str(project_dir))
    first = be.changed_contracts()
    assert first["changes"] == []
    assert first["summary"] == "Nothing means anything different."
    assert first["still_typing"] == []


def test_rebase_resets_the_baseline(project_dir):
    be = Backend(str(project_dir))
    be.changed_contracts()
    assert be.rebase()["ok"] is True


def test_every_tool_is_described_for_an_agent_not_for_a_programmer():
    assert len(TOOLS) == 7
    for name, desc in TOOLS:
        assert name and len(desc) > 30
    assert any("self-check" in d for _n, d in TOOLS)


def test_answers_are_json_serialisable(project_dir):
    be = Backend(str(project_dir))
    for payload in (be.contract("int_bad_unique"), be.findings(), be.changed_contracts()):
        json.dumps(payload, default=str)


def test_both_sdk_majors_are_tried_before_giving_up():
    """The SDK renamed FastMCP to MCPServer at v2. Importing one spelling means the command dies
    on whichever major the user happens to have, with a traceback instead of an explanation."""
    import inspect

    from dbt_assay import mcp_server
    src = inspect.getsource(mcp_server.serve)
    assert "mcp.server.mcpserver" in src and "mcp.server.fastmcp" in src
    assert "pip install mcp" in src
