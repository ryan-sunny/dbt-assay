"""Adjudicating the rows a deterministic rule flagged.

*** dbt ALREADY BUILT THE CANDIDATE GENERATOR. ***
`store_failures` writes every failing row to `dbt_test__audit`. That IS the population this layer
needs, produced for free, by code, with the exact division of labour the design calls for: the
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


def collect(project, entries, probe_mod, project_dir: str, profiles_dir: str | None,
            dbt_bin: str, limit_per_test: int = MAX_ROWS_PER_TEST) -> tuple[list, list]:
    """(rows, skipped). A test whose failures were never stored is skipped, not counted clean."""
    by_uid = {e.uid: e for e in entries}
    rows, skipped = [], []
    for t in project.tests:
        rel = audit_relation(project, t.unique_id)
        if not rel:
            skipped.append((t.name, "store_failures is off for this test"))
            continue
        if not t.tests_model or t.tests_model not in project.models:
            continue
        got = probe_mod.run_sql(f"select * from {rel}", project_dir, profiles_dir, dbt_bin,
                                limit=limit_per_test)
        if not got:
            skipped.append((t.name, "no stored failures, or the audit table is absent"))
            continue
        e = by_uid.get(t.tests_model)
        for r in got[:limit_per_test]:
            rows.append(FailingRow(
                test_name=t.name, model=project.models[t.tests_model].name,
                model_uid=t.tests_model, relation=rel, row=r,
                rule=describes(project, t.unique_id),
                purpose=(e.columns and None) or None,
                grain=(e.grain.value if e and e.grain else []),
            ))
    return rows, skipped
