"""Add a description or a test to a model's entry in its schema yml, by editing lines.

*** A SECOND ENTRY FOR THE SAME MODEL IS A dbt ERROR, AND THE FILE IS THEIRS. *** (patch.py
measured it: 344 of 358 models already have an entry.) So a change goes INTO the entry the model
has: under `- name: <model>`, under `columns:`, under `- name: <column>`. Every other line,
comment and blank line stays as it was, the way configpatch.py edits audit.yml. A model with no
entry gets one in a new file beside its SQL, which collides with nothing.

It refuses rather than guesses: an entry it cannot place is reported, not approximated.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_NAME = re.compile(r"^(\s*)-\s+name:\s*['\"]?([^'\"#\s]+)['\"]?\s*(#.*)?$")


@dataclass
class Edit:
    column: str = ""              # "" for the model's own description
    description: str = ""
    tests: list = field(default_factory=list)   # e.g. ["unique", "not_null"]


def _indent(s: str) -> int:
    return len(s) - len(s.lstrip())


def _block_end(lines: list, start: int, indent: int) -> int:
    """The first line after `start` at an indent <= indent that is not blank or a comment."""
    i = start + 1
    while i < len(lines):
        ln = lines[i]
        if ln.strip() and not ln.lstrip().startswith("#") and _indent(ln) <= indent:
            return i
        i += 1
    return i


def _find_item(lines, name, start, end, indent=None) -> tuple[int, int] | None:
    """(line, indent) of `- name: <name>` between start and end."""
    for i in range(start, end):
        m = _NAME.match(lines[i])
        if m and m.group(2).lower() == name.lower() and (indent is None
                                                          or len(m.group(1)) == indent):
            return i, len(m.group(1))
    return None


def _yaml_str(s: str) -> str:
    s = s.replace("\n", " ").strip()
    if not s or any(c in s for c in ":#'\"{}[],&*!|>%@`") or s[0] in "-?":
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def apply(text: str, model: str, edits: list[Edit]) -> tuple[str, list[str]]:
    """(new text, [refusals]). Adds only what is missing; an existing description is kept."""
    lines = text.split("\n")
    refused: list[str] = []
    top = next((i for i, ln in enumerate(lines) if re.match(r"^models:\s*(#.*)?$", ln)), None)
    if top is None:
        return text, [f"no `models:` section in the file for {model}"]
    end = _block_end(lines, top, 0)
    hit = _find_item(lines, model, top + 1, end)
    if hit is None:
        return text, [f"`{model}` has no entry in this file"]
    at, ind = hit
    item_end = _block_end(lines, at, ind)
    key_ind = ind + 2
    for e in edits:
        if not e.column:
            if not any(re.match(rf"^\s{{{key_ind}}}description:", lines[j])
                       for j in range(at + 1, item_end)):
                if e.description:
                    lines.insert(at + 1, " " * key_ind + f"description: {_yaml_str(e.description)}")
                    item_end += 1
            continue
        cols = next((j for j in range(at + 1, item_end)
                     if re.match(rf"^\s{{{key_ind}}}columns:\s*(#.*)?$", lines[j])), None)
        if cols is None:
            lines.insert(item_end, " " * key_ind + "columns:")
            cols = item_end
            item_end += 1
        cols_end = _block_end(lines, cols, key_ind)
        c = _find_item(lines, e.column, cols + 1, cols_end)
        if c is None:
            new = [" " * (key_ind + 2) + f"- name: {e.column}"]
            if e.description:
                new.append(" " * (key_ind + 4) + f"description: {_yaml_str(e.description)}")
            if e.tests:
                new.append(" " * (key_ind + 4) + "data_tests:")
                new += [" " * (key_ind + 6) + f"- {t}" for t in e.tests]
            lines[cols_end:cols_end] = new
            item_end += len(new)
            continue
        c_at, c_ind = c
        c_end = _block_end(lines, c_at, c_ind)
        body = range(c_at + 1, c_end)
        if e.description and not any(re.match(rf"^\s{{{c_ind + 2}}}description:", lines[j])
                                     for j in body):
            lines.insert(c_at + 1, " " * (c_ind + 2) + f"description: {_yaml_str(e.description)}")
            c_end += 1
            item_end += 1
        if e.tests:
            tk = next((j for j in range(c_at + 1, c_end)
                       if re.match(rf"^\s{{{c_ind + 2}}}(data_)?tests:\s*(#.*)?$", lines[j])), None)
            if tk is None:
                block = [" " * (c_ind + 2) + "data_tests:"] + \
                    [" " * (c_ind + 4) + f"- {t}" for t in e.tests]
                lines[c_end:c_end] = block
                item_end += len(block)
            else:
                t_end = _block_end(lines, tk, c_ind + 2)
                have = {re.sub(r"^\s*-\s*", "", lines[j]).split(":")[0].strip()
                        for j in range(tk + 1, t_end) if lines[j].strip().startswith("-")}
                add = [t for t in e.tests if t.split(":")[0].strip() not in have]
                ti = _indent(lines[tk + 1]) if tk + 1 < t_end and lines[tk + 1].strip() \
                    else c_ind + 4
                lines[t_end:t_end] = [" " * ti + f"- {t}" for t in add]
                item_end += len(add)
    return "\n".join(lines), refused


def new_entry(model: str, edits: list[Edit]) -> str:
    """A whole yml file for a model that has no entry anywhere."""
    out = ["version: 2", "", "models:", f"  - name: {model}"]
    own = next((e for e in edits if not e.column and e.description), None)
    if own:
        out.append(f"    description: {_yaml_str(own.description)}")
    cols = [e for e in edits if e.column]
    if cols:
        out.append("    columns:")
        for e in cols:
            out.append(f"      - name: {e.column}")
            if e.description:
                out.append(f"        description: {_yaml_str(e.description)}")
            if e.tests:
                out.append("        data_tests:")
                out += [f"          - {t}" for t in e.tests]
    return "\n".join(out) + "\n"
