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

# *** THE MANIFEST NAMES ITS OWN ADAPTER, SO NOBODY SHOULD HAVE TO PASS A DIALECT. ***
# Defaulting to duckdb meant every other warehouse was silently misparsed unless the user knew a
# flag existed. Most adapter names ARE sqlglot dialect names; these are the ones that are not.
ADAPTER_DIALECT = {
    "postgres": "postgres", "redshift": "redshift", "snowflake": "snowflake",
    "bigquery": "bigquery", "databricks": "databricks", "spark": "spark",
    "duckdb": "duckdb", "trino": "trino", "athena": "athena", "clickhouse": "clickhouse",
    "fabric": "tsql", "synapse": "tsql", "sqlserver": "tsql", "mssql": "tsql",
    "materialize": "postgres", "dremio": "presto", "starrocks": "starrocks",
    "doris": "doris", "oracle": "oracle", "teradata": "teradata", "exasol": "postgres",
    "glue": "spark", "vertica": "postgres", "greenplum": "postgres",
}


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
    compiled_from: str = "none"   # disk | manifest | stripped | none
    compiled_path: str | None = None
    # Other target dirs held a DIFFERENT compiled body for this model. Which one you audit changes
    # the answer, so the disagreement is recorded rather than resolved by luck.
    compiled_conflicts: list = field(default_factory=list)
    # *** WHOSE MODEL IS THIS. ***
    # An installed package's models are in the manifest and are not yours. On a real warehouse
    # that is 30 of 358 models, every one unreadable, carrying 541 columns of unknown provenance,
    # all diluting numbers about the project somebody actually wrote. dbt records the owner, so
    # this is exact rather than a name heuristic.
    package: str = ""
    project: str = ""
    # *** dbt ALREADY HASHES EVERY MODEL FILE AND assay READ NONE OF THEM. ***
    # `checksum.checksum` is a sha256 of the model's source, present on 358 of 358 models on the
    # field warehouse. It is what lets a judged answer say "the SQL I was computed from has moved"
    # for free, with no call and no re-parse. Empty when dbt did not record one, which is not the
    # same as unchanged and must never read as such.
    checksum: str = ""

    @property
    def readable(self) -> bool:
        return bool(self.compiled)

    @property
    def is_installed_package(self) -> bool:
        """True when this model came from a package rather than from this project.

        *** AND AN UNREADABLE MODEL OF YOUR OWN MUST NEVER BE HIDDEN BY THIS. ***
        A package's model being unreadable is not your problem. One of YOURS being unreadable is
        the "you did not compile" signal, which is exactly what a filter on `unreadable` would
        have hidden. That is why the split is by owner and never by whether it parsed.
        """
        return bool(self.package and self.project and self.package != self.project)


@dataclass
class Source:
    unique_id: str
    name: str
    source_name: str
    schema: str
    description: str
    columns: dict
    children: list[str] = field(default_factory=list)


@dataclass
class Exposure:
    """Something OUTSIDE the warehouse that depends on named models: a dashboard, an app, a report.

    *** THE ONLY PLACE A PROJECT WRITES DOWN WHAT ITS MODELS ARE FOR. ***
    Everything assay ranked by was a proxy: `marts` is a count of downstream models that happen to
    sit in a layer. An exposure is the project saying "these feed the paid report", which is a
    different sentence to put in front of somebody deciding what to fix first (25.23d).
    """
    unique_id: str
    name: str
    label: str
    type: str
    owner: str
    url: str
    maturity: str
    depends_on: list[str]
    path: str

    @property
    def title(self) -> str:
        return self.label or self.name


