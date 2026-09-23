"""The one path that builds a state, so that a state can be built AGAIN.

*** EVERY CALLER USED TO ASSEMBLE ITS OWN, AND NOTHING COULD REPRODUCE ANY OF THEM. ***
Eighteen call sites built the dict they sent to `decide()`: some called a module's `build_state`,
some merged a vocabulary in at the call site, two wrote the dict out inline. Measured against the
field warehouse: rebuilding all 871 model and edge subjects through `subjects.build` and hashing
them reproduced **0** of the stored `state_hash` values. Not because anything had drifted --
because the thing that was sent was never the thing a rebuild produces.

That makes `state_hash` a one-way number. It can say two answers came from the same state; it can
never say whether the state an answer came from is still what the code says today. So
`assay stale --exact` could not be written, and every judged answer's exactness rested on a
comparison nobody could make.

*** SO THE STATE IS BUILT HERE, BY NAME, FROM IDENTIFIERS. ***
A builder is `fn(ctx, inputs) -> dict`. `inputs` names WHICH subject -- a uid, a list of claim ids,
a pair of relations -- and never carries derived content, because content recorded in the inputs
is content a rebuild cannot notice changing. `make()` and `rebuild()` call the same function with
the same arguments, so they agree by construction rather than by review.

`decide()` takes a `Recipe` and refuses a bare dict. There is no second way in.

*** AND A BUILDER THAT CANNOT BE REPRODUCED SAYS SO, BY NAME. ***
Three states carry rows read out of the warehouse at a moment in time: a feed's sample, a failing
row dbt stored, a practice check's output. Those cannot be rebuilt from code and never will be.
They are declared `reproducible=False` with the reason, and `assay stale --exact` reports them as
*not comparable* rather than as unchanged. An absent comparison is not a clean bill.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field


def digest_of(parts) -> str:
    """A stable id for a chunk of subjects.

    *** `hash()` IS SALTED PER PROCESS AND THREE DECISION KEYS WERE BUILT FROM IT. ***
    `align::{hash(tuple(...))}`, `{uid}::pred::{hash(...)}` and `sev::{hash(...)}`. Python
    randomizes string hashing per interpreter, so the same ten pairs produced a different cache
    key on every run: the lookup missed, the question was re-asked, and it was paid for again,
    forever. Verified in three separate processes -- 3086818264447541477, 851160657164217624,
    -1533554951737849456 for the same tuple.
    """
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Recipe:
    """What was sent, what it was about, and how to build it again."""
    builder: str
    key: str                        # the decision key
    inputs: dict                    # identifiers only; enough to rebuild, never derived content
    state: dict


@dataclass(frozen=True)
class Builder:
    name: str
    fn: Callable | None             # None exactly when the state cannot be rebuilt from code
    reproducible: bool = True
    because: str = ""               # required when reproducible is False


BUILDERS: dict = {}

# *** THE CTX THAT DROPPED A TERM IS RARELY THE ONE REPORTING. ***
# Several commands build a Ctx per call site, so a counter living on one instance would be a
# number the surface never sees -- the failure this codebase keeps finding, where "it reported
# nothing" and "it is not wired up" are the same output. Append-only, per process.
VOCAB_DROPS: list = []


def builder(name: str, *, reproducible: bool = True, because: str = ""):
    def register(fn):
        if not reproducible and not because:
            raise ValueError(f"{name}: a builder that cannot be reproduced must say why")
        BUILDERS[name] = Builder(name, fn if reproducible else None, reproducible, because)
        return fn
    return register


def carries_data(name: str) -> bool:
    """True for the three states built from rows read out of the warehouse."""
    b = BUILDERS.get(name)
    return b is not None and not b.reproducible


@dataclass
class Ctx:
    """Everything any builder may read. Derived collections are computed once, on demand.

    Nothing here is a state. A builder that needs the project's grain proposals recomputes them
    from this, so that a grain moving is drift the rebuild can SEE. Recording the proposal in the
    inputs instead would make the one thing worth noticing invisible.
    """
    project: object = None
    digests: dict = field(default_factory=dict)
    schema: object = None
    store: object = None
    vocab: dict = field(default_factory=dict)
    entries: list | None = None
    # The full finding stream, judged findings included, for `finding` subjects. None builds the
    # structural ones, which is what a rebuild months later can reproduce from code alone.
    findings: list | None = None
    _memo: dict = field(default_factory=dict, repr=False)
    # (term, the subjects it was dropped for) -- every time a scoped term did not reach a state.
    # Counted rather than silent: a vocabulary quietly thinning is the same shape as one that was
    # never wired up, and those must not read alike.
    vocab_drops: list = field(default_factory=list, repr=False)

    def once(self, key: str, fn):
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]

    # ---------- the vocabulary, scoped ----------

    def _scope_of(self, term: str, sel):
        """The uids a term applies to, or None for 'everywhere'. Resolved once per term.

        *** A SCOPE NEEDS TO SUBTRACT, BECAUSE THE EXCEPTION LIVES INSIDE THE RULE. ***
        On the warehouse this was built for, the Arizona models are `models/water/az` -- 70 of
        them, INSIDE `models/water`. So `path:models/water` for a term about Colorado law still
        reaches every Arizona model, and the scoping would have read as working while changing
        nothing for the case that motivated it. `applies_to` therefore takes either a selector or
        `{select:, exclude:}`, which is the shape dbt's own `--select`/`--exclude` already has.
        """
        from .selector import scope_of
        return self.once(f"scope::{term}", lambda: scope_of(self.project, sel))

    def vocab_for(self, *uids) -> dict:
        """The terms that are true about EVERY subject in this state.

        *** INTERSECTION, BECAUSE A STATE THAT CONTRADICTS ITSELF IS WORSE THAN A THIN ONE. ***
        Some states are about several models at once -- a chunk of column pairs, a chunk of tests.
        Under a union rule a batch pairing a Colorado model with an Arizona one would be told both
        that prior appropriation decides who gets water and that an AMA permit does, in one call.
        Under this rule it is told neither, keeps the terms that are true of both, and the drop is
        recorded so a vocabulary thinning is visible rather than inferred.

        A term with no `applies_to` is true everywhere, which is what every term meant before this
        existed -- so a config written yesterday behaves identically today.
        """
        if not self.vocab:
            return {}
        known = [u for u in uids if u]
        # *** AND A STATE WHOSE SUBJECTS COULD NOT BE RESOLVED KEEPS NO SCOPED TERM. ***
        # `all(...)` over an empty list is True, so an empty subject set would quietly keep every
        # scoped term and the scoping would read as working while doing nothing. That is the
        # failure this feature exists to remove, one layer in. Caught by writing it wrong first:
        # `align`'s pairs carry model NAMES, not uids, so the first version resolved none of them
        # and kept the whole vocabulary.
        resolved = bool(known)
        out, dropped = {}, []
        for term, body in self.vocab.items():
            sel = body.get("applies_to") if isinstance(body, dict) else None
            if not sel or self.project is None:
                out[term] = body
                continue
            scope = self._scope_of(term, sel)
            # `resolve` returns None for "everything" and an EMPTY SET for "matched nothing".
            # Those are different facts and only one of them is a term that applies.
            if scope is None or (resolved and all(u in scope for u in known)):
                out[term] = body
            else:
                dropped.append(term)
        if dropped:
            rec = (tuple(sorted(dropped)), tuple(known))
            self.vocab_drops.append(rec)
            VOCAB_DROPS.append(rec)
        return out

    def uid_of_name(self, name: str) -> str | None:
        """A model name back to its unique_id. `align` carries names, and a scope wants uids."""
        by_name = self.once("by_name", lambda: {
            m.name: u for u, m in (getattr(self.project, "models", {}) or {}).items()})
        return by_name.get(name)

    # ---------- derived collections, one spelling each ----------

    def declared(self) -> dict:
        from . import relate
        return self.once("declared", lambda: relate.declared_keys(self.project))

    def observed(self) -> dict:
        from . import probe as probe_mod
        return self.once("observed", lambda: probe_mod.read(self.store) if self.store else {})

    def proposed(self) -> dict:
        from . import contracts
        return self.once("proposed", lambda: contracts.propose_all(
            self.project, self.digests, self.schema, self.declared(), self.observed()))

    def model_entries(self) -> list:
        from . import inventory as inv
        if self.entries is not None:
            return self.entries
        return self.once("entries", lambda: inv.build(
            self.project, self.digests, self.schema, self.store, self.observed()))

    def edge_facts(self) -> dict:
        from . import relate
        return self.once("edges", lambda: {
            (f.parent, f.child): f
            for f in relate.run_all(self.project, self.digests, self.schema)[0]})

    def semantic_subjects(self) -> dict:
        from . import semantics as sem
        return self.once("sem", lambda: {
            s.uid: s for s in sem.subjects(self.project, self.digests, self.schema,
                                           self.model_entries())})

    def claim_candidates(self) -> dict:
        """{claim_id: Claim} straight from the code. A pure function of the project."""
        from . import claims as claims_mod
        from . import semantics as sem
        return self.once("claim_cands", lambda: {
            c.claim_id: c for c in claims_mod.candidates(
                self.project, self.digests, sem.boilerplate(self.project))})

    def stored_claims(self) -> dict:
        from . import claims as claims_mod
        def load():
            out = {}
            for r in (self.store.claims() if self.store else []):
                out[r["claim_id"]] = claims_mod.Claim(
                    r["claim_id"], r["subject"], r["subject_name"], r["text"],
                    r["source_kind"], r["source_ref"], citation=r["citation"] or "")
            return out
        return self.once("claims", load)

    def subjects_of(self, kind: str, state: str = "full") -> dict:
        from . import subjects as subjects_mod
        def load():
            src = subjects_mod.SubjectSource(self.project, self.digests, self.schema, self.store,
                                             findings=self.findings)
            return {s.key: s for s in subjects_mod.build(kind, src, state=state)}
        return self.once(f"subjects::{kind}::{state}", load)

    def align_pairs(self) -> dict:
        from . import align as align_mod
        def load():
            joined = align_mod.joined_pairs(self.project, self.digests, self.schema)
            return {p.key: p for p in align_mod.candidates(self.model_entries(), joined)}
        return self.once("align", load)

    def test_subjects(self) -> dict:
        from . import testing as testing_mod
        return self.once("tests", lambda: {
            s.test_name: s for s in testing_mod.subjects(self.project, self.model_entries())})

    def column_facts(self, uid: str) -> dict:
        from . import columns as columns_mod
        return self.once(f"colfacts::{uid}", lambda: columns_mod.facts_for(
            uid, self.project, self.digests, self.schema, self.declared()))


def make(name: str, ctx: Ctx, *, key: str, inputs: dict, state: dict | None = None):
    """Build a state for sending. The ONLY place a state is made.

    A reproducible builder BUILDS the state and refuses one handed to it, so the live path and the
    rebuild path are the same function call. A builder declared non-reproducible is the other way
    round: its state carries data read out of the warehouse, so the caller supplies it and the
    registry records that it can never be rebuilt. Both still go through here and both still carry
    a builder name, which is what makes the gap countable instead of invisible.
    """
    b = BUILDERS.get(name)
    if b is None:
        raise KeyError(f"no state builder named {name!r}. Have: {sorted(BUILDERS)}")
    if b.reproducible:
        if state is not None:
            raise ValueError(
                f"{name} builds its own state; passing one is the second path this module exists "
                f"to remove")
        state = b.fn(ctx, inputs)
    elif state is None:
        raise ValueError(f"{name} cannot build its own state ({b.because}), so one is required")
    if not state:
        return None
    return Recipe(builder=name, key=key, inputs=dict(inputs), state=state)


def rebuild(name: str, ctx: Ctx, inputs: dict) -> dict | None:
    """Build the same state again, later, from the same identifiers. The SAME function."""
    b = BUILDERS.get(name)
    if b is None or not b.reproducible or b.fn is None:
        return None
    return b.fn(ctx, inputs)


def why_not(name: str) -> str:
    """Why a builder's state cannot be rebuilt from code, or '' when it can."""
    b = BUILDERS.get(name)
    if b is None:
        return f"no builder named {name!r} is registered any more"
    return "" if b.reproducible else b.because


