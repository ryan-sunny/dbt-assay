"""dbt-project-evaluator's rows, read into assay's findings: merged, weighed, and ruled on once.

*** 1,035 ROWS WOULD HAVE BEEN 1,035 CARDS, AND ABOUT 185 OF THEM ARE NEW. *** (sunny-data,
2026-09-25) The evaluator reports one row per (rule, node, and often per pair of nodes), so one
fact arrives several times: "this model reads raw sources outside staging" is a row in
`fct_direct_join_to_source` per source, another in `fct_marts_or_intermediate_dependent_on_source`,
another in `fct_multiple_sources_joined`, and the same fact seen from the source's side in
`fct_source_fanout`. A person rules on the FACT. So the rows become one card per (subject,
underlying fact), and the rule names ride on the card as evidence.

Where assay already says the same thing about the same subject, the evaluator does not add a
card: its rule is named on assay's card (`evidence.evaluator.also_flagged_by`), so one ruling
covers both. That evidence never enters a finding's identity, so a ruling survives the evaluator
starting or stopping to flag something.

*** A CONVENTION RULE THAT FIRES ON MOST OF A LAYER IS ABOUT THE CONFIG, NOT THE PROJECT. ***
Measured on the same project before `exclude_packages`: most naming rows were Elementary's own
models. Those rows become one card, `evaluator_config_does_not_fit`, naming the setting to
change, instead of one card per model telling a person to rename code they do not own.

Exceptions live in assay only: an accept or a waiver in the review form. Nothing here writes to
the evaluator's exceptions seed.

Nothing here reimplements a rule: every card is a row the evaluator wrote. What assay adds is
the merge, the blast radius (arithmetic over the DAG), and, for the rules with real exceptions,
a judged reading (`assay practices`), which annotates a card and never removes one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

EVALUATOR = "dbt_project_evaluator"

# A rule's rows this many or more, read in one statement. A table that reaches it is said to be
# truncated on its card rather than read in part without a word.
ROW_LIMIT = 5000

# "Most" of a layer: a convention rule firing on more than this share, on at least MIN models.
MOST, MIN_LAYER = 0.5, 5

# The graph table that says how the evaluator classified each node (model_type, package,
# excluded). Read beside the fct_ tables; only the misconfiguration checks use it.
GRAPH = "int_all_graph_resources"

# Rules whose rows describe the whole project, not a node. assay has its own coverage findings.
SUMMARY_RULES = {"fct_documentation_coverage", "fct_test_coverage"}

# The rules folded into `reads_raw_source_outside_staging`: one fact, four views of it.
RAW_SOURCE_RULES = ("fct_direct_join_to_source", "fct_marts_or_intermediate_dependent_on_source",
                    "fct_multiple_sources_joined", "fct_source_fanout")

# Which of assay's own checks say the same thing about the same subject. A card whose subject
# already carries one of these is folded into it.
MERGES_INTO = {
    "no_primary_key_test": ("grain_unresolved", "grain_contradicts_test"),
    "model_has_no_description": ("column_has_no_description",),
    "source_is_never_used": ("source_reaches_nothing",),
    "source_freshness_undeclared": ("source_freshness_undeclared",),
}

# The cards whose rules have real exceptions: `assay practices` asks about these.
ADJUDICATED = {"reads_raw_source_outside_staging", "source_read_directly_by_many_models",
               "model_has_many_leaf_children", "rejoins_an_upstream_concept", "too_many_joins",
               "model_refs_nothing", "staging_reads_staging", "exposure_rests_on_views"}

# The setting that answers a convention rule firing on most of a layer, as the package names it.
CONVENTION_SETTING = {
    "fct_model_naming_conventions": ("the `{t}_prefixes` var (the evaluator's names for a {t} "
                                     "model's prefixes)"),
    "fct_model_directories": "the `{t}_folder_name` var, or disabling `fct_model_directories`",
    "fct_source_directories": "disabling `fct_source_directories`",
    "fct_test_directories": "disabling `fct_test_directories`",
}


@dataclass
class Reading:
    """What the evaluator's tables held, and what could not be read."""
    schema: str = ""
    rows: dict = field(default_factory=dict)          # rule -> [row dict]
    graph: list = field(default_factory=list)         # int_all_graph_resources rows
    unread: dict = field(default_factory=dict)        # rule -> why
    truncated: list = field(default_factory=list)
    installed: bool = False
    disabled: list = field(default_factory=list)      # rules the project switched off

    @property
    def total(self) -> int:
        return sum(len(v) for k, v in self.rows.items() if k not in SUMMARY_RULES)


def _evaluator_nodes(project) -> list[dict]:
    """The package's models, enabled or not: a project that switches the evaluator on with a var
    (sunny-data does) lists every one of them under `disabled` in an ordinary parse."""
    raw = project.raw or {}
    out = [n for n in (raw.get("nodes") or {}).values()
           if n.get("package_name") == EVALUATOR and n.get("resource_type") == "model"]
    for group in (raw.get("disabled") or {}).values():
        out += [n for n in group or []
                if n.get("package_name") == EVALUATOR and n.get("resource_type") == "model"]
    return out


def installed(project) -> bool:
    return bool(_evaluator_nodes(project))


def switched_off(project) -> set:
    """The rules the project disabled: listed under `disabled` while other evaluator models are
    enabled. When every one is disabled (a var-gated package in an ordinary parse), the manifest
    cannot tell a rule switched off from a package switched off, so this is empty."""
    raw = project.raw or {}
    on = {n.get("name") for n in (raw.get("nodes") or {}).values()
          if n.get("package_name") == EVALUATOR and n.get("resource_type") == "model"}
    if not on:
        return set()
    off = {n.get("name") for g in (raw.get("disabled") or {}).values() for n in g or []
           if n.get("package_name") == EVALUATOR and n.get("resource_type") == "model"}
    return {n for n in off - on if n and n.startswith("fct_")}


