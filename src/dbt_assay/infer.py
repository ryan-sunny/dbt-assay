"""Physical relations, a schema, and each model's real output columns, derived in DAG order.

*** THIS IS THE MOVE THAT KEEPS WORKING, ONE LEVEL DOWN. ***
A model's columns are inferred with its PARENTS' already-derived columns in hand, walking the graph
parents-first. Small input, local judgment, and the relationship is resolved by code. The same shape
the contract inference will use when the judgment tier lands; this is its foundation.

*** WITHOUT A SCHEMA, `select *` REPORTS NOTHING AND THE TOOL LIES QUIETLY. ***
A model that selects star has no output columns until something expands the star. Every downstream
edge fact then records "0 columns offered", which reads exactly like a model that genuinely offers
nothing. Feeding sqlglot a schema turns that silence into a list.

*** THREE SOURCES OF COLUMNS, IN PRIORITY ORDER, AND THE PROVENANCE IS KEPT. ***
  derived   -- inferred from this model's own SQL with its parents known. Best: it is what the code
               actually produces today.
  catalog   -- `target/catalog.json`, written by `dbt docs generate`. Real columns from the
               warehouse, and possibly STALE. Used for leaves, where nothing can be inferred.
  declared  -- `columns:` in schema.yml. Frequently partial, since people document what matters.
A caller that cannot tell which one it got cannot judge how much to trust it, so `source_of` says.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify

from .parse import Digest


def relation_name(node: dict) -> str:
    """`catalog.schema.table`, matching how compiled dbt SQL spells a relation."""
    ident = node.get("alias") or node.get("identifier") or node.get("name") or ""
    return ".".join(p for p in (node.get("database"), node.get("schema"), ident) if p)


@dataclass
class Columns:
    """What a relation offers, and where that knowledge came from."""
    names: list[str] = field(default_factory=list)
    source_of: str = "unknown"          # derived | catalog | declared | unknown


class Schema:
    def __init__(self, project, catalog: dict | None = None):
        self.project = project
        self.catalog = catalog or {}
        self.by_uid: dict[str, Columns] = {}
        self.relation: dict[str, str] = {}      # unique_id -> catalog.schema.table
        self.uid_of: dict[str, str] = {}        # relation -> unique_id
        self._seed()

    @classmethod
    def load(cls, project, target_dir: str | Path) -> Schema:
        cat = Path(target_dir) / "catalog.json"
        data = json.loads(cat.read_text()) if cat.exists() else {}
        return cls(project, data)

    @property
    def catalog_present(self) -> bool:
        return bool(self.catalog)

    def _catalog_columns(self, uid: str) -> list[str]:
        for bucket in ("nodes", "sources"):
            entry = (self.catalog.get(bucket) or {}).get(uid)
            if entry:
                return list(entry.get("columns") or {})
        return []

    def _seed(self) -> None:
        raw_nodes = self.project.raw.get("nodes", {})
        raw_sources = self.project.raw.get("sources", {})

        for uid in self.project.models:
            rel = relation_name(raw_nodes.get(uid, {}))
            self.relation[uid] = rel
            if rel:
                self.uid_of[rel.lower()] = uid

        for uid, src in self.project.sources.items():
            rel = relation_name(raw_sources.get(uid, {}))
            self.relation[uid] = rel
            if rel:
                self.uid_of[rel.lower()] = uid
            # A source is a leaf: nothing can be inferred for it, so catalog then declared.
            cols = self._catalog_columns(uid)
            if cols:
                self.by_uid[uid] = Columns(cols, "catalog")
            elif src.columns:
                self.by_uid[uid] = Columns(list(src.columns), "declared")

    def columns(self, uid: str) -> Columns:
        return self.by_uid.get(uid, Columns())

    def for_parents(self, uid: str) -> dict:
        """A sqlglot schema mapping each parent's RELATION NAME to its columns."""
        out: dict = {}
        for p in self.project.models[uid].parents:
            cols = self.columns(p).names
            rel = self.relation.get(p)
            if rel and cols:
                out[rel] = {c: "UNKNOWN" for c in cols}
        return out


def derive_columns(project, digests: dict[str, Digest], schema: Schema,
                   dialect: str = "duckdb") -> dict:
    """Walk the DAG parents-first, expanding stars with what the parents were found to offer."""
    stats = {"expanded": 0, "qualify_failed": 0, "from_sql": 0, "from_catalog": 0,
             "from_declared": 0, "unknown": 0}
    raw_nodes = project.raw.get("nodes", {})

    for uid in project.topological():
        d = digests.get(uid)
        m = project.models[uid]
        cols: list[str] = []

        if d and d.ok and m.compiled:
            needs_star = "*" in m.compiled
            if needs_star:
                parent_schema = schema.for_parents(uid)
                if parent_schema:
                    try:
                        tree = sqlglot.parse_one(m.compiled, dialect=dialect)
                        q = qualify(tree, schema=parent_schema, dialect=dialect,
                                    validate_qualify_columns=False, infer_schema=True)
                        sel = q if isinstance(q, exp.Select) else q.find(exp.Select)
                        if sel is not None:
                            expanded = [e.alias_or_name for e in sel.expressions
                                        if e.alias_or_name and e.alias_or_name != "*"]
                            if len(expanded) > len(d.output_columns):
                                cols = expanded
                                stats["expanded"] += 1
                    except Exception:                                   # noqa: BLE001
                        # A qualify failure is not fatal: the un-expanded column list is still true,
                        # just incomplete, and the count is reported rather than buried.
                        stats["qualify_failed"] += 1
            if not cols:
                cols = list(d.output_columns)

        if cols:
            schema.by_uid[uid] = Columns(cols, "derived")
            stats["from_sql"] += 1
            continue

        cat = schema._catalog_columns(uid)
        if cat:
            schema.by_uid[uid] = Columns(cat, "catalog")
            stats["from_catalog"] += 1
            continue

        declared = list((raw_nodes.get(uid) or {}).get("columns") or {})
        if declared:
            schema.by_uid[uid] = Columns(declared, "declared")
            stats["from_declared"] += 1
        else:
            stats["unknown"] += 1

    return stats
