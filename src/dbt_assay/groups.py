"""One defect written in several places, found from the findings themselves.

*** NINE FINDINGS, ONE MACRO, AND NOTHING SAID THEY WERE ONE EDIT. *** (25.21, 25.23b)
`test_cannot_fail` fired on nine `stg_*_permits` models, and seven of them carry the SAME compiled
`CASE` -- it comes from `macros/permit_stg.sql`. `assay plan` listed nine rows with nine fix
shapes. `assay disagreements` already ships the principle for verdicts ("N rejections are usually
far fewer than N bugs"); this is the same collapse applied to findings, and it is free: the shape
is in the evidence and the macro is in the manifest.

*** A GROUP IS A READING AID, NEVER A THING TO RULE ON. ***
A verdict on a model already lands on every finding the model has, and that hazard is what
`assay-review` warns about. A verdict on a group would be the same hazard multiplied. So nothing
here is a finding, nothing here is ruled on, and every ruling stays on the finding it names. The
group only tells a reader that nine rows are one edit, and where the edit is.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Evidence that says WHERE or HOW MUCH, not WHAT was written. Two findings about one construct in
# two models differ in all of these, and a shape built from them would never match.
_NOT_SHAPE = frozenset({
    "column", "test", "missing", "missing_total", "columns", "layer", "ingestion", "path",
    "claim", "one_of_each", "confidence", "probability", "downstream", "marts", "store", "line",
    "says", "question", "partition_by", "described_in", "variants", "models", "count",
    # what KIND of test and how loud: metadata about the test, not the construct it tests
    "kind", "severity",
})

# Something a person WROTE: a literal, a call, an operator, a CASE. A shape that is only a column
# name masked to `<col>` is every `unique` test on a bare column, and grouping on it put eleven
# unrelated models in one "defect" on the field warehouse.
_WRITTEN = re.compile(r"'|\(|=|<>|<(?!col>)|>|\bcase\b|\bcoalesce\b|\+|\bover\b")


@dataclass
class Group:
    """One construct, the check it trips, and every finding that carries it."""
    key: str
    check: str
    shape: str
    findings: list = field(default_factory=list)
    macro: str = ""               # the project macro every member is built with, when one is
    macro_file: str = ""
    macro_lines: list = field(default_factory=list)

    @property
    def models(self) -> list[str]:
        return sorted({f.subject_name for f in self.findings})

    def as_dict(self) -> dict:
        where = (f"{self.macro_file}:{','.join(map(str, self.macro_lines))}"
                 if self.macro_lines else "")
        return {"group": self.key, "check": self.check, "size": len(self.models),
                "models": self.models, "findings": sorted(f.id for f in self.findings),
                "macro": self.macro, "macro_at": where, "shape": self.shape[:300]}


def shape_of(f) -> str:
    """The construct a finding is about, as written, with its own column name masked.

    Empty when the evidence names no construct: a finding about a count or a coverage gap has no
    shape to share, and grouping it on its location would group everything.
    """
    ev = {k: v for k, v in (f.evidence or {}).items()
          if k not in _NOT_SHAPE and not isinstance(v, float)}
    if not ev:
        return ""
    text = json.dumps(ev, sort_keys=True, default=str).lower()
    own = str((f.evidence or {}).get("column") or "").lower()
    if own:
        text = re.sub(rf"\b{re.escape(own)}\b", "<col>", text)
    text = re.sub(r"\s+", " ", text)
    return text if _WRITTEN.search(" ".join(_strings(ev)).lower()) else ""


def _strings(v) -> list[str]:
    """The strings inside evidence, flattened. A list's repr carries quote marks, and matching the
    repr grouped five models on `['_dlt_id']` as though somebody had written a literal."""
    if isinstance(v, dict):
        return [x for y in v.values() for x in _strings(y)]
    if isinstance(v, list | tuple | set):
        return [x for y in v for x in _strings(y)]
    return [str(v)]


def build(project, findings) -> list[Group]:
    """Groups of two or more models whose findings of one check share a shape, largest first."""
    by: dict = {}
    for f in findings:
        if not f.subject:
            continue
        s = shape_of(f)
        if not s:
            continue
        by.setdefault((f.check, s), []).append(f)
    out = []
    for (check, s), fs in by.items():
        if len({f.subject for f in fs}) < 2:
            continue
        key = hashlib.sha1(f"{check}|{s}".encode()).hexdigest()[:10]
        g = Group(key=key, check=check, shape=s, findings=sorted(fs, key=lambda x: x.id))
        _attribute(project, g)
        out.append(g)
    return sorted(out, key=lambda g: (-len(g.models), g.check, g.key))


def _attribute(project, g: Group) -> None:
    """The project macro every member is built with AND whose file carries the shape.

    Only the project's OWN macros, and only one every member depends on: a macro one member uses
    says nothing about the others. Depending on it is not enough -- two models on the field
    warehouse share `run_date` and write their CASE inline -- so the macro is credited only when
    its file carries the shape's most distinctive literal, and every line that does is named.
    """
    macros = (project.raw.get("macros") or {}) if project is not None else {}
    nodes = (project.raw.get("nodes") or {}) if project is not None else {}
    common = None
    for f in g.findings:
        deps = {m for m in ((nodes.get(f.subject) or {}).get("depends_on") or {}).get("macros", [])
                if (macros.get(m) or {}).get("package_name") == project.project_name}
        common = deps if common is None else common & deps
    if not common:
        return
    literals = sorted(re.findall(r"'((?:[^'\\]|\\.){6,})'", g.shape), key=len, reverse=True)
    root = getattr(project, "project_root", None)
    if not literals or not root:
        return
    for name in sorted(common):
        m = macros.get(name) or {}
        path = m.get("original_file_path", "")
        try:
            lines = (Path(root) / path).read_text(errors="replace").lower().splitlines()
        except OSError:
            continue
        for lit in literals:
            hits = [i for i, t in enumerate(lines, 1) if lit in t]
            if hits:
                g.macro, g.macro_file, g.macro_lines = m.get("name", name), path, hits
                return


def membership(groups: list[Group]) -> dict:
    """{finding id: the group it belongs to}, for surfaces that show one finding at a time."""
    return {f.id: g for g in groups for f in g.findings}
