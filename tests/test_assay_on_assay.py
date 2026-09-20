"""assay's own rules, applied to assay.

*** EVERY ONE OF THESE WAS A REAL DEFECT IN THIS REPOSITORY FIRST. ***
The tool exists to find prose that stopped matching code, checks that pass over nothing, and
instructions that name something which does not exist. It kept producing all three itself, and
each was found by a person reading output rather than by anything here.

These are the ones that can be checked mechanically. They are not a substitute for reading.
"""
import ast
import inspect
import re
from pathlib import Path

import pytest

import dbt_assay

SRC = Path(dbt_assay.__file__).parent
UNIVERSAL = re.compile(r"\b(every|all|always|never)\b", re.IGNORECASE)


def _modules():
    for f in sorted(SRC.rglob("*.py")):
        if f.name != "__init__.py":
            yield f


def test_no_console_print_puts_a_bracketed_word_where_rich_will_eat_it():
    """*** rich READ `[mcp]` AS A STYLE TAG AND DROPPED IT. ***

    The message telling someone to install `dbt-assay[mcp]` printed `dbt-assay`. An instruction
    broken by the defect it was written to fix. Anything with a bracketed non-style word must pass
    `markup=False` or escape it.
    """
    known = {"bold", "dim", "red", "green", "yellow", "cyan", "blue", "magenta", "white",
             "bold red", "bold cyan", "bold green", "bold yellow", "/", "on", "reverse"}
    bad = []
    for f in _modules():
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if "console.print" not in line or "markup=False" in line:
                continue
            # A rich tag opens after whitespace, a quote or a brace. `models[uid]` is Python
            # SUBSCRIPTING inside an f-string, evaluated before rich ever sees the string, and
            # matching it made this guard fire on three correct lines. A guard that matches too
            # much is the mirror of one that matches nothing.
            for m in re.finditer(r"(?:^|[\s\"'{(])\[([a-z_][a-z0-9_.\-]*)\]", line):
                tag = m.group(1)
                if tag in known or tag.startswith("/"):
                    continue
                bad.append(f"{f.name}:{i} [{tag}]")
    assert not bad, bad


def test_every_scanner_asserts_it_found_something():
    """*** A CHECK THAT LOOKS AT NOTHING REPORTS A CLEAN PROJECT. ***

    Found seven times in this repository: the dialect default, the decision keys, the docs reader
    that found zero commands, `regress` replaying nothing, the evaluator's unbuilt tables, the
    uncounted grain, and an empty table passing a uniqueness test. Any function that walks a
    collection and reports on it must assert the collection was not empty.
    """
    from dbt_assay import lint, patch, practices, rows
    for fn in (rows.which_have_failures, practices.verify_grains, patch.plan,
               lint.judge_overlap):
        src = inspect.getsource(fn)
        assert ("unknown" in src or "NOT COUNTED" in src or "absent" in src
                or "could not" in src or "EMPTY" in src), (
            f"{fn.__name__} must distinguish 'found nothing' from 'looked at nothing'")


def test_no_docstring_claims_something_the_function_plainly_does_not_do():
    """The `load_all_banks` docstring said "A user's own bank loads the same way" while nothing
    could pass it a directory. A universal claim naming a symbol that is absent from the function
    is the cheapest version of that defect to catch."""
    bad = []
    for f in _modules():
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            doc = ast.get_docstring(node) or ""
            if not UNIVERSAL.search(doc):
                continue
            body = ast.unparse(node)
            for sym in re.findall(r"`([a-z_][a-z0-9_]{3,})`", doc):
                if sym in ("assay", "dbt", "none", "true", "false", "null"):
                    continue
                if sym not in body and sym not in doc.split("***")[0]:
                    bad.append(f"{f.name}::{node.name} claims `{sym}`, which is not in it")
    assert not bad, bad


def test_the_package_has_no_todo_left_where_a_user_will_read_it():
    """A TODO in a docstring is a promise to a reader that nobody is tracking."""
    bad = []
    for f in _modules():
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if re.search(r"\b(TODO|FIXME|XXX|HACK)\b", line) and not line.strip().startswith("#"):
                bad.append(f"{f.name}:{i}")
    assert not bad, bad


@pytest.mark.parametrize("doc", ["README.md", "docs/OVERVIEW.md"])
def test_no_document_names_a_version_that_is_not_this_one(doc):
    root = SRC.parent.parent
    p = root / doc
    if not p.exists():
        pytest.skip("installed as a wheel")
    named = set(re.findall(r"dbt-assay@v(\d+\.\d+\.\d+)", p.read_text()))
    assert named <= {dbt_assay.__version__}, (doc, named, dbt_assay.__version__)
