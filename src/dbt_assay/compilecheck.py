"""Was this SQL compiled against a warehouse, or rendered blind?

*** A COMPILE WITHOUT A CONNECTION PRODUCES WRONG SQL THAT LOOKS RIGHT. ***
A macro that asks the warehouse what columns a relation has -- `adapter.get_columns_in_relation`,
`dbt_utils.star`, `run_query` -- is guarded on `execute`, and with no connection it gets nothing
back. On the field project that turns every `dlt_col(...)` into `cast(null as varchar)`: valid SQL,
every value NULL, and assay then reads and judges it with nothing anywhere saying it was built
blind. `onboard` already names "no compiled SQL" as a degradation; this names the worse case, SQL
that exists and is not the real thing.

*** IT READS THE SHAPE, AND SAYS WHAT IT CANNOT KNOW. ***
Which macros introspect is read from the manifest's own macro bodies, following macros that call
macros. A model built on one is flagged when its compiled SQL carries the shape an empty answer
leaves: nearly every output column a NULL placeholder, `in ()`, a select list with nothing in it,
or SQL that no longer parses. A connected compile can legitimately null SOME columns, so it takes
nearly all of them. A model that introspects and shows none of these is not proven connected --
only not caught -- and the count of those is reported too.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_INTROSPECTS = re.compile(
    r"adapter\s*\.\s*(get_columns_in_relation|get_relation|get_missing_columns|"
    r"get_columns_in_table)|\brun_query\s*\(|dbt_utils\s*\.\s*(star|get_column_values|"
    r"get_filtered_columns_in_relation|union_relations|get_relations_by_pattern)\b|"
    r"\bget_column_values\s*\(")
_EMPTY_IN = re.compile(r"\bin\s*\(\s*\)", re.IGNORECASE)
_EMPTY_SELECT = re.compile(r"\bselect\s*(distinct\s*)?(,|\bfrom\b)", re.IGNORECASE)
_NULL_SHARE = 0.8
# Quoted identifiers handed to a macro inside `{{ }}`: `dlt_col(present, 'parcel_id')`.
_CALL = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_QUOTED = re.compile(r"'([A-Za-z_][A-Za-z0-9_]*)'")
_FN = re.compile(r"\b(\w+)\s*\(([^()]*)\)")
# Under this share of looked-up columns actually referenced, the lookup answered "none exist".
_RESOLVED_SHARE = 0.2


@dataclass
class Blind:
    model: str
    uid: str
    why: str


def introspective_macros(raw: dict) -> set[str]:
    """Macro unique_ids that ask the warehouse, directly or through another macro."""
    macros = (raw or {}).get("macros") or {}
    direct = {uid for uid, m in macros.items() if _INTROSPECTS.search(m.get("macro_sql") or "")}
    out = set(direct)
    changed = True
    while changed:
        changed = False
        for uid, m in macros.items():
            if uid in out:
                continue
            if set((m.get("depends_on") or {}).get("macros") or []) & out:
                out.add(uid)
                changed = True
    return out


def blind_models(project, digests, catalog: dict | None = None) -> tuple[list[Blind], int]:
    """(models whose compiled SQL shows an introspection that got nothing back, how many
    introspecting models were read at all)."""
    raw = dict(getattr(project, "raw", {}) or {})
    raw["_catalog"] = catalog
    intro = introspective_macros(raw)
    if not intro:
        return [], 0
    nodes = raw.get("nodes") or {}
    found, looked = [], 0
    for uid, m in project.models.items():
        uses = set(((nodes.get(uid) or {}).get("depends_on") or {}).get("macros") or [])
        if not uses & intro or not m.readable:
            continue
        looked += 1
        sql = m.compiled or ""
        d = (digests or {}).get(uid)
        why = ""
        if d is not None and not d.ok:
            why = "its compiled SQL does not parse"
        elif _EMPTY_IN.search(sql):
            why = "its compiled SQL has an empty `in ()`"
        elif _EMPTY_SELECT.search(sql):
            why = "its compiled SQL has a select list with nothing in it"
        elif d is not None and (miss := _unresolved(nodes.get(uid) or {}, d, catalog_cols(
                raw.get("_catalog"), nodes.get(uid) or {}))):
            why = miss
        elif d is not None:
            roots = d.output_roots or {}
            cols = list(roots)
            nulls = [c for c in cols if str(roots.get(c) or "").startswith("null")]
            if len(cols) >= 3 and len(nulls) / len(cols) >= _NULL_SHARE:
                why = (f"{len(nulls)} of {len(cols)} output columns are a NULL placeholder, "
                       f"which is what an introspecting macro writes when it is told the "
                       f"relation has no columns")
        if why:
            found.append(Blind(m.name, uid, why))
    return found, looked


def catalog_cols(catalog: dict | None, node: dict) -> set:
    """The columns `catalog.json` records for this model's parents, lowercased."""
    out: set = set()
    for parent in ((node.get("depends_on") or {}).get("nodes") or []):
        entry = ((catalog or {}).get("sources") or {}).get(parent) or \
            ((catalog or {}).get("nodes") or {}).get(parent) or {}
        out |= {str(c).lower() for c in (entry.get("columns") or {})}
    return out


