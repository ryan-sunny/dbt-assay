"""What a stranger reads, and the two things that leaked into it.

*** assay's OWN RELEASE HISTORY WAS PRINTED INTO THE USER'S REPORT. ***
"the fix was structural (0.15.0, 0.21.1), not a third waiver" -- on a card about somebody else's
warehouse, where 0.15.0 names nothing the reader has ever seen.

*** AND ONE WAREHOUSE'S DOMAIN WAS THE WORKED EXAMPLE OF A GENERAL TOOL. ***
Colorado water law shipped inside the question bank, the linter and the review form, because that
is the warehouse this was developed against. It is a dbt tool; the example has to be one every
reader already has.

A comment explaining WHY a check exists is developer-facing and may cite anything. These scan what
goes on the screen.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import dbt_assay
from dbt_assay import explorer, lint, reviewform

PKG = Path(dbt_assay.__file__).parent

# A release number in prose: `0.15.0`, `assay.0.21.1`. Not a threshold (`0.75`) and not a date.
VERSIONS = re.compile(r"\b\d+\.\d+\.\d+\b")
# The domain that leaked. `water` also catches `water_source` and `water_rights`.
DOMAIN = re.compile(r"\bC\.?R\.?S\.?\s*\d|\bnontributary\b|\bconditional_right\b|\bwater[_ ]"
                    r"(?:right|source|division)s?\b|\bprior appropriation\b", re.IGNORECASE)


def _js_strings(js: str) -> list[str]:
    """Every string literal in a shipped script, with the comments removed.

    The comments are where this codebase keeps its evidence, and evidence cites versions. What
    reaches a browser is the literals.
    """
    without = re.sub(r"/\*.*?\*/", " ", js, flags=re.DOTALL)
    without = re.sub(r"^\s*//.*$", " ", without, flags=re.MULTILINE)
    out = re.findall(r"'((?:[^'\\\n]|\\.)*)'", without)
    out += re.findall(r'"((?:[^"\\\n]|\\.)*)"', without)
    return out


SCRIPTS = {"the report page": lambda: explorer._VIEWS,
           "the review form": lambda: reviewform._JS}


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_the_scanner_can_see(name):
    """*** A SCANNER THAT MATCHES NOTHING PASSES WRONGLY. ***
    Both tests below are only worth their green if the extraction actually found the prose."""
    strings = _js_strings(SCRIPTS[name]())
    assert len(strings) > 100, f"{name}: the literal extractor found almost nothing; it is broken"
    joined = " ".join(strings)
    assert "assay" in joined, f"{name}: no prose came back, so nothing was scanned"


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_no_shipped_screen_text_names_an_assay_release(name):
    hits = [s for s in _js_strings(SCRIPTS[name]()) if VERSIONS.search(s)]
    assert not hits, (
        f"{name} prints assay's own release history to somebody reading their warehouse:\n  "
        + "\n  ".join(hits[:6]))


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_no_shipped_screen_text_ships_one_warehouses_domain(name):
    hits = [s for s in _js_strings(SCRIPTS[name]()) if DOMAIN.search(s)]
    assert not hits, (
        f"{name} ships Colorado water law as a worked example of a general dbt tool:\n  "
        + "\n  ".join(hits[:6]))


def test_the_question_banks_ship_no_domain_of_their_own():
    """*** A BANK EXAMPLE IS SENT TO A MODEL, NOT JUST SHOWN. ***

    `examples:` steers the answer. One that names a statute from one state teaches every model in
    every project what that statute says, which is the failure `asserts_law_everywhere` exists to
    catch in the USER's vocabulary.
    """
    banks = sorted((PKG / "questions").glob("*.yml"))
    assert len(banks) > 3, "the bank reader found almost nothing; it is broken"
    bad = []
    for b in banks:
        for i, line in enumerate(b.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue                      # the evidence comments, which may cite anything
            if DOMAIN.search(line):
                bad.append(f"{b.name}:{i}: {line.strip()[:110]}")
    assert not bad, "the shipped question banks carry one warehouse's domain:\n  " + \
                    "\n  ".join(bad)


def test_a_suggestion_never_quotes_an_assay_version_at_the_reader():
    """`suggest` writes the prose on the Configure tab and in `assay suggest`. Its docstrings are
    evidence and may cite releases; the strings it hands a reader may not."""
    src = (PKG / "suggest.py").read_text()
    # Strip docstrings and comments -- the two places this codebase keeps its reasoning.
    body = re.sub(r'"""(?:.|\n)*?"""', " ", src)
    body = re.sub(r"^\s*#.*$", " ", body, flags=re.MULTILINE)
    hits = [s for s in re.findall(r'"((?:[^"\\\n]|\\.)*)"', body) if VERSIONS.search(s)]
    assert not hits, "a suggestion quotes assay's release history at the reader:\n  " + \
                     "\n  ".join(hits[:6])


def test_the_linter_still_catches_a_statute_in_a_users_own_vocabulary():
    """*** REMOVING THE EXAMPLE MUST NOT REMOVE THE CHECK. ***
    `C.R.S.` is a statute citation and a vocab term that carries one is asserted to every model
    in the project. The tool no longer USES water law as an example; it still finds it."""
    assert lint._STATUTE.search("per C.R.S. 37-92-302(1)(c)")
    assert lint._STATUTE.search("A.R.S. 45-101")
    assert not lint._STATUTE.search("one row per customer and order date")


def test_the_suggest_module_still_explains_the_choice_it_refuses_to_make():
    """The version numbers went; the point they were making did not."""
    src = (PKG / "suggest.py").read_text()
    assert "would a reader who knew this term still call the finding correct" in src.lower()


# ------------------------------------------------------------ the loop the form could not close

def test_load_handback_is_the_only_mcp_tool_that_can_file_a_human_verdict():
    """*** THE PERSON DID THE WORK AND THE FILE SAT IN ~/Downloads. ***

    `rule` files `agent` and says so at length, because an agent able to raise the ruled-on
    number would destroy the one figure nobody can game. `load_handback` files `human` -- and it
    can only file what the downloaded file carries, which is why it takes a PATH and has no
    parameter for a verdict. There is no shape of that call that invents an opinion.
    """
    import inspect

    from dbt_assay.mcp_server import TOOLS, Backend
    names = [n for n, _d in TOOLS]
    assert "load_handback" in names, "the form's output still reaches nothing from MCP"
    sig = inspect.signature(Backend.load_handback)
    # verdicts_only narrows what is written; nothing here can supply a verdict
    assert set(sig.parameters) - {"self"} == {"path", "apply", "by", "verdicts_only"}
    for bad in ("verdict", "agree", "disagree", "note"):
        assert bad not in sig.parameters, f"an agent could invent a {bad} through this tool"


def test_the_handback_loader_names_the_rows_that_recorded_nothing(tmp_path):
    """"recorded 40" and "you answered 40 of 212" have to be distinguishable from the outside."""
    import json

    from dbt_assay.mcp_server import Backend
    from dbt_assay.store import Store

    store_path = tmp_path / "s.duckdb"
    Store(str(store_path)).close()
    hb = tmp_path / "handback.json"
    hb.write_text(json.dumps({
        "by": "ryan",
        "verdicts": [
            {"subject": "model.p.orders", "question": "grain__is_it", "verdict": "agree",
             "note": "read it", "findings": ["abc"]},
            # A card somebody typed a note on and never ruled. It records NOTHING and is named.
            {"subject": "model.p.items", "question": "grain__is_it", "verdict": "",
             "note": "typed a note and never ruled"},
        ],
    }))
    be = Backend.__new__(Backend)
    be._store_or_why = lambda: (Store(str(store_path)), "")
    out = be.load_handback(str(hb), by="ryan")
    assert out["recorded"] == 1
    assert out["as"] == "human"
    assert out["findings_ruled"] == 1
    assert out["recorded_nothing_total"] == 1, out


def test_a_missing_handback_says_to_ask_rather_than_guessing(tmp_path):
    from dbt_assay.mcp_server import Backend
    be = Backend.__new__(Backend)
    out = be.load_handback(str(tmp_path / "nope.json"))
    assert out["recorded"] == 0
    assert "ask for the path" in out["error"]
