"""*** THE PAGE SHIPPED AS 81 KB OF SYNTACTICALLY INVALID JAVASCRIPT. ***

One eaten quote -- `'...No release moves it.'check finds more...'` -- and the ENTIRE script failed
to parse. Every tab rendered, with correct counts, and did nothing when clicked, because the HTML
was fine and the JavaScript never ran.

Nothing in this repo noticed. The round-trip test passed: the file is deterministic, byte-identical
across runs, valid HTML, correct payload. The docs tests passed. 734 assertions passed. Every one
of them checks what the page CONTAINS and not one checks that it RUNS.

`node --check` finds it in about ten seconds, which is the whole lesson: the cheap check that
exercises the artifact beats any number of checks that inspect it.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from dbt_assay import explorer, reviewform

# Every script this project ships to a browser. A new one is covered by adding it here, and the
# test below fails if a name here stops existing.
SCRIPTS = {
    "explorer (assay page)": lambda: explorer._VIEWS,
    "review form (assay review --emit)": lambda: reviewform._JS,
}


def _node() -> str | None:
    return shutil.which("node")


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_the_shipped_script_parses(name):
    """*** A PAGE THAT CANNOT PARSE IS A PAGE THAT DOES NOTHING, AND IT LOOKS FINE. ***"""
    node = _node()
    if node is None:
        pytest.skip("node is not installed here; CI runs this")
    js = SCRIPTS[name]()
    assert js.strip(), f"{name} is empty"
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "s.js"
        f.write_text(js)
        p = subprocess.run([node, "--check", str(f)], capture_output=True, text=True, check=False)
    assert p.returncode == 0, (
        f"{name} is not valid JavaScript, so none of it runs and the page is static HTML that "
        f"looks correct:\n{p.stderr.strip()[:900]}")


def test_a_string_is_never_concatenated_by_eating_its_quote():
    """The exact shape of the break, in case `node` is ever missing.

    `'a sentence.'more text '` -- a closing quote followed immediately by a word. Valid nowhere,
    and it is what a careless edit to the page's copy produces.
    """
    import re
    for name, get in sorted(SCRIPTS.items()):
        bad = [m.group(0) for m in
               re.finditer(r"'[^'\n]{12,}[.:;,]'[A-Za-z]{2,}", get())]
        assert not bad, f"{name}: a string literal runs into an identifier: {bad[:3]}"


def test_the_script_list_names_things_that_exist():
    """A scanner pointed at a renamed attribute checks nothing and passes."""
    for name, get in sorted(SCRIPTS.items()):
        js = get()
        assert isinstance(js, str) and len(js) > 500, f"{name} is not a script any more"


def test_the_page_stamps_what_the_rendering_CODE_is_not_only_what_it_says_it_is():
    """*** `uvx` SERVED A CACHED 0.47.1 WHILE THE PROCESS REPORTED ITSELF AS 0.47.2. ***

    The page it produced was completely dead, and every number on it agreed with every other
    number because all of them came from the same wrong install. A version is what the package
    SAYS; the build hash is what the rendering code actually IS, so two pages claiming one
    version and differing here came from two different installs.

    Same argument `state_hash` already makes for a judged answer.
    """
    from dbt_assay import explorer

    first = explorer.build_fingerprint()
    assert len(first) == 12 and first == explorer.build_fingerprint(), "it is not deterministic"

    real = explorer._VIEWS
    try:
        explorer._VIEWS = real + "\n/* one more line */"
        assert explorer.build_fingerprint() != first, (
            "the rendering code changed and the stamp did not, so the stamp is not evidence")
    finally:
        explorer._VIEWS = real
    assert explorer.build_fingerprint() == first


def test_the_stamp_reaches_the_page():
    from dbt_assay import explorer

    data = {"meta": {"project": "p", "models": 1, "sources": 0, "version": "9.9.9",
                     "generated_at": "x", "coverage": {}},
            "models": [], "edges": [], "claims": [], "findings": [], "decisions": [],
            "questions": [], "adjudications": [], "config": {}, "runs": [], "unreadable": [],
            "unconfigured": [], "effectiveness": [], "moved": {}}
    doc = explorer.explorer_html(data, "<html></html>")
    assert f"build {explorer.build_fingerprint()}" in doc
    assert "assay 9.9.9" in doc
