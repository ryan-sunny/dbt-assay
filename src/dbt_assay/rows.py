"""Adjudicating the rows a deterministic rule flagged.

*** dbt ALREADY BUILT THE CANDIDATE GENERATOR. ***
`store_failures` writes every failing row to `dbt_test__audit`. That IS the population this layer
needs, produced for free, by code, with the exact division of labor the design calls for: the
cheap exact check narrows, the judgment adjudicates what survives.

*** IT CHANGES WHAT A dbt TEST IS FOR. ***
A test returning 3,229 rows is a test switched off within a week. With adjudication behind it, it
returns 3,229 rows, the explanation says most are normal for their kind, and a handful reach a
person. The assertion stops being a gate and becomes a candidate generator, which is the only job
it was ever good at.

*** THE OPTIONS ARE THE DOMAIN KNOWLEDGE AND THERE IS ONE SET PER MART. ***
A generic set ships. `explanations:` in audit.yml replaces it per model, and that file is where the
real work lives: a terse rewrite of one criterion has flipped an answer before.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import QUESTIONS
from .jev import choice, noul

EXPL_Q = QUESTIONS["row_explanation"]
COH_Q = QUESTIONS["row_is_internally_coherent"]
EXPL_VERSION = EXPL_Q["prompt_version"]
COH_VERSION = COH_Q["prompt_version"]

MAX_ROWS_PER_TEST = 25


@dataclass
class FailingRow:
    test_name: str
    model: str
    model_uid: str
    relation: str
    row: dict
    rule: str = ""
    purpose: str | None = None
    grain: list = field(default_factory=list)


def audit_relation(project, test_uid: str) -> str | None:
    """Where dbt stored this test's failures, from the manifest's own fields."""
    n = (project.raw.get("nodes", {}) or {}).get(test_uid) or {}
    if not ((n.get("config") or {}).get("store_failures")):
        return None
    rel = n.get("relation_name")
    if rel:
        return rel.replace('"', "")
    ident = n.get("alias") or n.get("name")
    parts = [n.get("database"), n.get("schema"), ident]
    return ".".join(p for p in parts if p) or None


def describes(project, test_uid: str) -> str:
    n = (project.raw.get("nodes", {}) or {}).get(test_uid) or {}
    meta = n.get("test_metadata") or {}
    col = n.get("column_name") or (meta.get("kwargs") or {}).get("column_name")
    kind = meta.get("name") or "a test"
    return f"{kind} on `{col}`" if col else kind


def options_for(model: str, config_explanations: dict | None) -> dict:
    """Per-mart options where a project supplies them, the generic set otherwise."""
    custom = (config_explanations or {}).get(model)
    if not custom:
        return EXPL_Q["criteria"]
    out = dict(EXPL_Q["criteria"])
    for name, desc in custom.items():
        out[name] = {"what": desc} if isinstance(desc, str) else desc
    return out


def build_state(fr: FailingRow, vocab: dict | None = None) -> dict:
    state = {
        "model": fr.model,
        "model_purpose": fr.purpose,
        "model_grain": fr.grain or None,
        "rule": fr.rule,
        "row": {k: v for k, v in fr.row.items() if v is not None},
    }
    if vocab:
        state["vocabulary"] = vocab
    return {k: v for k, v in state.items() if v not in (None, [], {})}


def questions_for(fr: FailingRow, config_explanations: dict | None = None) -> dict:
    return {
        "explanation": choice(EXPL_Q["instructions"], options_for(fr.model, config_explanations)),
        # Beside it, never instead of it: coherence detects, explanation excuses.
        "coherent": noul(COH_Q["instructions"], COH_Q["criteria"]["true"],
                         COH_Q["criteria"]["false"]),
    }


def which_have_failures(relations: list[str], probe_mod, project_dir: str,
                        profiles_dir: str | None, dbt_bin: str,
                        batch: int = 200) -> tuple[set[str], list[str]]:
    """*** NARROW IN ONE QUERY, NOT ONE QUERY PER TEST. ***

    Reported from a project that turns `store_failures` on globally: this loop shelled a COLD
    `dbt show --inline` for each of 1,288 tests at roughly fifteen seconds each -- five and a half
    hours -- and thirteen of those tables held a single row between them. Every other call paid a
    dbt startup to be told a table was empty.

    A `union all` of counts answers the same question for hundreds of relations in one statement.
    A relation that does not exist would fail the whole batch, so a failed batch is retried in
    halves and a single relation that still fails is recorded as UNKNOWN rather than as empty:
    silence has to be distinguishable from absence, which is the rule this tool is built on.

    Returns (relations_with_rows, relations_we_could_not_read).
    """
    have: set[str] = set()
    unknown: list[str] = []

    def ask(chunk: list[str]) -> bool:
        sql = " union all ".join(
            f"select '{r}' as rel, count(*) as n from {r}" for r in chunk)
        got = probe_mod.run_sql(sql, project_dir, profiles_dir, dbt_bin, limit=len(chunk) + 1,
                                caller="assay.rows.which_have_failures", kind="count")
        if got.failed:
            return False
        for row in got.rows:
            vals = list(row.values())
            rel = row.get("rel", vals[0] if vals else None)
            n = row.get("n", vals[1] if len(vals) > 1 else 0)
            try:
                if rel and int(n) > 0:
                    have.add(str(rel))
            except (TypeError, ValueError):
                continue
        return True

    def walk(chunk: list[str]) -> None:
        if not chunk:
            return
        if ask(chunk):
            return
        if len(chunk) == 1:
            unknown.append(chunk[0])          # never counted clean
            return
        mid = len(chunk) // 2
        walk(chunk[:mid])
        walk(chunk[mid:])

    rels = sorted(set(relations))
    for i in range(0, len(rels), batch):
        walk(rels[i:i + batch])
    return have, unknown


def collect(project, entries, probe_mod, project_dir: str, profiles_dir: str | None,
            dbt_bin: str, limit_per_test: int = MAX_ROWS_PER_TEST) -> tuple[list, list]:
    """(rows, skipped). A test whose failures were never stored is skipped, not counted clean."""
    by_uid = {e.uid: e for e in entries}
    rows, skipped = [], []

    # One pass to find which audit tables hold anything, then one query per table that does.
    candidates = {}
    for t in project.tests:
        rel = audit_relation(project, t.unique_id)
        if not rel:
            skipped.append((t.name, "store_failures is off for this test"))
            continue
        if not t.tests_model or t.tests_model not in project.models:
            continue
        candidates[t.unique_id] = (t, rel)
    have, unknown = which_have_failures([r for _t, r in candidates.values()], probe_mod,
                                        project_dir, profiles_dir, dbt_bin)

    for t, rel in candidates.values():
        if rel in unknown:
            skipped.append((t.name, "the audit table could not be read; NOT counted as clean"))
            continue
        if rel not in have:
            skipped.append((t.name, "the audit table is empty: this test stored no failures"))
            continue
        got = probe_mod.run_sql(f"select * from {rel}", project_dir, profiles_dir, dbt_bin,
                                limit=limit_per_test, caller="assay.rows.collect",
                                kind="metadata", relation=rel)
        if got.failed or not got.rows:
            # The count above said this table holds rows, so either reading them failed or they
            # went away between the two statements. Both are "not read", and neither is clean.
            skipped.append((t.name, got.why[:120] if got.failed
                            else "counted rows but could not read them"))
            continue
        e = by_uid.get(t.tests_model)
        for r in got.rows[:limit_per_test]:
            rows.append(FailingRow(
                test_name=t.name, model=project.models[t.tests_model].name,
                model_uid=t.tests_model, relation=rel, row=r,
                rule=describes(project, t.unique_id),
                purpose=(e.columns and None) or None,
                grain=(e.grain.value if e and e.grain else []),
            ))
    return rows, skipped
