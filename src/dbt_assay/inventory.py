"""What every model in the project actually IS. The artifact, not a list of complaints.

*** THE FIRST RUN SHOULD NOT BE A FINDINGS LIST. ***
Every tool gives you findings and everyone ignores them. This gives you the document that does not
exist for any warehouse anywhere, because it cannot be written by hand at three hundred models:
here is each model's grain, and for each column its role, where its value came from, and what a NULL
in it would mean. Findings are then just the contradictions inside it.

*** EVERY CELL WEARS WHERE IT CAME FROM AND HOW SURE IT IS. ***
A grain a human declared, one the probe counted, one code derived and one a judgment reached at 0.53
must not render alike. An inventory that is confidently wrong and relied upon is worse than no
inventory, and that is the failure mode that would do real damage. So:

    declared  a human wrote it down (a unique test, a documented key)
    observed  the probe counted it, on a stated day and row count
    derived   code worked it out from the SQL and the DAG
    judged    a model answered, and the probability is carried with it

*** IT DEGRADES TO THE NO-KEY TIER. ***
With no API key there is still a grain for every model code can settle, provenance for every column,
the edges, and the blast radius. That is already a document nobody has. A judgment fills in role and
sharpens grain; it is not the price of entry.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import contracts, provenance, relate

SOURCES = ("declared", "observed", "derived", "judged", "unknown")

DDL = """
create table if not exists inventory (
    run_id     varchar,
    model      varchar,
    column_name varchar,          -- '' for a model-level property
    property   varchar,           -- grain | role | provenance | null_meaning | ...
    value      varchar,
    source     varchar,           -- declared | observed | derived | judged | unknown
    confidence double,
    note       varchar,
    primary key (run_id, model, column_name, property)
);
"""


@dataclass
class Fact:
    value: object
    source: str = "unknown"
    confidence: float | None = None
    note: str = ""
    # A fact built on an unresolved premise must say so rather than inherit a confidence it did
    # not earn. Confidences are never multiplied; provenance is carried instead.
    resting_on: list = field(default_factory=list)

    @property
    def firm(self) -> bool:
        return self.source in ("declared", "observed") and not self.resting_on

    def render(self) -> str:
        """No square brackets: rich reads them as style tags and silently eats the provenance,
        which is the one part of a cell that must never disappear."""
        v = ", ".join(self.value) if isinstance(self.value, list) else str(self.value)
        c = f" @{self.confidence:.2f}" if self.confidence is not None else ""
        flag = " · rests on an unresolved premise" if self.resting_on else ""
        return f"{v} · {self.source}{c}{flag}"


@dataclass
class ColumnEntry:
    name: str
    provenance: Fact
    role: Fact | None = None
    null_meaning: Fact | None = None
    in_key: bool = False


@dataclass
class ModelEntry:
    uid: str
    name: str
    path: str
    layer: str
    materialized: str
    grain: Fact | None = None
    # *** WHAT THE SQL SAYS, KEPT EVEN WHEN A DECLARATION OUTRANKS IT. ***
    # A declared key wins in the inventory, which is right. But it means a model whose GROUP BY
    # moved would show no change at all, and a group by drifting away from a declared key is
    # exactly the event worth catching: the test is about to start failing, or the declaration has
    # already gone stale.
    derived_grain: list | None = None
    columns: list = field(default_factory=list)
    descendants: int = 0
    marts: int = 0
    reads: list = field(default_factory=list)
    unreadable: bool = False

    @property
    def confidence_floor(self) -> str:
        """The weakest link, because a contract is only as good as its shakiest part."""
        order = {s: i for i, s in enumerate(SOURCES)}
        facts = [self.grain] + [c.role for c in self.columns] + [c.provenance for c in self.columns]
        present = [f for f in facts if f is not None]
        return max((f.source for f in present), key=lambda s: order.get(s, 99)) if present else "unknown"


def _judgments(store, uid: str) -> dict:
    """Stored answers for one model, keyed by question id."""
    if store is None:
        return {}
    rows = store.con.execute(
        """select question, answer, confidence from model_decisions
           where decision_key = ?""", [uid]).fetchall()
    return {q: {"answer": a, "confidence": c} for q, a, c in rows}


def build(project, digests, schema, store=None, observed=None) -> list[ModelEntry]:
    declared = relate.declared_keys(project)
    proposed = contracts.propose_all(project, digests, schema, declared, observed)
    observed = observed or {}
    out = []

    for uid in project.topological():
        m = project.models[uid]
        entry = ModelEntry(uid=uid, name=m.name, path=m.path, layer=m.layer,
                           materialized=m.materialized, unreadable=not m.readable,
                           reads=[project.name_of(p) for p in m.parents])
        b = project.blast_radius(uid)
        entry.descendants, entry.marts = b["descendants"], b["marts"]

        # ---- grain, strongest evidence first ----
        judged = _judgments(store, uid)
        if uid in proposed:
            entry.derived_grain = [c.lower() for c in proposed[uid].columns]
        if uid in declared:
            entry.grain = Fact(declared[uid], "declared", note="a test in this project declares it")
        elif uid in proposed:
            cand = proposed[uid]
            key_answers = {q: a for q, a in judged.items() if q.startswith("key__")}
            if key_answers:
                g = contracts.key_from_answers(cand, {k: {"kind": "noul", **v}
                                                      for k, v in key_answers.items()})
                # *** THE GRAIN'S CONFIDENCE IS THE WEAKEST COLUMN IN IT, NOT THE WEAKEST ANSWER. ***
                # Taking the minimum across every answer drags a sound grain down using columns the
                # judgment CORRECTLY rejected: a key of [wdid] read 0.25 because a measure beside it
                # scored 0.25 and was thrown out, which is the system working.
                kept = [float(key_answers[f"key__{c}"]["answer"])
                        for c in g.columns if f"key__{c}" in key_answers]
                entry.grain = Fact(g.columns, "judged", min(kept) if kept else None, cand.reason,
                                   resting_on=[f"unresolved: {c}" for c in g.uncertain])
            else:
                src = "observed" if cand.route == "from_probe" else "derived"
                entry.grain = Fact(cand.columns, src, note=cand.reason)

        # ---- columns ----
        prov = provenance.classify(uid, project, digests, schema)
        # *** "UNKNOWN" MUST SAY WHY. ***
        # A model with no compiled SQL cannot be classified at all, and reporting its columns as a
        # bare "unknown" reads like assay looked and found nothing, rather than that it could not
        # look. Same rule as the coverage line: a scanner that cannot see must say so.
        blind = "this model has no compiled SQL, so nothing could be read" if entry.unreadable else ""
        key_cols = {c.lower() for c in (entry.grain.value if entry.grain else [])}
        for col in schema.columns(uid).names:
            c = col.lower()
            p = prov.get(c)
            kind = p.kind if p else "unknown"
            note = p.evidence if p else ""
            if kind == "unknown" and blind:
                note = blind
            pf = Fact(kind, "derived", note=note)
            ce = ColumnEntry(name=c, provenance=pf, in_key=c in key_cols)
            r = judged.get(f"role__{c}")
            if r:
                ce.role = Fact(r["answer"], "judged", r.get("confidence"))
            n = judged.get(f"null__{c}")
            if n:
                ce.null_meaning = Fact(n["answer"], "judged", n.get("confidence"))
            rel = (schema.relation.get(uid) or "").lower()
            seen = (observed.get(rel) or {}).get(c)
            if seen and seen.status != "unknown":
                ce.provenance.note += f"; probe: {seen.detail}"
            entry.columns.append(ce)
        out.append(entry)
    return out


def describe(entry: ModelEntry) -> str:
    """Prose ASSEMBLED BY CODE from typed answers. Never generated.

    Jev is not trained to generate text, and this is better than generation anyway: every clause
    traces to a stored judgment or a parsed fact, carries its own confidence, and can be corrected
    through `assay review`.
    """
    bits = []
    if entry.grain:
        g = ", ".join(entry.grain.value) if isinstance(entry.grain.value, list) else entry.grain.value
        qual = {"declared": "", "observed": " (counted in the data)",
                "derived": " (worked out from the SQL)",
                "judged": " (inferred)"}.get(entry.grain.source, "")
        bits.append(f"One row per {g}{qual}.")
    else:
        bits.append("The grain of this model has not been settled.")

    measures = [c.name for c in entry.columns if c.role and c.role.value == "measure"]
    ids = [c.name for c in entry.columns if c.role and c.role.value == "identifier"]
    if ids:
        bits.append(f"Identifiers: {', '.join(ids[:6])}.")
    if measures:
        bits.append(f"Measures you could sum: {', '.join(measures[:6])}.")

    defaulted = [c.name for c in entry.columns if c.provenance.value == "defaulted"]
    if defaulted:
        bits.append(f"{', '.join(defaulted[:4])} replace NULL with a literal here, "
                    f"so a not_null test on them cannot fire.")
    from_src = sum(1 for c in entry.columns if c.provenance.value == "from_source")
    if from_src:
        bits.append(f"{from_src} of {len(entry.columns)} columns arrive from a source, so what "
                    f"produced them is outside this project.")
    if entry.descendants:
        bits.append(f"{entry.descendants} models downstream, {entry.marts} of them marts.")
    return " ".join(bits)


def write_store(store, run_id: str, entries: list[ModelEntry]) -> None:
    store.con.execute(DDL)
    rows = []
    for e in entries:
        if e.grain:
            rows.append([run_id, e.name, "", "grain", json.dumps(e.grain.value),
                         e.grain.source, e.grain.confidence, e.grain.note[:400]])
        for c in e.columns:
            rows.append([run_id, e.name, c.name, "provenance", str(c.provenance.value),
                         c.provenance.source, c.provenance.confidence, c.provenance.note[:400]])
            for prop, f in (("role", c.role), ("null_meaning", c.null_meaning)):
                if f:
                    rows.append([run_id, e.name, c.name, prop, str(f.value), f.source,
                                 f.confidence, f.note[:400]])
    if rows:
        store.con.executemany("insert or replace into inventory values (?,?,?,?,?,?,?,?)", rows)


def to_yaml_dict(entries: list[ModelEntry], adjudicated_only: bool = True,
                 adjudicated: set | None = None) -> dict:
    """A SEPARATE file, never dbt's schema.yml.

    Writing inferences into files a human maintains means every run produces a diff in them, and a
    stream of machine edits is how people stop reading their own pull requests. This is isolated,
    opt-in, and dbt never reads it.
    """
    adjudicated = adjudicated or set()
    out: dict = {"version": 1, "models": {}}
    for e in entries:
        if not e.grain:
            continue
        if adjudicated_only and (e.uid, "grain") not in adjudicated \
                and e.grain.source not in ("declared", "observed"):
            continue
        block = {"grain": e.grain.value, "grain_source": e.grain.source,
                 "description": describe(e)}
        cols = {c.name: {"role": c.role.value, "confidence": c.role.confidence}
                for c in e.columns
                if c.role and (not adjudicated_only or (e.uid, f"role__{c.name}") in adjudicated)}
        if cols:
            block["columns"] = cols
        out["models"][e.name] = block
    return out
