"""Read a dbt project from `manifest.json`. NO dbt-core dependency.

*** THIS READS JSON. IT DOES NOT IMPORT dbt. ***
dbt-core pins adapters, Python versions and transitive dependencies aggressively. Depending on it
means fighting every user's environment and breaking on every dbt release. The manifest is a stable
published artifact; reading it as data means assay works across dbt versions and adapters and can
never break somebody's dbt.

*** COMPILED SQL IS USUALLY NOT IN THE MANIFEST. ***
Measured against a real 357-model project: a manifest written by `dbt parse` or `docs generate`
carried `compiled_code` for ZERO nodes. The SQL lives in `target/compiled/<project>/<path>`. So the
file on disk is the primary source and `compiled_code` is the fallback, not the other way round.
A model with neither is reported as UNREADABLE and never silently skipped -- a scanner that cannot
see a model must not contribute to a passing result.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

LAYERS = ("staging", "intermediate", "marts")


def _layer_of(path: str) -> str:
    parts = Path(path).parts
    for p in parts:
        if p in LAYERS:
            return p
    return "other"


@dataclass(frozen=True)
class Test:
    unique_id: str
    name: str
    kind: str | None          # not_null | unique | accepted_values | relationships | <custom>
    column: str | None
    tests_model: str | None   # unique_id of the model under test
    kwargs: dict
    severity: str
    description: str


@dataclass
class Model:
    unique_id: str
    name: str
    path: str                 # original_file_path, for pointing a human at the right file
    layer: str
    schema: str
    materialized: str
    description: str
    columns: dict             # declared columns from schema.yml: name -> {description, meta, ...}
    meta: dict
    parents: list[str] = field(default_factory=list)
    children: list[str] = field(default_factory=list)
    compiled: str | None = None
    compiled_from: str = "none"   # disk | manifest | none

    @property
    def readable(self) -> bool:
        return bool(self.compiled)


@dataclass
class Source:
    unique_id: str
    name: str
    source_name: str
    schema: str
    description: str
    columns: dict
    children: list[str] = field(default_factory=list)


class Project:
    """A dbt project, read from its manifest and its compiled output."""

    def __init__(self, manifest: dict, target_dir: Path):
        self.raw = manifest
        self.target_dir = target_dir
        self.project_name = manifest.get("metadata", {}).get("project_name", "")
        self.dbt_version = manifest.get("metadata", {}).get("dbt_version", "")
        self.models: dict[str, Model] = {}
        self.sources: dict[str, Source] = {}
        self.tests: list[Test] = []
        self._build()

    # ---------- loading ----------

    @classmethod
    def load(cls, target_dir: str | Path) -> Project:
        target = Path(target_dir)
        mf = target / "manifest.json"
        if not mf.exists():
            raise FileNotFoundError(
                f"no manifest at {mf}. Run `dbt parse` (or `dbt compile`) in your dbt project first."
            )
        return cls(json.loads(mf.read_text()), target)

    def _build(self) -> None:
        nodes = self.raw.get("nodes", {})
        parent_map = self.raw.get("parent_map", {})
        child_map = self.raw.get("child_map", {})

        for uid, n in nodes.items():
            if n.get("resource_type") == "model":
                self.models[uid] = Model(
                    unique_id=uid,
                    name=n.get("name", ""),
                    path=n.get("original_file_path", ""),
                    layer=_layer_of(n.get("original_file_path", "")),
                    schema=n.get("schema", ""),
                    materialized=(n.get("config") or {}).get("materialized", ""),
                    description=n.get("description", "") or "",
                    columns=n.get("columns", {}) or {},
                    meta=(n.get("config") or {}).get("meta", {}) or {},
                )
            elif n.get("resource_type") == "test":
                meta = n.get("test_metadata") or {}
                kwargs = meta.get("kwargs", {}) or {}
                # `attached_node` is authoritative on modern dbt; on older manifests fall back to
                # the single model in depends_on. A test with several model parents (relationships)
                # is attached to the one it is declared on, which is what we want.
                attached = n.get("attached_node")
                if not attached:
                    deps = [d for d in (n.get("depends_on") or {}).get("nodes", [])
                            if d.startswith("model.")]
                    attached = deps[0] if len(deps) == 1 else None
                self.tests.append(Test(
                    unique_id=uid,
                    name=n.get("name", ""),
                    kind=meta.get("name"),
                    column=n.get("column_name") or kwargs.get("column_name"),
                    tests_model=attached,
                    kwargs=kwargs,
                    severity=(n.get("config") or {}).get("severity", "error") or "error",
                    description=n.get("description", "") or "",
                ))

        for uid, s in (self.raw.get("sources", {}) or {}).items():
            self.sources[uid] = Source(
                unique_id=uid,
                name=s.get("name", ""),
                source_name=s.get("source_name", ""),
                schema=s.get("schema", ""),
                description=s.get("description", "") or "",
                columns=s.get("columns", {}) or {},
            )

        known = set(self.models) | set(self.sources)
        for uid, m in self.models.items():
            m.parents = [p for p in parent_map.get(uid, []) if p in known]
            m.children = [c for c in child_map.get(uid, []) if c in self.models]
        for uid, s in self.sources.items():
            s.children = [c for c in child_map.get(uid, []) if c in self.models]

        self._attach_compiled()

    def _attach_compiled(self) -> None:
        """Disk first, manifest second. See the module docstring."""
        nodes = self.raw.get("nodes", {})
        roots = [self.target_dir / "compiled" / self.project_name]
        # Orchestrators (Dagster's dbt integration among them) write per-run target dirs beside the
        # main one. Any of them may hold the only compiled copy of a given model, so all are searched.
        for extra in sorted(self.target_dir.parent.glob("target*/compiled")):
            cand = extra / self.project_name
            if cand.is_dir() and cand not in roots:
                roots.append(cand)

        for uid, m in self.models.items():
            for root in roots:
                p = root / m.path
                if p.exists():
                    m.compiled, m.compiled_from = p.read_text(), "disk"
                    break
            if m.compiled:
                continue
            code = (nodes.get(uid) or {}).get("compiled_code")
            if code:
                m.compiled, m.compiled_from = code, "manifest"

    # ---------- graph ----------

    @cached_property
    def edges(self) -> list[tuple[str, str]]:
        """(parent, child) over models and sources. The unit of inference for the path layer."""
        return [(p, uid) for uid, m in self.models.items() for p in m.parents]

    def name_of(self, uid: str) -> str:
        if uid in self.models:
            return self.models[uid].name
        if uid in self.sources:
            s = self.sources[uid]
            return f"{s.source_name}.{s.name}"
        return uid.split(".")[-1]

    def topological(self) -> list[str]:
        """Model unique_ids, parents before children. Contract inference walks this order."""
        seen, out = set(), []

        def visit(uid: str, stack: frozenset) -> None:
            if uid in seen or uid not in self.models or uid in stack:
                return
            for p in self.models[uid].parents:
                visit(p, stack | {uid})
            seen.add(uid)
            out.append(uid)

        for uid in self.models:
            visit(uid, frozenset())
        return out

    def descendants(self, uid: str) -> set[str]:
        out, stack = set(), list(self.models.get(uid).children if uid in self.models
                                else self.sources.get(uid).children if uid in self.sources else [])
        while stack:
            n = stack.pop()
            if n in out:
                continue
            out.add(n)
            stack.extend(self.models[n].children)
        return out

    def blast_radius(self, uid: str) -> dict:
        """Severity is arithmetic over the DAG, never a judgment."""
        d = self.descendants(uid)
        return {
            "descendants": len(d),
            "marts": sum(1 for x in d if self.models[x].layer == "marts"),
        }

    # ---------- coverage ----------

    def coverage(self) -> dict:
        readable = [m for m in self.models.values() if m.readable]
        return {
            "models": len(self.models),
            "sources": len(self.sources),
            "tests": len(self.tests),
            "edges": len(self.edges),
            "readable": len(readable),
            "unreadable": len(self.models) - len(readable),
            "from_disk": sum(1 for m in readable if m.compiled_from == "disk"),
            "from_manifest": sum(1 for m in readable if m.compiled_from == "manifest"),
        }