def relations(project, schema: str | None = None) -> dict:
    """{model name: relation} for the fct_ tables and the graph table, from the manifest.

    The schema is the manifest's own for each node, so nothing is guessed; `schema` overrides it
    (`--evaluator-schema`, or `practices.evaluator_schema` in audit.yml)."""
    out: dict = {}
    for n in _evaluator_nodes(project):
        name = n.get("name") or ""
        if not (name.startswith("fct_") or name == GRAPH) or name in out:
            continue
        ident = n.get("alias") or name
        if schema:
            db = n.get("database")
            out[name] = ".".join(p for p in (db, schema, ident) if p)
        else:
            out[name] = (n.get("relation_name") or
                         ".".join(p for p in (n.get("database"), n.get("schema"), ident) if p))
    return out


def _schema_of(rels: dict) -> str:
    rel = next(iter(rels.values()), "")
    parts = [p.strip('"`') for p in rel.split(".")]
    return parts[-2] if len(parts) >= 2 else ""


def read(project, runner, schema: str | None = None, dialect: str = "duckdb",
         cats: dict | None = None) -> Reading:
    """Every fct_ table with rows, and the graph table, in as few dbt calls as the engine allows.

    *** A MISSING TABLE IN A BATCH COSTS A CALL PER HALVING. *** A disabled rule has no table,
    and a batch with a failing member is split in halves to find it, each half another dbt
    startup. Where the engine has `information_schema` (DuckDB, Postgres, Snowflake, Redshift),
    one statement lists the schema's tables first and only those are read.
    """
    from .probe import ask_many
    rels = relations(project, schema)
    rep = Reading(schema=schema or _schema_of(rels), installed=bool(rels))
    if not rels:
        return rep
    # A rule the project switched off is not a rule that failed to build: it is not read and not
    # reported (sunny-data disables the three folder rules on purpose).
    off = switched_off(project)
    rep.disabled = sorted(off)
    wanted = {k: v for k, v in rels.items()
              if (cats or {}).get(k) != "off" and k not in SUMMARY_RULES and k not in off}
    exists = None
    if dialect in ("duckdb", "postgres", "snowflake", "redshift", "mysql", "bigquery") \
            and rep.schema:
        if dialect == "bigquery":
            # the dataset's own INFORMATION_SCHEMA; the project from a relation name
            first = next(iter(rels.values())).replace("`", "").split(".")
            proj = first[-3] + "." if len(first) >= 3 else ""
            listing = (f"select lower(table_name) as t from "
                       f"`{proj}{rep.schema}`.INFORMATION_SCHEMA.TABLES")
        else:
            listing = (f"select lower(table_name) as t from information_schema.tables "
                       f"where lower(table_schema) = '{rep.schema.lower()}'")
        (got,) = ask_many(runner, [(listing, 2000)])
        if not got.failed:
            exists = {str(r.get("t") or next(iter(r.values()), "")).lower() for r in got.rows}
    todo = []
    for name, rel in sorted(wanted.items()):
        ident = rel.split(".")[-1].strip('"`').lower()
        if exists is not None and ident not in exists:
            rep.unread[name] = "not built"
            continue
        todo.append((name, rel))
    # Forty tables of a few hundred rows each: one statement, not a batch per twelve.
    results = ask_many(runner, [(f"select * from {rel}", 50_000 if name == GRAPH else ROW_LIMIT)
                                for name, rel in todo], max_rows=10_000_000, max_statements=60)
    for (name, _rel), res in zip(todo, results):
        if res.failed:
            rep.unread[name] = res.why or "could not be read"
            continue
        rows = [{str(k).lower(): v for k, v in r.items()} for r in res.rows]
        if name == GRAPH:
            rep.graph = rows
            continue
        if len(rows) >= ROW_LIMIT:
            rep.truncated.append(name)
        if rows:
            rep.rows[name] = rows
    return rep


# ---------------------------------------------------------------- resolving names to nodes

class _Names:
    """Evaluator names -> this project's nodes. Models by name, sources as `source.table`."""

    def __init__(self, project):
        self.project = project
        self.model: dict = {}
        for uid, m in project.models.items():
            # the root project's model wins a name a package also uses
            if m.name not in self.model or not getattr(m, "is_installed_package", False):
                self.model[m.name] = uid
        self.source = {f"{s.source_name}.{s.name}".lower(): uid
                       for uid, s in project.sources.items()}
        self.source_group: dict = {}
        for uid, s in project.sources.items():
            self.source_group.setdefault(s.source_name.lower(), []).append(uid)

    def of(self, name) -> str | None:
        n = str(name or "").strip()
        if not n:
            return None
        return self.model.get(n) or self.source.get(n.lower()) or self.model.get(n.split(".")[-1])

    def label(self, uid: str) -> str:
        if uid in self.project.models:
            return self.project.models[uid].name
        s = self.project.sources.get(uid)
        return f"{s.source_name}.{s.name}" if s else uid

    def foreign(self, uid: str | None) -> str:
        """The package a node belongs to when it is not the project's own, else ""."""
        m = self.project.models.get(uid or "")
        return m.package if m is not None and getattr(m, "is_installed_package", False) else ""


def _split(v) -> list[str]:
    return [x.strip() for x in str(v or "").split(",") if x.strip()]


