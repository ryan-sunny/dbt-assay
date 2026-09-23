"""Grain: candidates from code, the minimal key from a judgment.

*** THE QUESTION IS NOT 'WHAT IS THE GRAIN'. ***
Measured against the 209 models in a real project that declare their own key -- a labeled set the
project already contained, needing no human labeling -- code alone proposes a candidate column set
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

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .jev import noul
from .parse import Digest


# *** 3,620 YAML PARSES IN ONE `assay check`, 13 OF ITS 22 SECONDS. ***
# `Store.live_decisions` asks which prompt versions still ship, once per model, and each ask
# re-read all ten banks. A file is parsed once per (path, mtime, size): an edited bank is re-read,
# an unchanged one is not. Callers get a deep copy, because a cached dict handed out by reference
# is one caller's mutation showing up in every later caller.
_BANK_CACHE: dict = {}


def _load_bank(path: Path) -> dict:
    """Normalize YAML's boolean keys back to strings.

    YAML 1.1 reads a bare `true:` key as the boolean True, so a hand-written bank that omits the
    quotes loads with {True: ..} and every lookup by "true" raises. assay ships quoted keys, but a
    USER's bank will not always, and failing on their file is a worse outcome than accepting it.
    """
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    if key is not None and key in _BANK_CACHE:
        return copy.deepcopy(_BANK_CACHE[key])
    data = yaml.safe_load(path.read_text()) or {}
    for q in data.values():
        if isinstance(q, dict) and isinstance(q.get("criteria"), dict):
            q["criteria"] = {("true" if k is True else "false" if k is False else k): v
                             for k, v in q["criteria"].items()}
    if key is not None:
        _BANK_CACHE[key] = copy.deepcopy(data)
    return data


def user_bank_dir() -> Path | None:
    """Where this project keeps its own questions, if it does.

    `ASSAY_QUESTIONS` wins; otherwise an `assay_questions/` directory here or above, found the same
    way a `.env` is. Convention rather than configuration, because this has to resolve at IMPORT
    time: fourteen modules bind their question at module level, so a bank loaded any later would
    add families and silently fail to override one.
    """
    env = os.environ.get("ASSAY_QUESTIONS")
    if env:
        p = Path(env).expanduser()
        return p if p.is_dir() else None
    here = Path.cwd().resolve()
    for d in [here, *here.parents][:6]:
        c = d / "assay_questions"
        if c.is_dir():
            return c
    return None


def load_all_banks(directory: Path | None = None, with_user: bool = True) -> dict:
    """Every .yml in the questions directory, then this project's own on top.

    *** A USER'S BANK CAN ADD A FAMILY AND CAN REPLACE ONE. ***
    Replacing is the point: a warehouse whose `column_role` needs an extra option should not have
    to fork. The shipped bank loads first and the user's overwrites by name, and `assay banks`
    prints which is which so an override is never a surprise.
    """
    out: dict = {}
    for d in ([directory] if directory else
              [Path(__file__).parent / "questions",
               *( [user_bank_dir()] if with_user and user_bank_dir() else [] )]):
        if d is None:
            continue
        for f in sorted(d.glob("*.yml")):
            for name, q in _load_bank(f).items():
                if name != "version":
                    q = dict(q)
                    q["_source"] = str(f)
                    out[name] = q
    return out


SHIPPED = load_all_banks(Path(__file__).parent / "questions")
QUESTIONS = load_all_banks()
KEY_Q = QUESTIONS["column_is_part_of_the_key"]
PROMPT_VERSION = KEY_Q["prompt_version"]


@dataclass
class GrainCandidate:
    columns: list[str]
    route: str            # group_by | qualify_dedupe | from_driver
    reason: str = ""
    # Columns the route named that this model does not actually emit. A grain is only useful in
    # the names a consumer can see, and one that names something else is a test nobody can write.
    not_emitted: list[str] = field(default_factory=list)


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
            # *** THE GROUP BY ROUTE RESOLVES ALIASES AND THIS ONE DID NOT. ***
            # `fact_sale` does `parcel_id as sale_id` and dedupes per parcel, so the grain came
            # back as `parcel_id` -- a column the model does not emit. A test cannot be written on
            # it, and `practices` duly proposed one. The output name is the only one anything
            # downstream can assert.
            alias = {k.lower(): v for k, v in (d.alias_of or {}).items()}
            cols = [alias.get(str(c).lower(), c) for c in w.partition_columns]
            renamed = [f"{c} (as {alias[str(c).lower()]})"
                       for c in w.partition_columns if str(c).lower() in alias]
            why = "the model dedupes to one row per partition"
            if renamed:
                why += f", renamed on the way out: {', '.join(renamed)}"
            return GrainCandidate(cols, "qualify_dedupe", why)

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


def _in_this_models_own_names(c: GrainCandidate, uid: str, digests, schema) -> GrainCandidate:
    """*** A GRAIN IS ONLY USEFUL IN THE NAMES A CONSUMER CAN SEE. ***

    `fact_sale` inherited `parcel_id` from a parent while its own arms do `parcel_id as sale_id`.
    The grain named a column the model does not emit, so `practices` proposed a uniqueness test
    that cannot be written, and every consumer reading the inventory was told the wrong key.

    Translated where the SQL says how -- an alias, or an output expression that IS that column --
    and where it cannot be translated the column is KEPT and recorded as not emitted, because
    dropping it would quietly narrow a key and that is worse than naming a problem.
    """
    d = digests.get(uid)
    try:
        emitted = {x.lower() for x in schema.columns(uid).names}
    except Exception:                                                   # noqa: BLE001
        return c
    if not emitted:
        return c
    alias = {k.lower(): v for k, v in ((d.alias_of if d else None) or {}).items()}
    exprs = {str(v).strip().lower(): k for k, v in ((d.output_exprs if d else None) or {}).items()}

    cols, missing = [], []
    for col in c.columns:
        low = str(col).lower()
        if low in emitted:
            cols.append(col)
        elif low in alias and str(alias[low]).lower() in emitted:
            cols.append(alias[low])                     # renamed on the way out
        elif low in exprs and str(exprs[low]).lower() in emitted:
            cols.append(exprs[low])                     # an output column IS this expression
        else:
            cols.append(col)
            missing.append(col)
    return GrainCandidate(cols, c.route, c.reason, missing)


def propose_all(project, digests, schema, declared, observed=None) -> dict[str, GrainCandidate]:
    """Walk the DAG parents-first so a child can inherit what its parents were found to be."""
    known: dict[str, list[str]] = {}
    out: dict[str, GrainCandidate] = {}
    for uid in project.topological():
        c = candidates(uid, project, digests, schema, known, declared, observed)
        if c:
            c = _in_this_models_own_names(c, uid, digests, schema)
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


# *** A QUESTION ID THAT MAPS TO NO FAMILY IS FILED UNDER NOTHING, SILENTLY. ***
# A verdict is recorded against the FAMILY a question id names, and the id is `<prefix>__<subject>`
# or a bare prefix. When a prefix is not declared by any bank, `name__` and `unit__` both were, the
# verdict lands under a family that does not exist: it counts toward no gate, appears in no report,
# and nothing anywhere says so. Twice now that has been found by reading, months apart, which is
# not a way to find things.
#
# So it is checked where the question is ASKED rather than in a test that has to guess at every
# call site. Wrong here is a programming error and it raises.
def family_index() -> dict:
    """{id_prefix: family}, read once.

    *** FOR CALLERS THAT ASK ABOUT THOUSANDS OF IDS, AND FOR NO OTHER REASON. ***
    `family_of` re-reads every bank off disk per call, which is right for a caller resolving one
    question and is thirty seconds for `assay cost`, which resolves 2,289. Built here and passed
    back IN so the prefix rule stays written down exactly once -- reimplementing the split at the
    call site is how three shipped questions came to resolve to a neighbouring family.
    """
    return {q["id_prefix"]: name for name, q in load_all_banks().items() if q.get("id_prefix")}


def family_of(question_id: str, index: dict | None = None) -> str | None:
    """The bank a question id files its verdicts under, or None when nothing claims it."""
    prefix = question_id.split("__")[0]
    if index is not None:
        return index.get(prefix)
    for name, q in load_all_banks().items():
        if q.get("id_prefix") == prefix:
            return name
    return None


# *** THE INVARIANT THAT WAS NEVER ASSERTED. ***
# `check_question_ids` asserts that SOME bank claims a prefix, and one always does. It never
# asserted that the prefix claims the RIGHT bank, which is the actual invariant, and three shipped
# questions violated it: `sentence_is_a_claim` (prefix `sentence`) files under `claim__N`,
# `claim_alignment` (prefix `claim`) files under `align`, and `same_concept` declares `align`. Each
# read the next one's version.
EMITTED_IDS = {
    # family: the id its writer actually puts in the question dict, and where.
    "sentence_is_a_claim":              ("sentence__<i>", "claims.kind_questions"),
    "claim_alignment":                  ("claim", "claims.align_question"),
    "same_concept":                     ("align__<i>", "align.pair_questions"),
    "severity_fit":                     ("sev__<i>", "testing.questions"),
    "predicate_intent":                 ("pred__<i>", "semantics.predicate_questions"),
    "description_contradicts_the_code": ("desc", "semantics.description_question"),
    "practice_exception":               ("exception", "practices.question_for"),
    "options_overlap":                  ("overlap", "lint.judge_overlap"),
}


def id_prefix_conflicts() -> list[str]:
    """Where a family's declared `id_prefix` disagrees with the id its writer emits, or where two
    families would resolve from the same prefix. Either one makes `family_of` land on the wrong
    bank, and nothing in this codebase may resolve a version through it while they exist."""
    banks = load_all_banks()
    out, by_prefix = [], {}
    for fam, (emitted, where) in sorted(EMITTED_IDS.items()):
        q = banks.get(fam)
        if q is None:
            continue
        declared, actual = q.get("id_prefix"), emitted.split("__")[0]
        if declared != actual:
            out.append(f"{fam} declares id_prefix {declared!r} and {where} files under "
                       f"{emitted!r}, so `family_of` resolves it to whichever bank claims "
                       f"{actual!r}")
        by_prefix.setdefault(actual, []).append(fam)
    for prefix, fams in sorted(by_prefix.items()):
        if len(fams) > 1:
            out.append(f"{', '.join(fams)} all file under the prefix {prefix!r}, so no mapping "
                       f"from a question id to a family can be correct for them")
    return out


def check_question_ids(question_ids) -> None:
    """Raise on any id no bank claims. Called before a request is built, so a mis-prefixed
    question fails on its first run rather than filing verdicts nobody can count."""
    unknown = sorted({q for q in question_ids if family_of(q) is None})
    if unknown:
        known = sorted(q["id_prefix"] for q in load_all_banks().values() if q.get("id_prefix"))
        raise ValueError(
            f"question id(s) {unknown} use a prefix no bank declares. "
            f"A verdict on one files under a family that does not exist, counts toward no gate "
            f"and shows in no report. Declared prefixes: {known}. "
            f"Add `id_prefix:` to the question's bank.")


def current_versions() -> dict:
    """{id_prefix: the prompt_version shipping now}, for every question a bank defines.

    *** AN ANSWER FROM A RETIRED VERSION IS NOT AN ANSWER TO THIS QUESTION. ***
    `model_decisions` is keyed on (decision_key, question, prompt_version, model_version) so every
    version of every answer is kept, which is what makes `effectiveness` possible. Reading it
    without filtering hands back all of them at once.

    Reported from the field: `traversal` returned twelve verdicts for four hops and called the
    same hop both `silently_multiplied` and `deliberately_coarser`, because every one of them
    predated a version bump. Worse, `inventory._judgments` did the same thing and then let a
    later row OVERWRITE an earlier one, so which answer reached a finding depended on the order
    duckdb happened to return -- `arbitrary_pick`, the defect this tool checks other people's
    code for, in its own inventory.
    """
    out: dict = {}
    for q in load_all_banks().values():
        p, v = q.get("id_prefix"), q.get("prompt_version")
        if p and v:
            out[str(p)] = str(v)
    return out
