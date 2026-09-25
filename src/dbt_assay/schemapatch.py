"""Add a description or a test to a model's entry in its schema yml, by editing lines.

*** A SECOND ENTRY FOR THE SAME MODEL IS A dbt ERROR, AND THE FILE IS THEIRS. *** (patch.py
measured it: 344 of 358 models already have an entry.) So a change goes INTO the entry the model
has: under `- name: <model>`, under `columns:`, under `- name: <column>`. Every other line,
comment and blank line stays as it was, the way configpatch.py edits audit.yml. A model with no
entry gets one in a new file beside its SQL, which collides with nothing.

YAML allows a list's items at the same indent as its key (`models:` then `- name:` in column 0),
and a flow list for tests (`data_tests: [unique, not_null]`). Both are common, so both are read,
and new lines follow the indentation the file already uses.

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


def _content(ln: str) -> bool:
    return bool(ln.strip()) and not ln.lstrip().startswith("#")


def _key_end(lines: list, at: int) -> int:
    """The end of a `key:` block: deeper lines, and list items at the key's own indent."""
    k = _indent(lines[at])
    i = at + 1
    while i < len(lines):
        ln = lines[i]
        if _content(ln):
            ind = _indent(ln)
            if ind < k or (ind == k and not ln.lstrip().startswith("- ")):
                return i
        i += 1
    return i


def _item_end(lines: list, at: int) -> int:
    """The end of a `- name: x` item: everything deeper than its dash."""
    k = _indent(lines[at])
    i = at + 1
    while i < len(lines):
        if _content(lines[i]) and _indent(lines[i]) <= k:
            return i
        i += 1
    return i


def _before_blanks(lines: list, end: int, start: int) -> int:
    """Where to insert at the end of a block: before the blank lines that separate it from the
    next one, so a new item sits with its siblings."""
    while end - 1 > start and not lines[end - 1].strip():
        end -= 1
    return end


def _find_item(lines, name, start, end, indent=None) -> tuple[int, int] | None:
    """(line, indent) of `- name: <name>` between start and end."""
    for i in range(start, end):
        m = _NAME.match(lines[i])
        if m and m.group(2).lower() == name.lower() and (indent is None
                                                          or len(m.group(1)) == indent):
            return i, len(m.group(1))
    return None


def _find_key(lines, key, start, end, indent) -> int | None:
    for j in range(start, end):
        if re.match(rf"^\s{{{indent}}}{key}:(\s|$)", lines[j]):
            return j
    return None


def _yaml_str(s: str) -> str:
    s = s.replace("\n", " ").strip()
    if not s or any(c in s for c in ":#'\"{}[],&*!|>%@`") or s[0] in "-?":
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _add_tests(lines: list, key_at: int, tests: list) -> None:
    """Append tests to a `data_tests:` / `tests:` key, flow or block."""
    ln = lines[key_at]
    m = re.match(r"^(\s*)((?:data_)?tests):\s*\[(.*)\]\s*(#.*)?$", ln)
    if m:
        have = [x.strip() for x in m.group(3).split(",") if x.strip()]
        names = {h.split(":")[0].strip() for h in have}
        add = [t for t in tests if t.split(":")[0].strip() not in names]
        if add:
            lines[key_at] = f"{m.group(1)}{m.group(2)}: [{', '.join(have + add)}]" + \
                (f" {m.group(4)}" if m.group(4) else "")
        return
    end = _before_blanks(lines, _key_end(lines, key_at), key_at)
    have = {re.sub(r"^\s*-\s*", "", lines[j]).split(":")[0].strip()
            for j in range(key_at + 1, end) if lines[j].strip().startswith("-")}
    add = [t for t in tests if t.split(":")[0].strip() not in have]
    first = next((j for j in range(key_at + 1, end) if lines[j].strip().startswith("-")), None)
    ind = _indent(lines[first]) if first is not None else _indent(ln) + 2
    lines[end:end] = [" " * ind + f"- {t}" for t in add]


def apply(text: str, model: str, edits: list[Edit]) -> tuple[str, list[str]]:
    """(new text, [refusals]). Adds only what is missing; an existing description is kept."""
    lines = text.split("\n")
    top = next((i for i, ln in enumerate(lines) if re.match(r"^models:\s*(#.*)?$", ln)), None)
    if top is None:
        return text, [f"no `models:` section in the file for {model}"]
    hit = _find_item(lines, model, top + 1, _key_end(lines, top))
    if hit is None:
        return text, [f"`{model}` has no entry in this file"]
    at, ind = hit
    key_ind = ind + 2
    for e in edits:
        item_end = _item_end(lines, at)
        if not e.column:
            if e.description and _find_key(lines, "description", at + 1, item_end,
                                           key_ind) is None:
                lines.insert(at + 1, " " * key_ind + f"description: {_yaml_str(e.description)}")
            continue
        cols = _find_key(lines, "columns", at + 1, item_end, key_ind)
        if cols is None:
            at_end = _before_blanks(lines, item_end, at)
            lines.insert(at_end, " " * key_ind + "columns:")
            cols = at_end
        cols_end = _key_end(lines, cols)
        # the file's own style: items at the key's indent, or two deeper
        first = next((j for j in range(cols + 1, cols_end) if _NAME.match(lines[j])), None)
        c_ind = _indent(lines[first]) if first is not None else key_ind + 2
        c = _find_item(lines, e.column, cols + 1, cols_end, c_ind)
        if c is None:
            end = _before_blanks(lines, cols_end, cols)
            new = [" " * c_ind + f"- name: {e.column}"]
            if e.description:
                new.append(" " * (c_ind + 2) + f"description: {_yaml_str(e.description)}")
            if e.tests:
                new.append(" " * (c_ind + 2) + f"data_tests: [{', '.join(e.tests)}]")
            lines[end:end] = new
            continue
        c_at, c_ind = c
        c_end = _before_blanks(lines, _item_end(lines, c_at), c_at)
        if e.description and _find_key(lines, "description", c_at + 1, c_end,
                                       c_ind + 2) is None:
            lines.insert(c_at + 1, " " * (c_ind + 2) + f"description: {_yaml_str(e.description)}")
            c_end += 1
        if e.tests:
            tk = next((j for j in range(c_at + 1, c_end)
                       if re.match(rf"^\s{{{c_ind + 2}}}(data_)?tests:", lines[j])), None)
            if tk is None:
                lines.insert(c_end, " " * (c_ind + 2) + f"data_tests: [{', '.join(e.tests)}]")
            else:
                _add_tests(lines, tk, e.tests)
    return "\n".join(lines), []


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
                out.append(f"        data_tests: [{', '.join(e.tests)}]")
    return "\n".join(out) + "\n"
