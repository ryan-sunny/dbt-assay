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

import functools
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify

# *** sqlglot's OPTIMIZER TALKS TO stdout. ***
# Qualifying SQL whose Jinja has been stripped produces "Cannot traverse scope _jinja_" for every
# placeholder, which is true and useless: assay already reports what it could not read, with a
# count and a reason. Leaking a library's internals into a tool's output makes it unpipeable.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

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
    # column -> what it ROOTS in (column | agg:min | coalesce:literal | window:row_number | ...).
    # Propagated parents-first, so a child referencing `c.first_year` learns it is `min(year)`
    # four models up. An AGGREGATE CAN NEVER BE PART OF THE GRAIN, which makes this a code fact
    # rather than a judgment -- but only once it has been carried this far.
    roots: dict = field(default_factory=dict)


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


def _resolve_roots(d, schema, uid, project) -> dict:
    """A model's column roots, following plain references back into its PARENTS.

    `record_first_year` reads as `c.first_year` and says nothing. `c` is a parent model, and that
    parent published `first_year` as `min(year)`. One lookup against the already-derived parent
    settles what no amount of judgment about this model's own text could.
    """

    roots = dict(d.resolved_roots or d.output_roots)
    by_rel = {}
    for parent in project.models[uid].parents:
        rel = schema.relation.get(parent)
        if rel:
            by_rel[rel.lower()] = schema.columns(parent).roots

    for name, root in list(roots.items()):
        if root != "column":
            continue
        ref = _plain_ref(d.output_exprs.get(name, ""))
        if ref is None:
            continue
        table, col = ref
        rel = (d.alias_relation.get(table.lower()) or "").lower()
        parent_roots = by_rel.get(rel)
        if parent_roots:
            roots[name] = parent_roots.get(col.lower(), root)
    return roots


@functools.lru_cache(maxsize=16384)
def _plain_ref(text: str):
    """(table, column) when `text` is a qualified column reference, else None."""
    import sqlglot
    from sqlglot import exp as _exp
    try:
        e = sqlglot.parse_one(text, dialect="duckdb")
    except Exception:                                        # noqa: BLE001
        return None
    if not isinstance(e, _exp.Column) or not e.table:
        return None
    return (e.table, e.name)


def derive_columns(project, digests: dict[str, Digest], schema: Schema,
                   dialect: str | None = None, memo=None) -> dict:
    """Walk the DAG parents-first, expanding stars with what the parents were found to offer.

    `memo` (a DigestCache) keeps each expansion keyed by the SQL, the dialect and the parents'
    columns it was expanded against: everything it reads."""
    dialect = dialect or getattr(project, "dialect", "duckdb")
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
                # *** A STAR OVER A CTE NEEDS NO EXTERNAL SCHEMA. ***
                # Requiring parents' columns before attempting expansion left `select * from
                # some_cte` reporting its output as literally ['*']. On a real public package that
                # meant 38 of 38 tests were unevaluable and the check reported "no findings",
                # which is the same shape as a pass.
                parent_schema = schema.for_parents(uid)

                def expand(sql=m.compiled, parent_schema=parent_schema):
                    try:
                        tree = sqlglot.parse_one(sql, dialect=dialect)
                        q = qualify(tree, schema=parent_schema, dialect=dialect,
                                    validate_qualify_columns=False, infer_schema=True)
                        sel = q if isinstance(q, exp.Select) else q.find(exp.Select)
                        return (True, None if sel is None else
                                [e.alias_or_name for e in sel.expressions
                                 if e.alias_or_name and e.alias_or_name != "*"])
                    except Exception:                                   # noqa: BLE001
                        return (False, None)
                ok, expanded = (memo.memo("star", json.dumps([dialect, m.compiled, parent_schema],
                                                             sort_keys=True), expand)
                                if memo is not None else expand())
                if not ok:
                    # A qualify failure is not fatal: the un-expanded column list is still true,
                    # just incomplete, and the count is reported rather than buried.
                    stats["qualify_failed"] += 1
                elif expanded is not None and len(expanded) > len(d.output_columns):
                    cols = expanded
                    stats["expanded"] += 1
            if not cols:
                cols = list(d.output_columns)

        # *** A LIST CONTAINING `*` IS NOT A COLUMN LIST, IT IS AN UNEXPANDED STAR. ***
        # When qualify cannot expand, the fallback was the raw output columns -- which still hold
        # the literal `*`. Downstream that is a column named `*`, and every REAL column of the
        # model is simply absent, so everything reading it reports `unknown` provenance and no
        # test on it can be evaluated. Measured: three models on a 358-model warehouse, and 131
        # unknown columns in their descendants.
        #
        # The catalog knows the real names here, and names from the catalog with the roots we did
        # manage to resolve is strictly more than a star nobody expanded. The SOURCE says catalog,
        # because that is where the names came from.
        if cols and "*" in cols:
            cat = schema._catalog_columns(uid)
            if cat:
                schema.by_uid[uid] = Columns(cat, "catalog",
                                             _resolve_roots(d, schema, uid, project))
                stats["from_catalog"] += 1
                stats["star_unexpanded"] = stats.get("star_unexpanded", 0) + 1
                continue
            # No catalog either. Drop the `*` rather than publishing it as a column name: an
            # incomplete list is true, and a column called `*` is not.
            cols = [c for c in cols if c != "*"]
            stats["star_unexpanded"] = stats.get("star_unexpanded", 0) + 1

        if cols:
            schema.by_uid[uid] = Columns(cols, "derived", _resolve_roots(d, schema, uid, project))
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