# ---------------------------------------------------------------- cards

@dataclass
class Tally:
    rows: int = 0
    cards: int = 0
    folded: int = 0          # rows that landed on one of assay's own findings
    config: int = 0          # rows absorbed by a config card
    unmatched: int = 0       # rows naming a node this manifest does not have
    by_rule: dict = field(default_factory=dict)

    def line(self) -> str:
        bits = [f"{self.rows:,} dbt-project-evaluator row(s) became {self.cards:,} card(s)"]
        if self.folded:
            bits.append(f"{self.folded:,} landed on assay's own findings about the same thing")
        if self.config:
            bits.append(f"{self.config:,} describe the evaluator's configuration, not the project")
        if self.unmatched:
            bits.append(f"{self.unmatched:,} name a node this manifest does not have (an older "
                        f"evaluator build than the manifest?)")
        return "; ".join(bits) + ". One card per (subject, fact); the rules are named on it."


def _ev(rules, rows: int, **more) -> dict:
    """The volatile part of a card's evidence, under one key identity never reads."""
    return {"evaluator": {"rules": sorted(set(rules)), "rows": rows, **more}}


def _reads_raw(rep: Reading, names: _Names, skip: set) -> tuple[list, dict]:
    """One card per model that reads raw sources outside staging, and the sources left over.

    The four rules name the same fact. `fct_source_fanout` names it from the source's side, so
    its rows fold into the cards of the models it lists; a source whose readers carry no card
    here (they are staging models, which is what reading a source is for) gets one card of its
    own when `fct_source_fanout` flagged it.
    """
    from .checks.structural import Finding
    per: dict = {}                                    # model uid -> {sources, rules, rows}

    def add(child, source, rule):
        uid = names.of(child)
        if uid is None or uid in skip:
            return False
        e = per.setdefault(uid, {"sources": set(), "rules": set(), "rows": 0})
        if source:
            e["sources"].add(str(source))
        e["rules"].add(rule)
        e["rows"] += 1
        return True

    joins = rep.rows.get("fct_direct_join_to_source", [])
    for r in joins:
        if str(r.get("parent_resource_type") or "") in ("source", "seed"):
            add(r.get("child"), r.get("parent"), "fct_direct_join_to_source")
    for r in rep.rows.get("fct_marts_or_intermediate_dependent_on_source", []):
        add(r.get("child"), r.get("parent"), "fct_marts_or_intermediate_dependent_on_source")
    for r in rep.rows.get("fct_multiple_sources_joined", []):
        uid = names.of(r.get("child"))
        if uid is None or uid in skip:
            continue
        e = per.setdefault(uid, {"sources": set(), "rules": set(), "rows": 0})
        e["sources"].update(_split(r.get("source_parents")))
        e["rules"].add("fct_multiple_sources_joined")
        e["rows"] += 1
    # `fct_direct_join_to_source` also lists the child's MODEL parents: the same finding's rows.
    for r in joins:
        if str(r.get("parent_resource_type") or "") not in ("source", "seed"):
            e = per.get(names.of(r.get("child")) or "")
            if e is not None:
                e["rows"] += 1
    leftover: dict = {}
    for r in rep.rows.get("fct_source_fanout", []):
        src = str(r.get("parent") or "")
        readers = [names.of(c) for c in _split(r.get("model_children"))]
        carded = [u for u in readers if u in per]
        for u in carded:
            per[u]["rules"].add("fct_source_fanout")
            per[u]["sources"].add(src)
        if carded:
            per[carded[0]]["rows"] += 1
        else:
            leftover[src] = [u for u in readers if u]
    out = []
    for uid, e in per.items():
        m = names.project.models.get(uid)
        if m is None:
            continue
        srcs = sorted(e["sources"])
        out.append(Finding(
            check="reads_raw_source_outside_staging", subject=uid, subject_name=m.name,
            file=m.path, base=2,
            summary=f"`{m.name}` reads raw sources directly, outside the staging layer",
            detail=(f"It reads {len(srcs)} raw source(s) itself: {', '.join(srcs[:8])}"
                    + (f" and {len(srcs) - 8} more" if len(srcs) > 8 else "") + ". "
                    "Whatever staging cleans up for those sources (renames, types, filters, "
                    "dedups) does not happen on this path, and a fix made in staging does not "
                    "reach it. Flagged by dbt-project-evaluator: "
                    + ", ".join(f"`{x}`" for x in sorted(e["rules"])) + ". "
                    "Some cases are deliberate (a small seed-like source read once); an accept "
                    "or a waiver in the form is where that is recorded."),
            evidence={"fact": "reads_raw_source",
                      **_ev(e["rules"], e["rows"], sources=srcs[:40])}))
    return out, leftover


def _source_fanout_leftover(leftover: dict, names: _Names, rows_of: dict) -> list:
    from .checks.structural import Finding
    out = []
    for src, readers in sorted(leftover.items()):
        uid = names.of(src)
        if uid is None or names.project.sources.get(uid) is None:
            continue
        labels = sorted(names.label(u) for u in readers)
        out.append(Finding(
            check="source_read_directly_by_many_models", subject=uid, subject_name=src,
            file="", base=1,
            summary=f"`{src}` is read directly by several models",
            detail=(f"Read by {', '.join(labels[:10])}"
                    + (f" and {len(labels) - 10} more" if len(labels) > 10 else "") + ". "
                    "Each reader repeats whatever the source needs; one staging model would say "
                    "it once. Flagged by dbt-project-evaluator's `fct_source_fanout`."),
            evidence={"fact": "source_fanout",
                      **_ev(["fct_source_fanout"], rows_of.get(src, 1), readers=labels[:40])}))
    return out