# ------------------------------------------------------------------ the builders
# Each one: fn(ctx, inputs) -> the state as sent, vocabulary included. `inputs` is identifiers.


@builder("description")
def _description(ctx, inputs):
    from . import semantics as sem
    s = ctx.semantic_subjects().get(inputs["uid"])
    return sem.description_state(s, ctx.vocab_for(inputs["uid"])) if s else None


@builder("predicates")
def _predicates(ctx, inputs):
    from . import semantics as sem
    s = ctx.semantic_subjects().get(inputs["uid"])
    if s is None:
        return None
    want = list(inputs["predicates"])
    # The chunk is named by its members, so a predicate that has since been edited out makes the
    # rebuild impossible rather than silently smaller -- which would read as a changed state.
    if not set(want) <= set(s.predicates or []):
        return None
    return sem.build_state(s, want, ctx.vocab_for(inputs["uid"]))


@builder("claim_kind")
def _claim_kind(ctx, inputs):
    from . import claims as claims_mod
    cands = ctx.claim_candidates()
    chunk = [cands[c] for c in inputs["claim_ids"] if c in cands]
    if len(chunk) != len(inputs["claim_ids"]):
        return None
    m = ctx.project.models.get(inputs["uid"])
    if m is None:
        return None
    return claims_mod.kind_state(m.name, chunk, m.description or "",
                                 ctx.vocab_for(inputs["uid"]))


