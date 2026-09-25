"""SQL that reads the warehouse from outside dbt: which files, which relations, and what dbt does
not know about.

*** THE PAID REPORT LAYER WAS PAST THE EDGE OF WHAT assay COULD SEE. *** (sunny-data, 2026-09-25:
101 warehouse tables queried from Python in 17 files.) A source only Python reads looked like a
source nothing reads, a model feeding a paid report through Python looked internal, and a table
the Python reads that dbt never built or declared was invisible.

`outside_dbt: {paths: [product/, delivery/], paid: [product/reports/]}` in audit.yml names where
that code lives. assay reads the string literals in those files that parse as SQL, and records
which relations each file reads. Each file then counts as a reader, the way an exposure does: a
source it reads is read, what it reads reaches it, and a path under `paid` is customer-facing.
A relation it reads that is neither a model nor a declared source is a finding. What the query is
FOR is a person's answer (or an agent's), never assay's guess.

Read-only and offline: the files are parsed, never run.
"""
from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp

from .checks.structural import Finding

POLICY: dict = {}
_BASE: list = [Path(".")]
_CACHE: dict = {}


def set_policy(block: dict | None, base: Path | None = None) -> None:
    POLICY.clear()
    POLICY.update(block or {})
    _BASE[0] = Path(base or ".")


@dataclass
class Reader:
    file: str
    relations: set = field(default_factory=set)       # lower-case names as written
    queries: int = 0
    paid: bool = False


def _strings(tree) -> list[str]:
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append(n.value)
        elif isinstance(n, ast.JoinedStr):
            # an f-string: its literal parts, with each {...} as a placeholder name
            out.append("".join(v.value if isinstance(v, ast.Constant) else "_param"
                               for v in n.values))
    return out


_MARKS = re.compile(r"\*|\(|\n|\bjoin\b|\bwhere\b|\bgroup\s+by\b|\border\s+by\b|"
                    r"\bfrom\s+[\w\"`]+\.[\w\"`]+", re.IGNORECASE)


def _looks_like_sql(s: str) -> bool:
    """A SELECT with something prose does not have: `*`, a call, a join or where, a qualified
    name, a line break. "select a font from the menu" parses as SQL and is not."""
    low = s.lower()
    if not ((re.search(r"\bselect\b", low) and re.search(r"\bfrom\b", low))
            or low.lstrip().startswith("with ")):
        return False
    return bool(_MARKS.search(s))


def scan(dialect: str = "duckdb") -> list[Reader]:
    paths = POLICY.get("paths") or []
    if not paths:
        return []
    base = _BASE[0]
    paid = [str(p).rstrip("/") for p in (POLICY.get("paid") or [])]
    key = (str(base.resolve()), tuple(paths), tuple(paid), dialect)
    if key in _CACHE:
        return _CACHE[key]
    out = []
    files: list[Path] = []
    for p in paths:
        root = (base / p)
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            files += sorted(x for x in root.rglob("*") if x.suffix in (".py", ".sql")
                            and ".venv" not in x.parts and "__pycache__" not in x.parts)
    for f in files:
        try:
            text = f.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        rel = os.path.relpath(f, base)
        strings = [text] if f.suffix == ".sql" else []
        if f.suffix == ".py":
            try:
                strings = [s for s in _strings(ast.parse(text)) if _looks_like_sql(s)]
            except SyntaxError:
                continue
        r = Reader(rel, paid=any(rel == x or rel.startswith(x + "/") for x in paid))
        for s in strings:
            try:
                trees = sqlglot.parse(s, read=dialect)
            except Exception:                                    # noqa: BLE001, S112
                continue          # a string that is not SQL after all
            for t in trees:
                if t is None:
                    continue
                ctes = {c.alias_or_name.lower() for c in t.find_all(exp.CTE)}
                found = {".".join(x for x in (tb.db, tb.name) if x).lower()
                         for tb in t.find_all(exp.Table) if tb.name}
                found = {x for x in found if x.split(".")[-1] not in ctes and x != "_param"}
                if found:
                    r.queries += 1
                    r.relations |= found
        if r.relations:
            out.append(r)
    _CACHE[key] = out
    return out


def _resolve(project, name: str) -> str | None:
    """A relation name as the Python wrote it, to a model or source uid."""
    parts = name.split(".")
    table = parts[-1]
    schema = parts[-2] if len(parts) > 1 else ""
    for uid, m in project.models.items():
        if m.name.lower() == table and not getattr(m, "is_installed_package", False):
            return uid
    for uid, s in project.sources.items():
        ident = (getattr(s, "identifier", "") or s.name).lower()
        if ident == table and (not schema or schema == (s.schema or "").lower()
                               or schema == (s.source_name or "").lower()):
            return uid
    return None


def apply(project) -> None:
    """Each outside reader becomes an exposure of the relations it reads (once per project)."""
    if getattr(project, "_outside_applied", False):
        return
    project._outside_applied = True
    readers = scan(getattr(project, "dialect", "duckdb") or "duckdb")
    project.outside_readers = {}
    project.outside_unknown = {}
    if not readers:
        return
    from .manifest import Exposure
    for r in readers:
        uids = []
        for name in sorted(r.relations):
            uid = _resolve(project, name)
            if uid is None:
                project.outside_unknown.setdefault(name, []).append(r.file)
                continue
            uids.append(uid)
            project.outside_readers.setdefault(uid, []).append(r.file)
        if not uids:
            continue
        e = Exposure(unique_id=f"outside.{r.file}", name=r.file, label=r.file,
                     type="outside_dbt", owner="", url="", maturity="", depends_on=uids,
                     path=r.file, meta={"paid": r.paid, "outside_dbt": True})
        project.add_exposure(e)


def findings(project) -> list[Finding]:
    """One finding per relation the outside code reads that dbt neither builds nor declares, and
    one per file, so somebody says what the queries are for."""
    apply(project)
    out = []
    for name, files in sorted((getattr(project, "outside_unknown", None) or {}).items()):
        out.append(Finding(
            check="read_outside_dbt_undeclared", subject="", subject_name=name,
            file=files[0],
            summary=f"`{name}` is read outside dbt and dbt neither builds nor declares it",
            detail=("Code outside dbt queries this relation, and nothing in the project says "
                    "what it is: no model builds it and no source declares it. Its freshness, "
                    "its tests and its lineage are unknown to anything dbt runs. Declare it as a "
                    "source, or build it as a model."),
            base=2, evidence={"relation": name, "read_by": sorted(set(files))[:10]}))
    for r in scan(getattr(project, "dialect", "duckdb") or "duckdb"):
        out.append(Finding(
            check="sql_outside_dbt", subject="", subject_name=r.file, file=r.file,
            summary=(f"{r.queries} quer{'y' if r.queries == 1 else 'ies'} in `{r.file}` read "
                     f"{len(r.relations)} relation(s) outside dbt"),
            detail=("This SQL runs outside dbt, so none of the checks, tests or lineage here "
                    "cover it. Say what it is for: a report that should read a mart, logic that "
                    "belongs in a model, or a one-off. Logic that belongs in a model should move "
                    "there, where it is tested and seen."),
            base=1 if not r.paid else 2,
            evidence={"relations": sorted(r.relations)[:20], "queries": r.queries,
                      "paid": r.paid}))
    return out
