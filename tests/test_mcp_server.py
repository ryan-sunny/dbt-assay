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
    # A floor, not an exact count: adding a tool should not break a test about DESCRIPTIONS.
    # The indices are checked separately, in test_field_reports.
    assert len(TOOLS) >= 10
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
    # the import moved into `server_class`, so the failure can be raised BEFORE stdio owns the
    # terminal -- a bare `uvx dbt-assay` skipped the extra and the client saw CONNECTION_CLOSED
    # while the explanation went nowhere.
    from dbt_assay.mcp_server import server_class
    src = inspect.getsource(server_class)
    assert "mcp.server.mcpserver" in src and "mcp.server.fastmcp" in src
    # the message names the EXTRA now, not the bare package: a bare `uvx dbt-assay`
    # installs no mcp and the old wording did not say so.
    assert "pip install" in src and "dbt-assay[mcp]" in src


def test_suggestions_answers_over_mcp(project_dir, tmp_path):
    """*** THE TOOL THAT SAYS WHAT TO WRITE IN audit.yml RAISED ON EVERY PROJECT. ***

    Reported from the field (25.22): `self.state().findings` on a dataclass that has never had the
    field. The CLI `assay suggest` worked, which is why nothing noticed.
    """
    from dbt_assay.store import Store
    Store(tmp_path / "s.duckdb").close()
    be = Backend(str(project_dir), str(tmp_path / "s.duckdb"))
    out = be.suggestions("", 5)
    assert "error" not in out and "suggestions" in out