@builder("claim_align")
def _claim_align(ctx, inputs):
    from . import claims as claims_mod
    c = ctx.stored_claims().get(inputs["claim_id"])
    if c is None:
        return None
    ev = claims_mod.evidence_for(c.subject, ctx.project, ctx.digests, ctx.schema,
                                 ctx.observed(), claim_text=c.text)
    return claims_mod.align_state(c, ev, ctx.vocab_for(c.subject)) if ev else None


@builder("edge")
def _edge(ctx, inputs):
    from . import relate
    f = ctx.edge_facts().get((inputs["parent"], inputs["child"]))
    if f is None:
        return None
    cd = ctx.digests.get(f.child)
    if cd is None or not cd.ok:
        return None
    # A hop is about BOTH models, so it gets the terms true of both.
    return relate.edge_state(f, cd, ctx.declared(), ctx.vocab_for(f.parent, f.child))


@builder("subject")
def _subject(ctx, inputs):
    """Every family that declares `subject:` in its bank -- `ask` and `regress`.

    *** THE VOCABULARY USED TO BE MERGED IN AT THE CALL SITE. ***
    `{**sub.state, **({"vocabulary": cfg.vocab} if cfg.vocab else {})}`, written twice, in two
    commands. That merge is why a rebuilt subject state could never match a stored hash: the
    subject builder produced one dict and the thing that was sent was a different one.
    """
    subs = ctx.subjects_of(inputs["kind"], inputs.get("subject_state", "full"))
    s = subs.get(inputs["key"])
    if s is None:
        return None
    v = ctx.vocab_for(s.uid)
    return {**s.state, **({"vocabulary": v} if v else {})}