def _unresolved(node: dict, digest, parent_cols: set) -> str:
    """Why this model's column lookups look unanswered, or "".

    *** THE RAW CODE SAYS WHAT IT LOOKED UP, AND THE COMPILED SQL SAYS WHAT CAME BACK. ***
    `dlt_col(present, 'parcel_id')` renders as `parcel_id` when the relation has it and as
    `cast(null as varchar)` when it does not. A blind compile is told nothing exists, so NONE of
    the names the model looks up appear in its compiled SQL. A connected one references most.
    """
    # *** ONLY THE MODEL'S OWN MACROS, AND ONLY THE NAME EACH ONE IS ASKED ABOUT. ***
    # The first version read every quoted word in every `{{ }}`, so `source('raw', 'parcels')`,
    # `config(materialized='table')` and a type argument like 'double' counted as columns looked
    # up, and three models of a CONNECTED compile were called blind.
    mine = {str(u).split(".")[-1] for u in ((node.get("depends_on") or {}).get("macros") or [])
            if not str(u).startswith("macro.dbt.")}
    named: set = set()
    for block in _CALL.findall(node.get("raw_code") or ""):
        for fn, args in _FN.findall(block):
            if fn not in mine:
                continue
            first = _QUOTED.search(args)
            if first:
                named.add(first.group(1).lower())
    if len(named) < 3:
        return ""
    used = {str(c).split(".")[-1].lower() for c in (digest.referenced_columns or [])}
    got = named & used
    if len(got) / len(named) >= _RESOLVED_SHARE:
        return ""
    exist = named & parent_cols
    tail = (f" -- and the catalog says {len(exist)} of them DO exist on the parent, so the "
            f"lookup was not answered" if exist else
            " -- either the relation really lacks them, or the lookup was never answered")
    return (f"its code looks up {len(named)} column(s) by name and the compiled SQL uses "
            f"{len(got)} of them{tail}")


def sentence(found: list[Blind], looked: int) -> str:
    """One line for a coverage panel, said before any finding. Empty when nothing to say."""
    if not found:
        return ""
    names = ", ".join(b.model for b in found[:5])
    return (f"{len(found)} of {looked} model(s) built on a macro that asks the warehouse look "
            f"compiled WITHOUT a connection ({names}{', ...' if len(found) > 5 else ''}). Their "
            f"SQL is what the macro writes when it is told nothing exists, so what assay reads and "
            f"judges there is not the real model. Compile with a connection -- the project's own "
            f"profile -- before trusting findings on them.")


def for_project(project, digests) -> tuple[list[Blind], int]:
    """`blind_models` with the project's own `catalog.json`, when there is one."""
    import json
    cat = None
    path = getattr(project, "target_dir", None)
    if path is not None and (path / "catalog.json").exists():
        try:
            cat = json.loads((path / "catalog.json").read_text())
        except (OSError, ValueError):
            cat = None
    return blind_models(project, digests, cat)