def _undocumented(rep: Reading, names: _Names, skip: set, rule: str, out: list) -> None:
    from .checks.structural import Finding
    for r in rep.rows.get(rule, []):
        uid = names.of(r.get("resource_name"))
        if uid is None or uid in skip or uid not in names.project.models:
            continue
        if any(f.subject == uid and f.check == "model_has_no_description" for f in out):
            ev = next(f for f in out if f.subject == uid and f.check == "model_has_no_description")
            ev.evidence["evaluator"]["rules"] = sorted({*ev.evidence["evaluator"]["rules"], rule})
            ev.evidence["evaluator"]["rows"] += 1
            continue
        m = names.project.models[uid]
        out.append(Finding(
            check="model_has_no_description", subject=uid, subject_name=m.name, file=m.path,
            base=1, summary=f"`{m.name}` has no description",
            detail=(f"Nothing says what this model is or what one row of it means "
                    f"(dbt-project-evaluator's `{rule}`)."),
            evidence={"fact": "model_has_no_description", **_ev([rule], 1)}))


# The directory rules: (column naming the node, column naming its layer) per rule.
_DIRECTORY_COLUMNS = {"fct_model_directories": ("child", "child_model_type"),
                      "fct_source_directories": ("resource_name", "resource_type"),
                      "fct_test_directories": ("model_name", "")}


def _directories(rep: Reading, names: _Names, skip: set) -> list:
    """What is left of the directory rules once a layer-wide mismatch went to a config card."""
    from .checks.structural import Finding
    out = []
    for rule, (col, _t) in _DIRECTORY_COLUMNS.items():
        for r in rep.rows.get(rule, []):
            uid = names.of(r.get(col))
            if uid is None or uid in skip:
                continue
            label = names.label(uid)
            where = (r.get("child_directory_path") or r.get("current_file_path")
                     or r.get("model_directory_path") or "")
            out.append(Finding(
                check="file_in_unexpected_directory", subject=uid, subject_name=label, file="",
                base=1,
                summary=f"`{label}` is not where the evaluator's layout expects it",
                detail=(f"In `{where}` (dbt-project-evaluator's `{rule}`)."
                        + (f" Suggested: `{r.get('change_file_path_to')}`."
                           if r.get("change_file_path_to") else "")),
                evidence={"fact": "directory", "rule": rule, **_ev([rule], 1)}))
    return out


HANDLED = {*RAW_SOURCE_RULES, "fct_missing_primary_key_tests", "fct_undocumented_models",
           "fct_undocumented_public_models", "fct_model_fanout", "fct_too_many_joins",
           "fct_root_models", "fct_model_naming_conventions", "fct_staging_dependent_on_staging",
           "fct_staging_dependent_on_marts_or_intermediate", "fct_rejoining_of_upstream_concepts",
           "fct_hard_coded_references", "fct_chained_views_dependencies",
           "fct_public_models_without_contract", "fct_sources_without_freshness",
           "fct_unused_sources", "fct_undocumented_sources", "fct_undocumented_source_tables",
           "fct_duplicate_sources", "fct_exposures_dependent_on_private_models",
           "fct_exposure_parents_materializations", *_DIRECTORY_COLUMNS}


def _unrecognised(rep: Reading, names: _Names, skip: set) -> list:
    """A rule this version of assay has no card for is still carried, one card per node it
    names, rather than read and dropped: a newer evaluator adds rules."""
    from .checks.structural import Finding
    out = []
    for rule in sorted(set(rep.rows) - HANDLED - SUMMARY_RULES):
        for r in rep.rows[rule]:
            name = next((r.get(c) for c in ("resource_name", "child", "model", "parent",
                                             "source_name") if r.get(c)), None)
            uid = names.of(name)
            if uid is None or uid in skip:
                continue
            label = names.label(uid)
            out.append(Finding(
                check="evaluator_rule", subject=uid, subject_name=label, file="", base=1,
                summary=f"dbt-project-evaluator's `{rule}` flags `{label}`",
                detail=("A rule this version of assay has no card of its own for; the row is "
                        "carried as the evaluator wrote it: "
                        + ", ".join(f"{k}={v}" for k, v in r.items() if v not in (None, ""))[:400]),
                evidence={"fact": "rule", "rule": rule, **_ev([rule], 1)}))
    return out