@builder("ruling_pair")
def _ruling_pair(ctx, inputs):
    """Two verdicts somebody gave. `subjects.build` already produced exactly this state and
    `disagreements` wrote its own copy of it three keys at a time; this is that one spelling."""
    s = ctx.subjects_of("ruling_pair").get(inputs["key"])
    return dict(s.state) if s else None


@builder("grain")
def _grain(ctx, inputs):
    from . import contracts
    uid = inputs["uid"]
    cand = ctx.proposed().get(uid)
    if cand is None:
        return None
    return contracts.build_state(uid, ctx.project, ctx.digests, ctx.schema, cand,
                                 ctx.declared(), ctx.vocab_for(uid))


@builder("columns")
def _columns(ctx, inputs):
    from . import columns as columns_mod
    uid = inputs["uid"]
    facts = ctx.column_facts(uid)
    cols = list(inputs["columns"])
    if not set(cols) <= set(facts):
        return None
    proposed = ctx.proposed()
    grain = [x.lower() for x in (proposed[uid].columns if uid in proposed
                                 else ctx.declared().get(uid) or [])]
    return columns_mod.build_state(uid, ctx.project, ctx.schema, facts, cols, grain,
                                   ctx.vocab_for(uid))


@builder("align")
def _align(ctx, inputs):
    from . import align as align_mod
    pairs = ctx.align_pairs()
    chunk = [pairs[k] for k in inputs["pairs"] if k in pairs]
    if len(chunk) != len(inputs["pairs"]):
        return None
    # A chunk of pairs spans two models each. Every one of them, or the term does not apply.
    uids = {u for p in chunk
            for u in (ctx.uid_of_name(p.model_a), ctx.uid_of_name(p.model_b)) if u}
    return align_mod.build_state(chunk, ctx.vocab_for(*sorted(uids)))


@builder("severity")
def _severity(ctx, inputs):
    from . import testing as testing_mod
    subs = ctx.test_subjects()
    chunk = [subs[t] for t in inputs["tests"] if t in subs]
    if len(chunk) != len(inputs["tests"]):
        return None
    uids = {u for u in (t.model_uid for t in chunk) if u}
    return testing_mod.build_state(chunk, ctx.vocab_for(*sorted(uids)))


@builder("bank")
def _bank(ctx, inputs):
    from .contracts import load_all_banks
    from .lint import bank_state
    bank = load_all_banks().get(inputs["family"])
    return bank_state(bank) if bank else None


@builder("feed", reproducible=False,
         because="its state carries a SAMPLE of rows read from the warehouse, which is data at a "
                 "moment in time and not a fact about the code")
def _feed(_ctx, _inputs):
    """Never called: the caller supplies this state, because it holds warehouse data."""
    raise NotImplementedError("feed states are built at the call site from a live sample")


@builder("failing_row", reproducible=False,
         because="its state carries the failing rows dbt stored for a test, which are data and "
                 "are gone the next time the test passes")
def _failing_row(_ctx, _inputs):
    """Never called: the caller supplies this state, because it holds warehouse data."""
    raise NotImplementedError("failing-row states are built at the call site from stored failures")


@builder("volume", reproducible=False,
         because="its state carries a row COUNT read out of the warehouse at a moment in time, "
                 "which is a measurement rather than something the code says")
def _volume(_ctx, _inputs):
    """Never called: the caller supplies this state, because it holds a counted movement."""
    raise NotImplementedError("volume states are built from what Elementary counted")


@builder("practice", reproducible=False,
         because="its state carries a row read out of a dbt-project-evaluator table in the "
                 "warehouse, which is a measurement rather than something the code says")
def _practice(_ctx, _inputs):
    """Never called: the caller supplies this state, because it holds warehouse data."""
    raise NotImplementedError("practice states are built at the call site from a probed row")