class Project:
    """A dbt project, read from its manifest and its compiled output."""

    def __init__(self, manifest: dict, target_dir: Path, project_root: Path | None = None):
        self.raw = manifest
        self.target_dir = target_dir
        # Where `original_file_path` is relative to, so the raw model can be read when there is no
        # compiled output. Defaults to the parent of target/, which is the usual layout.
        self.project_root = Path(project_root) if project_root else target_dir.parent
        self.project_name = manifest.get("metadata", {}).get("project_name", "")
        self.dbt_version = manifest.get("metadata", {}).get("dbt_version", "")
        self.adapter_type = manifest.get("metadata", {}).get("adapter_type", "") or ""
        self.dialect_override = ""      # set once, by the CLI, when --dialect is passed
        self.models: dict[str, Model] = {}
        self.sources: dict[str, Source] = {}
        self.tests: list[Test] = []
        self.exposures: dict[str, Exposure] = {}
        self._exposed: dict[str, set] = {}
        self._build()

    # ---------- loading ----------

    @classmethod
    def load(cls, target_dir: str | Path, project_root: str | Path | None = None) -> Project:
        target = Path(target_dir)
        mf = target / "manifest.json"
        if not mf.exists():
            raise FileNotFoundError(
                f"no manifest at {mf}. Run `dbt parse` (or `dbt compile`) in your dbt project first."
            )
        return cls(json.loads(mf.read_text()), target, project_root)

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
                    package=n.get("package_name", "") or "",
                    project=self.project_name,
                    checksum=((n.get("checksum") or {}).get("checksum", "") or ""),
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

        self._attach_exposures()
        self._attach_compiled()

    def _attach_exposures(self) -> None:
        """Each exposure, and every model and source upstream of what it names.

        A model an exposure names directly reaches it, and so does everything that model is built
        from: `stg_blm_plss_sections` reaches the report through the marts it feeds, which is the
        whole point of saying so.
        """
        for uid, e in (self.raw.get("exposures") or {}).items():
            owner = e.get("owner") or {}
            self.exposures[uid] = Exposure(
                unique_id=uid, name=e.get("name", ""), label=e.get("label", "") or "",
                type=e.get("type", "") or "",
                owner=(owner.get("name") or owner.get("email") or "") if isinstance(owner, dict)
                else str(owner or ""),
                url=e.get("url", "") or "", maturity=e.get("maturity", "") or "",
                depends_on=list((e.get("depends_on") or {}).get("nodes") or []),
                path=e.get("original_file_path", "") or "")
        for uid, e in self.exposures.items():
            stack, seen = [d for d in e.depends_on if d in self.models or d in self.sources], set()
            while stack:
                n = stack.pop()
                if n in seen:
                    continue
                seen.add(n)
                self._exposed.setdefault(n, set()).add(uid)
                if n in self.models:
                    stack.extend(self.models[n].parents)

    def exposures_of(self, uid: str) -> list[Exposure]:
        """The exposures this model or source reaches, directly or through what it feeds."""
        return sorted((self.exposures[x] for x in self._exposed.get(uid, ())),
                      key=lambda x: x.title)

    def _raw_sql(self, m: Model) -> str | None:
        if not self.project_root:
            return None
        p = self.project_root / m.path
        try:
            return p.read_text() if p.exists() else None
        except OSError:
            return None

    def _attach_compiled(self) -> None:
        """The CANONICAL copy first, then the manifest. Disagreements are recorded, never resolved
        by whichever directory happened to sort first.

        *** THIS WAS A FIRST-MATCH BUG AND IT CHANGED THE ANSWER. ***
        Orchestrators (Dagster's dbt integration among them) write per-run `target-*` directories
        beside the main one, each holding its own compiled copy of some models. Taking the first hit
        in glob order meant the audited body depended on directory sort order: the same project read
        295 models one way and 265 the other, with different findings. The canonical
        `target/compiled/<project>` now wins, a sibling is used ONLY where the canonical copy is
        absent, and a sibling whose body DIFFERS is recorded on the model so the ambiguity is
        visible instead of silent.
        """
        nodes = self.raw.get("nodes", {})
        compiled_dirs = sorted(self.target_dir.parent.glob("target*/compiled"))

        # *** EACH MODEL UNDER ITS OWN PACKAGE'S DIRECTORY. ***
        # dbt writes an installed package's models to `target/compiled/<package>/`, and this looked
        # only under the root project's name -- so the 30 Elementary models on the field warehouse
        # read as unreadable while their compiled SQL sat one directory over.
        def roots(package: str):
            canonical = self.target_dir / "compiled" / package
            return canonical, [d / package for d in compiled_dirs
                               if (d / package).is_dir() and (d / package) != canonical]
        by_pkg: dict = {}

        for uid, m in self.models.items():
            pkg = m.package or self.project_name
            if pkg not in by_pkg:
                by_pkg[pkg] = roots(pkg)
            canonical, siblings = by_pkg[pkg]
            primary = canonical / m.path
            if primary.exists():
                m.compiled, m.compiled_from = primary.read_text(), "disk"
                m.compiled_path = str(primary)
            else:
                for root in siblings:
                    p = root / m.path
                    if p.exists():
                        m.compiled, m.compiled_from = p.read_text(), "disk"
                        m.compiled_path = str(p)
                        break

            if m.compiled:
                for root in siblings:
                    p = root / m.path
                    if str(p) != m.compiled_path and p.exists() and p.read_text() != m.compiled:
                        m.compiled_conflicts.append(str(p))
                continue

            code = (nodes.get(uid) or {}).get("compiled_code")
            if code:
                m.compiled, m.compiled_from, m.compiled_path = code, "manifest", "<manifest>"
                continue

            # *** LAST RESORT: THE RAW MODEL, WITH ITS JINJA STRIPPED. ***
            # Compiling needs a warehouse connection, and plenty of projects cannot be compiled by
            # whoever wants to audit them -- a reviewer without credentials, a security team, a
            # stranger evaluating the tool. Resolving ref() and source() and dropping control
            # blocks is NOT a compile, and a macro-generated model will not survive it, so the
            # fidelity is recorded on the model rather than assumed.
            raw = self._raw_sql(m)
            if raw:
                from .backtest import dejinja
                m.compiled, m.compiled_from = dejinja(raw), "stripped"
                m.compiled_path = str(self.project_root / m.path) if self.project_root else m.path

    @property
    def dialect(self) -> str:
        """The SQL this project actually speaks, read from its own manifest.

        Everything that parses reads this rather than defaulting, so ONE place decides and a call
        site that forgets to thread a flag cannot quietly parse Snowflake as DuckDB.
        """
        if self.dialect_override:
            return self.dialect_override
        return ADAPTER_DIALECT.get(self.adapter_type.lower(), self.adapter_type.lower() or "duckdb")

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
            "exposures": [e.title for e in self.exposures_of(uid)],
        }

    # ---------- coverage ----------

    def coverage(self) -> dict:
        readable = [m for m in self.models.values() if m.readable]
        # *** AN INSTALLED PACKAGE'S MODELS ARE NOT A GAP IN YOUR AUDIT. ***
        # Reported from the field (25.5): `completeness` said "30 models assay could not read --
        # not audited, and not a pass", and all 30 were Elementary's. A reader takes that for
        # thirty of their own models outside the audit. `unreadable` is YOURS; the packages are
        # counted beside it, by name, so nothing is hidden and nothing is misattributed.
        theirs = [m for m in self.models.values()
                  if not m.readable and m.is_installed_package]
        by_pkg: dict = {}
        for m in theirs:
            by_pkg[m.package] = by_pkg.get(m.package, 0) + 1
        return {
            "models": len(self.models),
            "sources": len(self.sources),
            "tests": len(self.tests),
            "edges": len(self.edges),
            "readable": len(readable),
            "unreadable": len(self.models) - len(readable) - len(theirs),
            "installed_unreadable": len(theirs),
            "installed_packages": dict(sorted(by_pkg.items())),
            "from_disk": sum(1 for m in readable if m.compiled_from == "disk"),
            "from_manifest": sum(1 for m in readable if m.compiled_from == "manifest"),
            "from_stripped": sum(1 for m in readable if m.compiled_from == "stripped"),
            "conflicting_copies": sum(1 for m in readable if m.compiled_conflicts),
        }