def _per_node(rep: Reading, names: _Names, skip: set, grains: dict | None = None) -> list:
    """The rules that are one card per node already, each under its own check name.

    `grains` is {model name: [columns]}, the grain assay inferred from the SQL, which is what
    turns "add a primary key test" into "a test on these columns"."""
    from .checks.structural import Finding
    out = []
    grains = grains or {}

    def pk_detail(m, r) -> str:
        cols = grains.get(m.name)
        head = (f"{r.get('number_of_tests_on_model') or 0} test(s) on the model, none of them a "
                f"uniqueness test on a key (dbt-project-evaluator's "
                f"`fct_missing_primary_key_tests`). ")
        if cols:
            return head + (f"assay reads the grain from the SQL as ({', '.join(cols)}): a "
                           f"`unique_combination_of_columns` test on those is the test to add. "
                           f"`assay practices --keys-only` counts it against the warehouse first.")
        return head + ("assay could not read a grain from the SQL either, so nothing yet says "
                       "what one row of it is.")

    def model_card(rule, name_col, build):
        for r in rep.rows.get(rule, []):
            uid = names.of(r.get(name_col))
            if uid is None or uid in skip or uid not in names.project.models:
                continue
            m = names.project.models[uid]
            out.append(build(uid, m, r))

    model_card("fct_missing_primary_key_tests", "resource_name", lambda uid, m, r: Finding(
        check="no_primary_key_test", subject=uid, subject_name=m.name, file=m.path, base=2,
        summary=f"`{m.name}` has no test saying what one row is",
        detail=pk_detail(m, r),
        evidence={"fact": "no_primary_key_test",
                  **_ev(["fct_missing_primary_key_tests"], 1, grain=grains.get(m.name) or [])}))
    for rule in ("fct_undocumented_models", "fct_undocumented_public_models"):
        _undocumented(rep, names, skip, rule, out)
    model_card("fct_model_fanout", "parent", lambda uid, m, r: Finding(
        check="model_has_many_leaf_children", subject=uid, subject_name=m.name, file=m.path,
        base=1,
        summary=f"`{m.name}` feeds several leaf models directly",
        detail=(f"Leaf children: {r.get('leaf_children')}. A shared model with many consumers is "
                f"often the point of it; several leaves each finishing the same work is the case "
                f"worth a look (dbt-project-evaluator's `fct_model_fanout`)."),
        evidence={"fact": "model_fanout", **_ev(["fct_model_fanout"], 1)}))
    model_card("fct_too_many_joins", "resource_name", lambda uid, m, r: Finding(
        check="too_many_joins", subject=uid, subject_name=m.name, file=m.path, base=1,
        summary=f"`{m.name}` joins an unusual number of relations in one model",
        detail=(f"{r.get('join_count')} joins (dbt-project-evaluator's `fct_too_many_joins`). "
                f"Each join is a place a row can multiply or drop; assay's own join checks say "
                f"which of them do."),
        evidence={"fact": "too_many_joins", **_ev(["fct_too_many_joins"], 1)}))
    model_card("fct_root_models", "child", lambda uid, m, r: Finding(
        check="model_refs_nothing", subject=uid, subject_name=m.name, file=m.path, base=1,
        summary=f"`{m.name}` refs nothing: no ref() and no source()",
        detail=("Its lineage starts from nothing dbt knows about, so every check that follows "
                "lineage stops here (dbt-project-evaluator's `fct_root_models`)."),
        evidence={"fact": "root_model", **_ev(["fct_root_models"], 1)}))
    model_card("fct_model_naming_conventions", "resource_name", lambda uid, m, r: Finding(
        check="model_name_breaks_convention", subject=uid, subject_name=m.name, file=m.path,
        base=1,
        summary=f"`{m.name}` does not carry a {r.get('model_type')} prefix",
        detail=(f"The evaluator classes it as {r.get('model_type')} and expects one of: "
                f"{r.get('appropriate_prefixes')} (dbt-project-evaluator's "
                f"`fct_model_naming_conventions`)."),
        evidence={"fact": "naming", **_ev(["fct_model_naming_conventions"], 1)}))
    for rule, check_text in (("fct_staging_dependent_on_staging", "staging"),
                             ("fct_staging_dependent_on_marts_or_intermediate", "downstream")):
        for r in rep.rows.get(rule, []):
            uid = names.of(r.get("child"))
            if uid is None or uid in skip or uid not in names.project.models:
                continue
            m = names.project.models[uid]
            parent = r.get("parent")
            if check_text == "staging":
                out.append(Finding(
                    check="staging_reads_staging", subject=uid, subject_name=m.name,
                    file=m.path, base=1,
                    summary=f"staging model `{m.name}` reads another staging model",
                    detail=(f"It reads `{parent}` (dbt-project-evaluator's `{rule}`). A staging "
                            f"model is one source made usable; reading another staging model "
                            f"is usually an intermediate model in the wrong folder."),
                    evidence={"fact": "staging_on_staging", "parent": str(parent),
                              **_ev([rule], 1)}))
            else:
                out.append(Finding(
                    check="staging_reads_downstream", subject=uid, subject_name=m.name,
                    file=m.path, base=3,
                    summary=f"staging model `{m.name}` reads a model downstream of staging",
                    detail=(f"It reads `{parent}` (dbt-project-evaluator's `{rule}`). A layer "
                            f"reading the layers built on top of it is a cycle in intent."),
                    evidence={"fact": "staging_on_downstream", "parent": str(parent),
                              **_ev([rule], 1)}))
    rejoined: dict = {}
    for r in rep.rows.get("fct_rejoining_of_upstream_concepts", []):
        uid = names.of(r.get("child"))
        if uid is None or uid in skip or uid not in names.project.models:
            continue
        rejoined.setdefault(uid, []).append((str(r.get("parent")), str(r.get("parent_and_child"))))
    for uid, pairs in rejoined.items():
        m = names.project.models[uid]
        said = "; ".join(f"`{p}` again, already reaching it through `{via}`"
                         for p, via in sorted(set(pairs))[:6])
        out.append(Finding(
            check="rejoins_an_upstream_concept", subject=uid, subject_name=m.name, file=m.path,
            base=1,
            summary=f"`{m.name}` joins relations that already reach it through another model",
            detail=(f"It joins {said}"
                    + (f"; and {len(set(pairs)) - 6} more" if len(set(pairs)) > 6 else "") + ". "
                    "The same relation arrives twice, once directly and once through a model in "
                    "between, so whatever that model did to it is undone or duplicated here "
                    "(dbt-project-evaluator's `fct_rejoining_of_upstream_concepts`)."),
            evidence={"fact": "rejoin",
                      **_ev(["fct_rejoining_of_upstream_concepts"], len(pairs),
                            parents=sorted({p for p, _v in pairs}))}))
    for r in rep.rows.get("fct_hard_coded_references", []):
        uid = names.of(r.get("model") or r.get("resource_name"))
        if uid is None or uid in skip or uid not in names.project.models:
            continue
        m = names.project.models[uid]
        out.append(Finding(
            check="hard_coded_reference", subject=uid, subject_name=m.name, file=m.path, base=3,
            summary=f"`{m.name}` names a table directly instead of through ref() or source()",
            detail=(f"{r.get('hard_coded_references')} (dbt-project-evaluator's "
                    f"`fct_hard_coded_references`). dbt cannot see that dependency, so it is "
                    f"missing from lineage, and every check that follows lineage misses it too."),
            evidence={"fact": "hard_coded", **_ev(["fct_hard_coded_references"], 1)}))
    for r in rep.rows.get("fct_chained_views_dependencies", []):
        path = r.get("path")
        path = path if isinstance(path, list) else _split(str(path or "").strip("[]"))
        uid = names.of(str(path[-1]).strip("'\" ") if path else r.get("parent"))
        if uid is None or uid in skip or uid not in names.project.models:
            continue
        m = names.project.models[uid]
        out.append(Finding(
            check="long_chain_of_views", subject=uid, subject_name=m.name, file=m.path, base=1,
            summary=f"`{m.name}` sits at the end of a long chain of views",
            detail=(f"{r.get('distance')} views deep from `{r.get('parent')}` "
                    f"(dbt-project-evaluator's `fct_chained_views_dependencies`): every read "
                    f"re-computes the whole chain."),
            evidence={"fact": "chained_views", **_ev(["fct_chained_views_dependencies"], 1)}))
    for r in rep.rows.get("fct_public_models_without_contract", []):
        uid = names.of(r.get("resource_name"))
        if uid is None or uid in skip or uid not in names.project.models:
            continue
        m = names.project.models[uid]
        out.append(Finding(
            check="public_model_without_contract", subject=uid, subject_name=m.name,
            file=m.path, base=1,
            summary=f"`{m.name}` is public and enforces no contract",
            detail=("Other projects may depend on it, and nothing stops its columns changing "
                    "under them (dbt-project-evaluator's `fct_public_models_without_contract`)."),
            evidence={"fact": "public_no_contract",
                      **_ev(["fct_public_models_without_contract"], 1)}))
    return out


