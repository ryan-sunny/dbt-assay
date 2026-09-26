"""*** "ITS A BIG PRODUCT RN". *** (Ryan, before 0.54.0) The docs that describe the tool as it is
must say what it does now, and the check has to be mechanical, not a read-through.

The reference docs are the README and the docs that describe the tool as it is. The rest of docs/
are records (a build spec, a work order, a field report): each says so on its first line, and they
describe the tool as it was when they were written, which is what a record is for.
"""
import re
import shlex
from pathlib import Path

import pytest

import dbt_assay

ROOT = Path(dbt_assay.__file__).parent.parent.parent
REFERENCE = ["README.md", "docs/OVERVIEW.md", "docs/PRODUCT.md", "docs/SCHEMA.md",
             "docs/VERIFICATION.md"]


def _read(names) -> dict | None:
    if not (ROOT / "README.md").exists():
        return None                                  # installed as a wheel
    return {n: (ROOT / n).read_text() for n in names}


def _commands() -> dict:
    from dbt_assay.cli import app
    return {(c.name or c.callback.__name__.removesuffix("_cmd").replace("_", "-")): c
            for c in app.registered_commands}


def _flags(cmd) -> set:
    import typer.main
    params = typer.main.get_params_convertors_ctx_param_name_from_function(cmd.callback)[0]
    return {o for p in params for o in getattr(p, "opts", [])}


def test_every_doc_in_docs_is_reference_or_says_it_is_a_record():
    docs = _read([])
    if docs is None:
        pytest.skip("no docs in a wheel install")
    loose = []
    for p in sorted((ROOT / "docs").glob("*.md")):
        rel = f"docs/{p.name}"
        if rel in REFERENCE:
            continue
        head = "\n".join(p.read_text().splitlines()[:4])
        if "> A record" not in head:
            loose.append(rel)
    assert not loose, f"neither reference nor marked as a record: {loose}"


def test_every_assay_command_and_flag_the_reference_docs_name_exists():
    """In bash blocks AND inline: `assay review --load latest` in a sentence is copied as often as
    a line in a block."""
    docs = _read(REFERENCE)
    if docs is None:
        pytest.skip("no docs in a wheel install")
    cmds = _commands()
    bad, seen = [], 0
    for name, text in docs.items():
        spans = [ln for b in re.findall(r"```(?:bash|sh)\n(.*?)```", text, re.DOTALL)
                 for ln in b.splitlines()]
        spans += re.findall(r"`(assay [^`\n]+)`", text)
        for raw in spans:
            line = raw.split("#")[0].strip().replace("uvx dbt-assay", "assay")
            if not line.startswith("assay "):
                continue
            try:
                parts = shlex.split(line.rstrip("\\"))    # a quoted step is one token
            except ValueError:
                parts = line.split()
            if len(parts) < 2 or not re.fullmatch(r"[a-z][a-z-]*", parts[1]):
                continue
            seen += 1
            cmd = cmds.get(parts[1])
            if cmd is None:
                bad.append(f"{name}: assay {parts[1]}: no such command")
                continue
            flags = _flags(cmd)
            for f in (p.split("=")[0] for p in parts[2:] if p.startswith("--")):
                if f not in flags:
                    bad.append(f"{name}: assay {parts[1]} {f}")
    assert seen > 60, f"the reader found only {seen} commands; it is broken"
    assert not bad, bad


def test_every_command_is_in_the_reference_docs():
    docs = _read(REFERENCE)
    if docs is None:
        pytest.skip("no docs in a wheel install")
    text = "\n".join(docs.values())
    missing = sorted(c for c in _commands() if f"assay {c}" not in text)
    assert not missing, missing


def _views() -> set:
    """Every name the page and the form show a person: the four sections and Settings, the
    report's views, the form's tabs."""
    from dbt_assay import explorer, reviewform
    src = Path(explorer.__file__).read_text()
    tabs = src[src.index("    tabs = ["):]
    tabs = tabs[:tabs.index("\n    ]")]
    names = set(re.findall(r'\("[a-z_]+", "([^"]+)",', tabs))
    names |= {"Overview", "Fix", "Decide", "Explore", "Settings", "Judgment calls"}
    names |= set(re.findall(r'data-pane="[a-z]+"[^>]*>([^<]+)<', reviewform.form_html(
        [], {}, "p", "x", "0")))
    return names


def test_every_tab_or_section_the_reference_docs_name_is_on_the_page():
    """The page is four sections and Settings. A doc sending a reader to a tab that is gone (the
    thirteen tabs, "Understood") is a doc they stop trusting."""
    docs = _read(REFERENCE)
    if docs is None:
        pytest.skip("no docs in a wheel install")
    views = _views()
    assert {"Overview", "Findings", "Fixes", "Words"} <= views, views
    bad = []
    for name, text in docs.items():
        for m in re.finditer(r"\b(?:the|its|a) (?:\*\*)?([A-Z][A-Za-z]+(?: [a-z]+)?)(?:\*\*)? "
                             r"(tab|pane)\b", text):
            label = m.group(1)
            if label not in views and label.split(" ")[0] not in views:
                bad.append(f"{name}: {m.group(0)}")
    for gone in ("open the review form", "thirteen tabs", "13 tabs", "Understood tab"):
        for name, text in docs.items():
            if gone in text:
                bad.append(f"{name}: {gone}")
    assert not bad, bad


def test_the_readme_opens_on_what_a_person_does_with_it():
    """The first screen says what assay is and what somebody does with it now: the queue, the
    changes, the calls. Feature history comes later, if at all."""
    docs = _read(["README.md"])
    if docs is None:
        pytest.skip("no docs in a wheel install")
    first = "\n".join(docs["README.md"].splitlines()[:40]).lower()
    for word in ("broken now", "worth a look", "changes", "decide"):
        assert word in first, f"the README's first screen does not say {word!r}"
    assert "0.5" not in first.replace("0.54", ""), "version history on the first screen"
