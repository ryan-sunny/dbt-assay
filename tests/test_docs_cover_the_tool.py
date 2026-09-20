"""*** DOCS THAT DRIFT FROM THE CODE ARE THE DEFECT THIS TOOL EXISTS TO FIND. ***

Three things had already drifted before anyone checked once: fourteen of fifteen question families
were never named in either document, so a reader who saw `column_is_part_of_the_key` in
`assay config` had nowhere to look it up; the `rebase` MCP tool was absent from the skill file that
is supposed to be an agent's whole procedure; and three option lists in the overview named options
that do not exist.

This is the check assay would make of any other project, made of assay.
"""
import re
import subprocess
from pathlib import Path

import pytest

import dbt_assay

ROOT = Path(dbt_assay.__file__).parent.parent.parent


def _docs() -> str | None:
    r, o = ROOT / "README.md", ROOT / "docs" / "OVERVIEW.md"
    if not r.exists():
        return None                                  # installed as a wheel
    return r.read_text() + o.read_text()


def test_every_question_family_is_named_in_the_docs():
    """`rests_on` and `assay config` both print these names. A name with no documentation is a
    dead end for whoever reads it."""
    from dbt_assay.contracts import load_all_banks
    docs = _docs()
    if docs is None:
        pytest.skip("no docs in a wheel install")
    missing = [b for b in load_all_banks() if b not in docs]
    assert not missing, missing


def test_every_cli_command_is_named_in_the_docs():
    docs = _docs()
    if docs is None:
        pytest.skip("no docs in a wheel install")
    h = subprocess.run(["assay", "--help"], capture_output=True, text=True, check=False)
    if h.returncode != 0:
        pytest.skip("assay not on PATH in this environment")
    cmds = {m.group(1) for m in re.finditer(r"^\s*│ ([a-z][a-z0-9-]*)\s", h.stdout, re.MULTILINE)}
    assert len(cmds) > 20, "the help reader found almost nothing; it is broken"
    assert not [c for c in cmds if c not in docs]


def test_every_mcp_tool_is_in_the_skill_file():
    """The skill file IS the agent's procedure. A tool missing from it is a tool the agent will
    never call, however well it is implemented."""
    from dbt_assay.mcp_server import TOOLS
    from dbt_assay.skilltext import SKILL_MD
    assert TOOLS, "the tool list is empty; the reader is broken"
    assert not [n for n, _d in TOOLS if n not in SKILL_MD]


def test_the_families_with_no_finding_are_marked_as_such_where_they_are_listed():
    """Someone choosing what to rule on must be able to see which rows move a gate."""
    from dbt_assay.judged import FAMILIES_WITHOUT_FINDINGS
    p = ROOT / "docs" / "OVERVIEW.md"
    if not p.exists():
        pytest.skip("no docs in a wheel install")
    body = p.read_text()
    i = body.find("## Every question, and what rests on it")
    assert i >= 0, "the reference table is gone"
    table = body[i:body.find("\n## ", i + 10)]
    for fam in FAMILIES_WITHOUT_FINDINGS:
        row = next((ln for ln in table.splitlines() if f"`{fam}`" in ln), None)
        assert row, f"{fam} is not in the table"
        assert "—" in row or "-" in row.split("|")[-2], f"{fam} is not marked as feeding nothing"
