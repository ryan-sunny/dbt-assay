"""Grain: candidates from code, the minimal key from a judgment.

*** THE QUESTION IS NOT 'WHAT IS THE GRAIN'. ***
Measured against the 209 models in a real project that declare their own key -- a labelled set the
project already contained, needing no human labelling -- code alone proposes a candidate column set
for 80 of them, is exactly right on 45%, and on 62% the declared key is a SUBSET of what code
proposed, with a median of 3 surplus columns.

So the open question is which of the proposed columns are the KEY and which are labels or measures
carried along. `int_water_section_districts` groups by eight columns and declares three; the other
five follow from the district. `int_eco_basin` groups by five and declares two; one is a label and
two are counts. No parser can tell those apart, and a judgment does not need to invent anything
because every option is a column code already found.

*** NOTHING HERE INFERS ANYTHING FROM A FOLDER NAME. ***
The base case is graph position -- a node whose parents' grains are unknown -- not a layer called
"staging". A project with every model in one flat directory behaves identically. The only thing a
path is ever used for is a label in the output.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .jev import noul
from .parse import Digest


def _load_bank(path: Path) -> dict:
    """Normalise YAML's boolean keys back to strings.

    YAML 1.1 reads a bare `true:` key as the boolean True, so a hand-written bank that omits the
    quotes loads with {True: ..} and every lookup by "true" raises. assay ships quoted keys, but a
    USER's bank will not always, and failing on their file is a worse outcome than accepting it.
    """
    data = yaml.safe_load(path.read_text()) or {}
    for q in data.values():
        if isinstance(q, dict) and isinstance(q.get("criteria"), dict):
            q["criteria"] = {("true" if k is True else "false" if k is False else k): v
                             for k, v in q["criteria"].items()}
    return data


QUESTIONS = _load_bank(Path(__file__).parent / "questions" / "grain.yml")
KEY_Q = QUESTIONS["column_is_part_of_the_key"]
PROMPT_VERSION = KEY_Q["prompt_version"]


@dataclass
class GrainCandidate:
    columns: list[str]
    route: str            # group_by | qualify_dedupe | from_driver
    reason: str = ""


@dataclass
class Grain:
    columns: list[str] = field(default_factory=list)
    route: str = "unknown"
    source: str = "code"          # code | declared | judged
    dropped: list[str] = field(default_factory=list)   # candidates a judgment ruled out


def candidates(uid: str, project, digests: dict[str, Digest], schema,
               known: dict[str, list[str]], declared: dict[str, list[str]]) -> GrainCandidate | None:
    """What COULD be one row of this. A judgment only ever picks from what code found here."""
    d = digests.get(uid)
    if not d or not d.ok:
        return None

    if d.group_by_columns:
        return GrainCandidate(list(d.group_by_columns), "group_by",
                              "the model aggregates to these columns")

    for w in d.windows:
        if w.position == "qualify" and w.partition_columns:
            return GrainCandidate(list(w.partition_columns), "qualify_dedupe",
                                  "the model dedupes to one row per partition")

    drivers = [x for x in (schema.uid_of.get(r.lower()) for r in d.from_relations) if x]
    if not drivers:
        return None
    for j in d.joins:
        if j.target_aggregates or j.kind == "CROSS" or not j.target_relation:
            continue
        tgt = schema.uid_of.get(j.target_relation.lower())
        k = known.get(tgt) or declared.get(tgt)
        if not k:
            return None                                  # unknown target grain: cannot propagate
        if not set(k).issubset(set(j.target_keys)):
            return None                                  # this join inflates; grain is not the driver's
    for drv in drivers:
        g = known.get(drv) or declared.get(drv)
        if g:
            return GrainCandidate(list(g), "from_driver",
                                  f"inherited from {project.name_of(drv)}; no join inflates it")
    return None


def propose_all(project, digests, schema, declared) -> dict[str, GrainCandidate]:
    """Walk the DAG parents-first so a child can inherit what its parents were found to be."""
    known: dict[str, list[str]] = {}
    out: dict[str, GrainCandidate] = {}
    for uid in project.topological():
        c = candidates(uid, project, digests, schema, known, declared)
        if c:
            out[uid] = c
            known[uid] = [x.lower() for x in c.columns]
        elif uid in declared:
            known[uid] = [x.lower() for x in declared[uid]]
    return out


def key_questions(cand: GrainCandidate) -> dict:
    """ONE NOUL PER COLUMN, never one question over the whole list.

    A single question over several columns hides several judgments behind one answer, cannot say
    WHICH column it meant, and needs a second question to be actionable -- which is the cost it was
    supposed to save. The columns are independent, they batch into one request, and each carries its
    own criteria.
    """
    qs = {}
    for col in cand.columns:
        instr = dict(KEY_Q["instructions"])
        instr = {"column": col, "candidate_columns": list(cand.columns), **instr}
        qs[f"key__{col}"] = noul(instr, KEY_Q["criteria"]["true"], KEY_Q["criteria"]["false"])
    return qs


def build_state(uid: str, project, digests, schema, cand: GrainCandidate,
                declared: dict, vocab: dict | None = None) -> dict:
    """State is an OBJECT with named fields, because a judgment about a model needs to know which
    value is a parent's key and which is this model's own output."""
    d, m = digests[uid], project.models[uid]
    # A placeholder description is noise, not evidence. "Mart model." says nothing, and sending it
    # invites the model to read meaning into boilerplate. The key is OMITTED, not set to null.
    model_block = {"name": m.name, "materialized": m.materialized}
    if _meaningful(m.description):
        model_block["description"] = m.description.strip()[:400]

    state = {
        "model": model_block,
        "how_the_candidates_were_found": cand.reason,
        "candidate_columns": cand.columns,
        "all_output_columns": schema.columns(uid).names[:60],
        "column_expressions": {c: d.output_exprs.get(c, "")[:120] for c in cand.columns
                               if d.output_exprs.get(c)},
        "parents": [
            {"name": project.name_of(p), "declared_key": declared.get(p),
             "columns": schema.columns(p).names[:30]}
            for p in m.parents
        ][:12],
        "joins": [
            {"kind": j.kind,
             "target": project.name_of(schema.uid_of.get((j.target_relation or "").lower(), ""))
                       or ("<subquery>" if j.target_is_subquery else j.target[:40]),
             "matched_on": j.target_keys}
            for j in d.joins
        ][:12],
        "group_by": d.group_by_columns,
        "dedupes_with_distinct": d.absorbs_fanout,
    }
    if vocab:
        state["vocabulary"] = vocab
    return {k: v for k, v in state.items() if v not in (None, [], {})}


def _meaningful(desc: str | None) -> bool:
    if not desc:
        return False
    t = desc.strip().lower().rstrip(".")
    return len(t) > 24 and t not in {
        "mart model", "staging model", "intermediate model", "todo", "tbd", "model"}


def key_from_answers(cand: GrainCandidate, answers: dict, threshold: float = 0.5) -> Grain:
    """Code composes the key from independent per-column answers. The model never sees the whole."""
    keep, drop = [], []
    for col in cand.columns:
        a = answers.get(f"key__{col}")
        if a is None:
            keep.append(col)                 # unanswered: keep it rather than silently narrowing
            continue
        (keep if float(a["answer"]) >= threshold else drop).append(col)
    return Grain(columns=keep or list(cand.columns), route=cand.route,
                 source="judged", dropped=drop)
