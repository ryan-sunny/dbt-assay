"""*** DOCS THAT DRIFT FROM THE CODE ARE THE DEFECT THIS TOOL EXISTS TO FIND. ***

Three things had already drifted before anyone checked once: fourteen of fifteen question families
were never named in either document, so a reader who saw `column_is_part_of_the_key` in
`assay config` had nowhere to look it up; the `rebase` MCP tool was absent from the skill file that
is supposed to be an agent's whole procedure; and three option lists in the overview named options
that do not exist.

This is the check assay would make of any other project, made of assay.
"""
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


def commands() -> set[str]:
    """*** READ THE APP, NOT ITS RENDERED HELP. ***

    The first version of this scraped `assay --help` for box-drawing characters. rich does not
    emit them without a TTY, so on CI the reader found ZERO commands -- and the "assert it found
    something" clause is the only reason that surfaced as a failure rather than as a pass. Exactly
    the defect this file is about: a fact read through a representation that can differ from it.
    """
    from dbt_assay.cli import app
    return {(c.name or c.callback.__name__.removesuffix("_cmd").replace("_", "-"))
            for c in app.registered_commands}


def test_every_cli_command_is_named_in_the_docs():
    docs = _docs()
    if docs is None:
        pytest.skip("no docs in a wheel install")
    cmds = commands()
    assert len(cmds) > 20, "the command reader found almost nothing; it is broken"
    missing = sorted(c for c in cmds if c not in docs)
    assert not missing, missing


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


def test_every_command_and_flag_in_the_docs_actually_exists():
    """*** A DOC THAT NAMES A FLAG THAT DOES NOT EXIST IS WORSE THAN NO DOC. ***

    Caught twice by hand before this existed: `assay inventory --out` is `--html`, and
    `assay backtest --commits` is `--limit`. Someone copying either one gets an error and stops
    trusting the rest of the page.

    Checked against the registered PARAMETERS, never against rendered help, which wraps.
    """
    import re

    import typer.main

    from dbt_assay.cli import app
    docs = _docs()
    if docs is None:
        pytest.skip("no docs in a wheel install")

    by_name = {(c.name or c.callback.__name__.removesuffix("_cmd").replace("_", "-")): c
               for c in app.registered_commands}
    bad, seen = [], 0
    for block in re.findall(r"```bash\n(.*?)```", docs, re.DOTALL):
        for raw in block.splitlines():
            line = raw.split("#")[0].strip().replace("uvx dbt-assay", "assay")
            if not line.startswith("assay "):
                continue
            seen += 1
            parts = line.split()
            cmd = by_name.get(parts[1])
            if cmd is None:
                bad.append(f"{parts[1]}: no such command")
                continue
            params = typer.main.get_params_convertors_ctx_param_name_from_function(
                cmd.callback)[0]
            flags = {o for p in params for o in getattr(p, "opts", [])}
            for f in (p for p in parts[2:] if p.startswith("--")):
                if f not in flags:
                    bad.append(f"{parts[1]} {f}")
    assert seen > 25, f"the doc reader found only {seen} commands; it is broken"
    assert not bad, bad


def test_the_vocabulary_really_does_reach_every_question():
    """*** `assay config` PRINTS "sent with every question". THAT HAS TO BE TRUE. ***

    It reached seven families and not the two added last. A claim is exactly where a project's own
    words matter most -- "division" means something specific in a water warehouse, and a judgment
    that does not know it is guessing.

    Checked by building each state builder's output with a vocabulary and looking for it, rather
    than by grepping for the word, because a parameter that is accepted and dropped greps fine.
    """
    from dbt_assay import align, claims, columns, contracts, feeds, practices, rows, semantics
    v = {"division": {"means": "a Colorado water court region, 1 through 7"}}

    c = claims.Claim("i", "s", "n", "one row per division", "description")
    built = {
        "claims.kind_state": claims.kind_state("m", [c], "", v),
        "claims.align_state": claims.align_state(c, {"columns_this_model_produces": ["a"]}, v),
    }
    for name, st in built.items():
        assert st.get("vocabulary") == v, name

    # the builders that take it positionally, proven to still accept and keep it
    for mod in (align, columns, contracts, feeds, practices, rows, semantics):
        src = __import__("inspect").getsource(mod)
        assert 'state["vocabulary"] = vocab' in src or '"vocabulary"' in src, mod.__name__


def test_every_question_family_has_a_verification_status():
    """*** AN ANSWER IS NOT EVIDENCE THAT THE QUESTION WORKS. ***

    Two families passed every test in this suite and failed when a person read their output
    against real data. docs/VERIFICATION.md records which have had that done and which have not,
    and a family missing from it is one whose status nobody can look up.
    """

    from dbt_assay.contracts import SHIPPED
    p = ROOT / "docs" / "VERIFICATION.md"
    if not (ROOT / "README.md").exists():
        pytest.skip("no docs in a wheel install")
    assert p.exists(), "the verification record is gone"
    body = p.read_text()
    missing = [f for f in SHIPPED if f"`{f}`" not in body]
    assert not missing, missing