def _per_source(rep: Reading, names: _Names, skip: set) -> list:
    from .checks.structural import Finding
    out = []
    for r in rep.rows.get("fct_sources_without_freshness", []):
        uid = names.of(r.get("resource_name"))
        s = names.project.sources.get(uid or "")
        if s is None or uid in skip:
            continue
        # The same check name, summary and evidence assay's own `source_freshness_undeclared`
        # writes, so a ruling on either is a ruling on both.
        out.append(Finding(
            check="source_freshness_undeclared", subject=uid,
            subject_name=f"{s.source_name}.{s.name}", file="",
            summary=f"`{s.source_name}.{s.name}` declares no freshness",
            detail=("Nothing says how current this is supposed to be, so `dbt source freshness` "
                    "cannot check it and a feed going quiet would look exactly like a feed that "
                    "is up to date. Flagged by dbt-project-evaluator's "
                    "`fct_sources_without_freshness`."),
            base=1, evidence={"source": s.source_name, "table": s.name,
                              **_ev(["fct_sources_without_freshness"], 1)}))
    for r in rep.rows.get("fct_unused_sources", []):
        uid = names.of(r.get("parent") or r.get("resource_name"))
        s = names.project.sources.get(uid or "")
        if s is None or uid in skip:
            continue
        out.append(Finding(
            check="source_is_never_used", subject=uid,
            subject_name=f"{s.source_name}.{s.name}", file="", base=1,
            summary=f"`{s.source_name}.{s.name}` is declared and nothing reads it",
            detail=("Declared, and no model selects from it (dbt-project-evaluator's "
                    "`fct_unused_sources`). Either a model is missing or the declaration is."),
            evidence={"fact": "unused_source", **_ev(["fct_unused_sources"], 1)}))
    for rule, col in (("fct_undocumented_sources", "source_name"),
                      ("fct_undocumented_source_tables", "resource_name")):
        for r in rep.rows.get(rule, []):
            name = str(r.get(col) or r.get("resource_name") or "")
            uids = ([names.of(name)] if names.of(name) else
                    names.source_group.get(name.lower(), []))
            uids = [u for u in uids if u and u not in skip]
            if not uids:
                continue
            first = names.project.sources[uids[0]]
            out.append(Finding(
                check="source_has_no_description", subject=uids[0],
                subject_name=name, file="", base=1,
                summary=f"source `{name}` has no description",
                detail=(f"Nothing says what `{name}` is or who loads it "
                        f"(dbt-project-evaluator's `{rule}`)."
                        + (f" It declares {len(uids)} table(s), `{first.name}` among them."
                           if len(uids) > 1 else "")),
                evidence={"fact": "undocumented_source", "name": name, **_ev([rule], 1)}))
    for r in rep.rows.get("fct_duplicate_sources", []):
        name = str(r.get("resource_name") or r.get("source_name") or "")
        uid = names.of(name) or next(iter(names.source_group.get(name.split(".")[0].lower(), [])),
                                      None)
        if uid is None or uid in skip:
            continue
        out.append(Finding(
            check="source_declared_twice", subject=uid, subject_name=name, file="", base=2,
            summary=f"the table behind `{name}` is declared as a source more than once",
            detail=("Two names for one table drift apart: a fix or a test lands on one name and "
                    "not the other (dbt-project-evaluator's `fct_duplicate_sources`)."),
            evidence={"fact": "duplicate_source", **_ev(["fct_duplicate_sources"], 1)}))
    return out


