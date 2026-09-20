"""Bump the version when the MEANING changed, and never when it did not.

*** EVERY "YOU MUST BUMP THE VERSION" CHECK EVER WRITTEN DIES THE SAME WAY. ***
It fires on whitespace. Somebody reformats a model, the build nags, and within a fortnight the rule
is switched off. assay is the one thing in the stack that can tell a renamed CTE from a grain
change, which is what makes this rule bearable: a reformat, a rewritten join and a tidied comment
owe nothing at all.

*** THE LEVEL COMES FROM WHAT THE CHANGE DOES TO A CONSUMER. ***
    major  the grain moved, a column left, a column's role changed, or its provenance changed in a
           way that alters nullability -- `from_source` becoming `defaulted` means NULLs silently
           became zeros, and every average downstream shifts
    minor  a column was added
    none   anything else
That mapping is a proposal to be tuned against a project's own history, not a law.

*** A BUMP IS A DECISION; AN INFERRED CONTRACT IS A GUESS. ***
Inferred contracts are written to their own file, never to schema.yml, because a stream of machine
edits is how people stop reading their own diffs. A version bump is different in kind -- a human or
an agent decided it -- so it belongs in schema.yml where dbt reads it. It is still printed by
default rather than written, because a YAML round-trip destroys the comments in these files.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAJOR_KINDS = {"grain", "grain_in_sql", "column_removed", "role", "provenance", "model_removed"}
MINOR_KINDS = {"column_added"}

LEVELS = {"none": 0, "minor": 1, "major": 2}


@dataclass
class VersionState:
    model: str
    before: int | None
    after: int | None
    required: str                 # none | minor | major
    reasons: list
    patch_path: str | None = None

    @property
    def bumped(self) -> bool:
        return (self.after or 0) > (self.before or 0)

    @property
    def owes(self) -> bool:
        return LEVELS[self.required] > 0 and not self.bumped

    @property
    def suggested(self) -> int:
        return (self.before or 0) + 1


def declared_version(project, uid: str) -> int | None:
    """`meta.version`, or dbt's native `version:` where a project uses it."""
    node = (project.raw.get("nodes", {}) or {}).get(uid) or {}
    native = node.get("version")
    if native is not None:
        try:
            return int(native)
        except (TypeError, ValueError):
            return None
    meta = (node.get("config") or {}).get("meta") or {}
    v = meta.get("version")
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def patch_file(project, uid: str) -> str | None:
    """The schema.yml that documents this model, from the manifest's own pointer."""
    pp = ((project.raw.get("nodes", {}) or {}).get(uid) or {}).get("patch_path")
    if not pp:
        return None
    return pp.split("://", 1)[-1]


def required_level(changes) -> tuple[str, list]:
    level, why = "none", []
    for c in changes:
        if c.kind in MAJOR_KINDS:
            level = "major"
            why.append(f"{c.kind}: {c.detail or c.column or ''}".strip().rstrip(":"))
        elif c.kind in MINOR_KINDS and level == "none":
            level = "minor"
            why.append(f"{c.kind}: {c.column}")
    return level, why


def assess(changes, project_before, project_after) -> list[VersionState]:
    """One row per model whose meaning moved."""
    by_model: dict = {}
    for c in changes:
        by_model.setdefault(c.model, []).append(c)

    uid_of_after = {m.name: u for u, m in project_after.models.items()}
    uid_of_before = {m.name: u for u, m in project_before.models.items()}

    out = []
    for name, cs in sorted(by_model.items()):
        level, why = required_level(cs)
        if level == "none":
            continue
        ua, ub = uid_of_after.get(name), uid_of_before.get(name)
        out.append(VersionState(
            model=name,
            before=declared_version(project_before, ub) if ub else None,
            after=declared_version(project_after, ua) if ua else None,
            required=level, reasons=why,
            patch_path=patch_file(project_after, ua) if ua else None,
        ))
    return out


# --------------------------------------------------------------- the version column

_VERSIONISH = re.compile(r"(^|_)v(ersion)?$|^version|_version$", re.IGNORECASE)
_LITERAL = re.compile(r"^\s*'?([0-9]+)'?\s*$")


def version_column(entry, digest) -> tuple[str, str] | None:
    """A CONSTANT column that looks like a version stamp, and its literal value.

    Provenance already classifies a literal in the select list as `constant`, so this costs
    nothing: a column is a version stamp if it is constant and named like one.
    """
    for c in entry.columns:
        if c.provenance.value != "constant" or not _VERSIONISH.search(c.name):
            continue
        expr = (digest.output_exprs.get(c.name, "") if digest else "").strip()
        m = _LITERAL.match(expr)
        return c.name, (m.group(1) if m else expr)
    return None


def column_drift(project, entries, digests) -> list[tuple]:
    """(model, column, stamped, declared) where the stamp and the declaration disagree.

    A model that says it is v3 and stamps its rows with 2 makes every row produced since the bump
    untraceable, which is the one job the column had.
    """
    out = []
    for e in entries:
        declared = declared_version(project, e.uid)
        if declared is None:
            continue
        got = version_column(e, digests.get(e.uid))
        if not got:
            continue
        col, value = got
        if str(value) != str(declared):
            out.append((e.name, col, value, declared))
    return out


def unstamped_marts(project, entries, digests) -> list[str]:
    """Marts carrying no version stamp. Informational: it is a recommendation, not a defect."""
    return [e.name for e in entries
            if e.layer == "marts" and not e.unreadable
            and version_column(e, digests.get(e.uid)) is None]


def patch_text(state: VersionState) -> str:
    """The exact edit, for a human or an agent to apply. No YAML round-trip, no lost comments."""
    where = state.patch_path or "the schema.yml documenting it"
    return (f"in {where}, under `- name: {state.model}`:\n"
            f"    meta:\n"
            f"      version: {state.suggested}")


def write_bump(path: str, model: str, version: int) -> bool:
    """A TARGETED TEXT EDIT, never a YAML round-trip.

    These files carry a great deal of hand-written commentary, and `yaml.safe_dump` would silently
    discard every line of it. So the model's block is located, and only the version line is written
    or inserted.
    """
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return False
    lines = p.read_text().split("\n")
    start = None
    indent = ""
    for i, ln in enumerate(lines):
        m = re.match(r"^(\s*)-\s+name:\s*['\"]?" + re.escape(model) + r"['\"]?\s*$", ln)
        if m:
            start, indent = i, m.group(1) + "  "
            break
    if start is None:
        return False

    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^\s*-\s+name:", lines[i]) and not lines[i].startswith(indent + " "):
            end = i
            break

    block = lines[start:end]
    for i, ln in enumerate(block):
        if re.match(r"^\s*version:\s*\d+\s*$", ln):
            block[i] = re.sub(r"version:\s*\d+", f"version: {version}", ln)
            lines[start:end] = block
            p.write_text("\n".join(lines))
            return True
    for i, ln in enumerate(block):
        if re.match(rf"^{re.escape(indent)}meta:\s*$", ln):
            block.insert(i + 1, f"{indent}  version: {version}")
            lines[start:end] = block
            p.write_text("\n".join(lines))
            return True
    block.insert(1, f"{indent}meta:")
    block.insert(2, f"{indent}  version: {version}")
    lines[start:end] = block
    p.write_text("\n".join(lines))
    return True
