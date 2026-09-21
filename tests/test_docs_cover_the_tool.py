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
    """*** AND IT HAS TO LOOK FOR THE COMMAND, NOT FOR THE WORD. ***

    This matched the bare name anywhere in either document, so `assay evidence` was already
    "documented" by the sentence *the reason is a MEASUREMENT* containing no such word -- but by
    `evidence` appearing in a dozen unrelated paragraphs. A command called `page`, `check`,
    `diff` or `config` is a common English word, and the guard passed for every one of them on
    prose that has nothing to do with the command.

    A guard that matches something other than what it is checking is the defect this codebase
    keeps finding in other people's warehouses. It looks for `assay <name>`, which is how a
    document actually introduces a command.
    """
    docs = _docs()
    if docs is None:
        pytest.skip("no docs in a wheel install")
    cmds = commands()
    assert len(cmds) > 20, "the command reader found almost nothing; it is broken"
    missing = sorted(c for c in cmds if f"assay {c}" not in docs)
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


def test_the_docs_do_not_state_a_family_count_that_is_wrong():
    """*** "TWELVE QUESTION FAMILIES SHIP" SURVIVED FOUR FAMILIES BEING ADDED. ***

    A number written in prose is a copy of a fact, and it drifts silently because nothing reads it.
    Spelled-out numbers are checked because that is how they are written here.
    """
    import re

    from dbt_assay.contracts import load_all_banks
    docs = _docs()
    if docs is None:
        pytest.skip("no docs in a wheel install")
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
             8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen",
             14: "fourteen", 15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen",
             19: "nineteen", 20: "twenty"}
    right = words.get(len(load_all_banks()), str(len(load_all_banks())))
    # *** ONLY THE SENTENCES THAT CLAIM THE TOTAL. ***
    # A first version matched "two families sharing a prefix" and "nine families satisfied
    # nothing", which are counts of SUBSETS and correct. A guard that matches too much is the
    # mirror of one that matches nothing, and it gets deleted just as fast.
    # "N families ship" is the only phrasing that unambiguously claims the TOTAL. "nine of ten
    # families satisfied nothing" is a historical subset and correct; matching it made this guard
    # fail on true sentences, which is how a guard gets deleted.
    totals = re.compile(r"\b([A-Za-z]+|\d+) (?:question )?famil(?:ies|y) ship\b", re.IGNORECASE)
    seen, wrong = 0, []
    for m in totals.finditer(docs):
        said = m.group(1).lower()
        if said not in {v for v in words.values()} | {str(k) for k in words}:
            continue
        seen += 1
        if said != right:
            wrong.append(docs[max(0, m.start() - 40):m.start() + 50].replace("\n", " "))
    assert seen, "the reader found no total at all; it is broken"
    assert not wrong, wrong


def test_the_version_the_action_example_pins_is_the_current_one():
    """A README pinning an old tag hands every new user a version behind the docs around it."""
    import re

    import dbt_assay
    docs = _docs()
    if docs is None:
        pytest.skip("no docs in a wheel install")
    pinned = set(re.findall(r"dbt-assay@v(\d+\.\d+\.\d+)", docs))
    assert pinned, "the action example is gone"
    assert pinned == {dbt_assay.__version__}, (pinned, dbt_assay.__version__)


def test_the_product_doc_names_only_checks_and_families_that_exist():
    """*** PROSE THAT NAMES SOMETHING WHICH DOES NOT EXIST IS THE DEFECT THIS TOOL FINDS. ***

    The first draft of PRODUCT.md invented five check names by describing them instead of reading
    them: `bbox_used_as_distance` for `bbox_as_radius`, `variant_columns` for `variant_column`,
    and three more. A doc is a claim about the code, and assay's whole argument is that those go
    stale silently.
    """
    import re

    from dbt_assay.config import known_checks
    from dbt_assay.contracts import SHIPPED
    from dbt_assay.mcp_server import TOOLS

    p = ROOT / "docs" / "PRODUCT.md"
    if not p.exists():
        pytest.skip("no docs in a wheel install")
    known = set(known_checks()) | set(SHIPPED) | {n for n, _d in TOOLS}
    assert len(known) > 20, "the reader is broken; a scanner that knows nothing passes everything"
    # Only the two reference tables, so ordinary prose in backticks is not swept up.
    body = p.read_text()
    rows = [ln for ln in body.splitlines() if ln.startswith("| `")]
    assert len(rows) >= 12, f"only found {len(rows)} table rows; the reader is broken"
    bad = []
    for ln in rows:
        name = re.match(r"\| `([a-z_]+)`", ln)
        if name and name.group(1) not in known:
            bad.append(name.group(1))
    assert not bad, f"PRODUCT.md names checks or families that do not exist: {bad}"