def _per_exposure(rep: Reading, names: _Names, skip: set) -> list:
    """ONE card per exposure: a decision about the exposure, not about each of 73 models."""
    from .checks.structural import Finding
    out = []
    for rule, check_kind in (("fct_exposures_dependent_on_private_models", "private"),
                             ("fct_exposure_parents_materializations", "views")):
        by: dict = {}
        for r in rep.rows.get(rule, []):
            uid = names.of(r.get("parent_resource_name"))
            if uid is not None and uid in skip:
                continue
            by.setdefault(str(r.get("exposure_name") or ""), []).append(
                str(r.get("parent_resource_name") or ""))
        for exp_name, parents in sorted(by.items()):
            exp_uid = next((u for u, e in names.project.exposures.items()
                            if e.name == exp_name), f"exposure.{exp_name}")
            parents = sorted(set(parents))
            listed = ", ".join(parents[:10]) + (f" and {len(parents) - 10} more"
                                                if len(parents) > 10 else "")
            if check_kind == "private":
                out.append(Finding(
                    check="exposure_rests_on_private_models", subject=exp_uid,
                    subject_name=exp_name, file="", base=2,
                    summary=f"exposure `{exp_name}` depends on models not marked public",
                    detail=(f"{len(parents)} model(s): {listed}. Something outside the warehouse "
                            f"rests on models whose access says they are not meant to be "
                            f"depended on (dbt-project-evaluator's `{rule}`). One decision for "
                            f"the exposure: mark them public, or accept this."),
                    evidence={"fact": "exposure_private", **_ev([rule], len(parents),
                                                                models=parents[:60])}))
            else:
                out.append(Finding(
                    check="exposure_rests_on_views", subject=exp_uid, subject_name=exp_name,
                    file="", base=1,
                    summary=f"exposure `{exp_name}` reads models built as views",
                    detail=(f"{len(parents)} view(s): {listed}. Each read of the exposure "
                            f"re-computes them (dbt-project-evaluator's `{rule}`)."),
                    evidence={"fact": "exposure_views", **_ev([rule], len(parents),
                                                              models=parents[:60])}))
    return out


def _config_cards(rep: Reading, names: _Names) -> tuple[list, set, int]:
    """The evaluator's configuration, where its rows say more about it than about the project.

    Returns (cards, node uids whose rows they absorb, rows absorbed)."""
    from .checks.structural import Finding
    out, absorbed, n_rows = [], set(), 0
    # 1. rows about nodes that belong to an installed package
    pkg_rows: dict = {}
    for rule, rows in rep.rows.items():
        for r in rows:
            for col in ("resource_name", "child", "parent", "model"):
                uid = names.of(r.get(col))
                pkg = names.foreign(uid)
                if pkg:
                    pkg_rows.setdefault(pkg, []).append((rule, uid))
                    break
    if pkg_rows:
        pkgs = sorted(pkg_rows)
        n = sum(len(v) for v in pkg_rows.values())
        for v in pkg_rows.values():
            absorbed.update(u for _r, u in v)
        n_rows += n
        out.append(Finding(
            check="evaluator_config_does_not_fit", subject="", subject_name="dbt_project_evaluator",
            file="dbt_project.yml", base=2,
            summary="dbt-project-evaluator is judging installed packages' models",
            detail=(f"{n:,} evaluator row(s) are about models from {', '.join(pkgs)}, which this "
                    f"project does not own and cannot rename. Set `exclude_packages: ['all']` "
                    f"(or name them) under `vars: dbt_project_evaluator:` in dbt_project.yml. "
                    f"Those rows are not listed as cards; this is the one thing to change."),
            evidence={"fact": "packages_not_excluded", "packages": pkgs,
                      **_ev(sorted({r for v in pkg_rows.values() for r, _u in v}), n)}))
    # 2. a convention rule firing on most of a layer
    total: dict = {}
    for g in rep.graph:
        if str(g.get("resource_type") or "") != "model" or g.get("is_excluded") in (True, "true"):
            continue
        if names.foreign(names.of(g.get("resource_name"))):
            continue
        total[str(g.get("model_type") or "")] = total.get(str(g.get("model_type") or ""), 0) + 1
    for rule, setting_text in CONVENTION_SETTING.items():
        per_type: dict = {}
        col, tcol = _DIRECTORY_COLUMNS.get(rule, ("resource_name", "model_type"))
        for r in rep.rows.get(rule, []):
            uid = names.of(r.get(col))
            if uid in absorbed:
                continue
            per_type.setdefault(str(r.get(tcol) or "") if tcol else "", []).append(uid)
        for t, uids in sorted(per_type.items()):
            of = total.get(t, 0)
            if not of or len(uids) < MIN_LAYER or len(uids) / of <= MOST:
                continue
            absorbed.update(u for u in uids if u)
            n_rows += len(uids)
            setting = setting_text.format(t=t or "this")
            out.append(Finding(
                check="evaluator_config_does_not_fit", subject="",
                subject_name="dbt_project_evaluator", file="dbt_project.yml", base=2,
                summary=f"`{rule}` fails most {t} models: its convention is not this project's",
                detail=(f"{len(uids):,} of {of:,} {t} model(s). A rule that fails most of a "
                        f"layer describes the evaluator's configuration, not the project. Set "
                        f"{setting} to what this project does. The rows are not listed as "
                        f"cards."),
                evidence={"fact": "convention_mismatch", "rule": rule, "model_type": t,
                          **_ev([rule], len(uids), models=len(uids), of=of)}))
    return out, absorbed, n_rows


