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


def load_all_banks(directory: Path | None = None) -> dict:
    """Every .yml in the questions directory. A user's own bank loads the same way."""
    directory = directory or (Path(__file__).parent / "questions")
    out: dict = {}
    for f in sorted(directory.glob("*.yml")):
        for name, q in _load_bank(f).items():
            if name != "version":
                out[name] = q
    return out


QUESTIONS = load_all_banks()
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
    dropped: list[str] = field(default_factory=list)     # a judgment ruled these out
    uncertain: list[str] = field(default_factory=list)   # it could not tell, and said so


def candidates(uid: str, project, digests: dict[str, Digest], schema,
               known: dict[str, list[str]], declared: dict[str, list[str]],
               observed: dict | None = None) -> GrainCandidate | None:
    """What COULD be one row of this. A judgment only ever picks from what code found here."""
    d = digests.get(uid)
    if not d or not d.ok:
        return None

    if d.group_by_columns:
        # *** AN AGGREGATE IS NEVER PART OF THE GRAIN OF THE QUERY THAT PRODUCED IT. ***
        # Code decides this; asking a judgment spends tokens to learn what a parser already knows,
        # and three real models answered ~0.53 on exactly these columns because the state did not
        # carry the fact that settles them.
        roots = {**(d.resolved_roots or {}), **schema.columns(uid).roots}
        cols = [c for c in d.group_by_columns
                if not str(roots.get(c, "")).startswith("agg:")]
        dropped = [c for c in d.group_by_columns if c not in cols]
        reason = "the model aggregates to these columns"
        if dropped:
            reason += f" (excluded as aggregates, which cannot identify a row: {dropped})"
        if cols:
            return GrainCandidate(cols, "group_by", reason)

    for w in d.windows:
        if w.position == "qualify" and w.partition_columns:
            return GrainCandidate(list(w.partition_columns), "qualify_dedupe",
                                  "the model dedupes to one row per partition")

    drivers = [x for x in (schema.uid_of.get(r.lower()) for r in d.from_relations) if x]
    if not drivers:
        return None
    observed = observed or {}
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
        # *** THE BASE CASE. ***
        # Propagation stops at a relation nothing declares -- typically a source. A probe settles
        # it by counting, which is exact and needs no judgment, and this is the whole reason the
        # probe exists: a judgment cannot pick an option that was never on the list.
        rel = (schema.relation.get(drv) or "").lower()
        seen = observed.get(rel) or {}
        uniques = sorted(col for col, o in seen.items() if o.status == "unique")
        if len(uniques) == 1:
            return GrainCandidate(uniques, "from_probe",
                                  f"observed unique in {project.name_of(drv)} "
                                  f"({seen[uniques[0]].detail})")
    return None


def propose_all(project, digests, schema, declared, observed=None) -> dict[str, GrainCandidate]:
    """Walk the DAG parents-first so a child can inherit what its parents were found to be."""
    known: dict[str, list[str]] = {}
    out: dict[str, GrainCandidate] = {}
    for uid in project.topological():
        c = candidates(uid, project, digests, schema, known, declared, observed)
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


def key_from_answers(cand: GrainCandidate, answers: dict,
                     high: float = 0.70, low: float = 0.35) -> Grain:
    """Code composes the key from independent per-column answers, WITH A BAND.

    *** A NOUL NEAR 0.5 MEANS SIMILAR PROBABILITY EITHER WAY. IT IS NOT A WEAK YES. ***
    A single cut at 0.5 converts "I do not know" into "yes, it identifies", which is the worst of
    the three available answers. Measured on real models: three of them answered 0.52-0.56 on
    columns whose provenance lay in a SOURCE built outside dbt, so nothing in the state could
    settle them -- and the model said exactly that. Banding keeps the column (narrowing a key on
    absent evidence is worse than leaving it wide) and records that it is unresolved, which is what
    a review queue reads.
    """
    keep, drop, unsure = [], [], []
    for col in cand.columns:
        a = answers.get(f"key__{col}")
        if a is None:
            keep.append(col)                 # unanswered: keep rather than silently narrowing
            continue
        p = float(a["answer"])
        if p >= high:
            keep.append(col)
        elif p <= low:
            drop.append(col)
        else:
            keep.append(col)
            unsure.append(col)
    return Grain(columns=keep or list(cand.columns), route=cand.route,
                 source="judged", dropped=drop, uncertain=unsure)