def cards(project, rep: Reading, grains: dict | None = None) -> tuple[list, Tally]:
    """The evaluator's rows as findings, before folding into assay's own. (findings, tally)

    `grains` is {model name: [columns]} from `practices.primary_key_patches`, when the caller
    has the inventory."""
    names = _Names(project)
    config, absorbed, n_config = _config_cards(rep, names)
    raw_cards, leftover = _reads_raw(rep, names, absorbed)
    fan_rows = {str(r.get("parent") or ""): 1 for r in rep.rows.get("fct_source_fanout", [])}
    out = (config + raw_cards + _source_fanout_leftover(leftover, names, fan_rows)
           + _per_node(rep, names, absorbed, grains) + _per_source(rep, names, absorbed)
           + _per_exposure(rep, names, absorbed) + _directories(rep, names, absorbed)
           + _unrecognised(rep, names, absorbed))
    for f in out:
        if f.subject in project.models or f.subject in project.sources:
            b = project.blast_radius(f.subject)
            f.descendants, f.marts = b["descendants"], b["marts"]
            f.exposures = b.get("exposures") or []
    on_cards = sum(int(((f.evidence or {}).get("evaluator") or {}).get("rows", 0)) for f in out)
    t = Tally(rows=rep.total, cards=len(out), config=n_config,
              unmatched=max(0, rep.total - on_cards),
              by_rule={k: len(v) for k, v in rep.rows.items() if k not in SUMMARY_RULES})
    return out, t


def fold(assay_findings: list, ev_cards: list, tally: Tally) -> list:
    """Cards not already said by assay. The rest name their rule on assay's card instead."""
    have: dict = {}
    for f in assay_findings:
        have.setdefault((f.subject, f.check), []).append(f)
    out = []
    for c in ev_cards:
        targets = MERGES_INTO.get(c.check, ())
        hit = next((f for t in targets for f in have.get((c.subject, t), [])), None)
        if hit is None:
            out.append(c)
            continue
        ev = (c.evidence or {}).get("evaluator") or {}
        mine = hit.evidence.setdefault("evaluator", {"also_flagged_by": [], "rows": 0})
        mine["also_flagged_by"] = sorted(set(mine.get("also_flagged_by", []))
                                         | set(ev.get("rules", [])))
        mine["rows"] = int(mine.get("rows", 0)) + int(ev.get("rows", 1))
        rules = ", ".join(f"`{r}`" for r in ev.get("rules", []))
        if rules and rules not in hit.detail:
            hit.detail += f"\n\ndbt-project-evaluator flags this too ({rules})."
        tally.cards -= 1
        tally.folded += int(ev.get("rows", 1))
    return out


def judged_readings(store, ev_cards: list) -> int:
    """Put the stored `assay practices` reading on each adjudicated card. Returns how many.

    Annotation only: the card stays, with the reading in its detail and evidence. The exception
    itself is an accept or a waiver in the form."""
    if store is None:
        return 0
    todo = {f"practice::{f.check}::{f.subject_name}": f for f in ev_cards
            if f.check in ADJUDICATED}
    if not todo:
        return 0
    try:
        rows = store.live_decisions("decision_key like 'practice::%'", [],
                                    columns="question, answer, confidence, decision_key")
    except Exception:                                            # noqa: BLE001
        return 0
    n = 0
    for _q, answer, conf, key in rows:
        f = todo.get(key)
        if f is None or not answer:
            continue
        ev = f.evidence.setdefault("evaluator", {})
        ev["judged"] = {"answer": answer, "confidence": conf}
        f.detail += (f"\n\nJudged by `assay practices`: {str(answer).replace('_', ' ')}"
                     + (f" ({conf:.2f})" if isinstance(conf, (int, float)) else "") + ".")
        n += 1
    return n


def surface_counts(findings) -> dict | None:
    """{rows, cards, folded} for a surface holding findings, or None without evaluator cards."""
    rows = cards = folded = 0
    for f in findings:
        ev = ((f.get("evidence") if isinstance(f, dict) else f.evidence) or {}).get("evaluator")
        if not ev:
            continue
        if ev.get("rules"):
            cards += 1
            rows += int(ev.get("rows") or 0)
        elif ev.get("also_flagged_by"):
            folded += int(ev.get("rows") or 0)
    if not cards and not folded:
        return None
    return {"rows": rows + folded, "cards": cards, "folded": folded}


def surface_line(findings) -> str:
    """The rows-to-cards sentence for a surface holding findings (the page, the form), computed
    from the cards themselves, so it counts what that surface lists. "" without evaluator cards.
    `findings` is Finding objects or dicts with `evidence`."""
    rows = cards = folded = 0
    for f in findings:
        ev = ((f.get("evidence") if isinstance(f, dict) else f.evidence) or {}).get("evaluator")
        if not ev:
            continue
        if ev.get("rules"):
            cards += 1
            rows += int(ev.get("rows") or 0)
        elif ev.get("also_flagged_by"):
            folded += int(ev.get("rows") or 0)
    if not cards and not folded:
        return ""
    return (f"dbt-project-evaluator: {rows + folded:,} row(s) here, on {cards:,} card(s) of its "
            f"own" + (f" and {folded:,} on assay's own findings about the same thing"
                      if folded else "") + " (one card per subject and fact).")
