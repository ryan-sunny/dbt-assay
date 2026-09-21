"""Subjects a question can be asked about, built from the project without a bespoke call site.

*** A FAMILY WITH A NEW NAME USED TO BE LOADED, LINTED, LISTED, AND NEVER ASKED. ***
Every shipped family had its own hand-written caller that named it by string literal, so writing a
question was only half the job and the other half was a code change. Three custom families were
written on a real warehouse, linted at zero errors, shown by `assay banks` as `yours`, and did
nothing. They looked exactly like coverage.

A family declares the SUBJECT it wants and assay builds that state for it. `subject: expression`
gets one output expression at a time; `subject: window` gets one window function. Those two were
asked for by name in the field and had no call site at all.

*** THE STATE IS THE SMALLEST THING THAT CAN ANSWER THE QUESTION. ***
Measured: a claim alone read 0.96 and the same claim plus one CORRECT extra sentence read 0.47.
Every builder here sends the subject and the few facts that bear on it, never the whole model.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

KINDS = ("model", "edge", "column", "predicate", "expression", "window", "ruling_pair")


@dataclass
class SubjectSource:
    """Everything a subject builder may read.

    *** THE SIGNATURE ASSUMED EVERY SUBJECT COMES FROM THE PROJECT, AND ONE DOES NOT. ***
    `build(kind, project, digests, schema, ...)` was fine while every kind was a dbt object.
    `ruling_pair` comes from the STORE, and the choice was between a seventh parameter five kinds
    ignore or a bundle. The bundle, because the case that needs it is the one being written: an
    optional argument added for a caller that already exists is a decision deferred, not avoided.

    Two call sites, both internal. A custom family declares `subject:` in YAML and never touches
    this.
    """
    project: object = None
    digests: dict = field(default_factory=dict)
    schema: object = None
    store: object = None


@dataclass
class Subject:
    kind: str
    key: str                       # stable, and what a decision is filed under
    uid: str                       # the model this belongs to, for blast radius and findings
    name: str                      # what a person would call it
    state: dict = field(default_factory=dict)
    file: str = ""


def _model_of(project, uid):
    return project.models.get(uid)


def build(kind: str, src: SubjectSource, limit: int = 0,
          state: str = "full") -> list[Subject]:
    """Every subject of one kind in this project. Ordered so a --limit takes the reachable ones.

    *** A FIELD THAT HELPS ONE FAMILY CAN COST ANOTHER, AND BOTH ARE THE FIELD WORKING. ***
    `what_one_row_of_this_model_is` was added because a window subject could not say whether it
    ranked rights or sections. It fixed that case and cost two of eight verified answers on a
    family whose own criteria reason about the PARTITION: the model read a right id in the
    MODEL's grain as satisfying a clause about the WINDOW's partition, which was a different list.

    The criterion was relying on the model not knowing something, which is not a stable thing to
    rely on -- but the fix is to let that family opt out, not to remove a field that is correct
    and that other families need. `subject_state: minimal` in the bank.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown subject {kind!r}. Use one of {KINDS}.")
    if state not in ("full", "minimal"):
        raise ValueError(f"unknown subject_state {state!r}. Use 'full' or 'minimal'.")
    project, digests, schema = src.project, src.digests, src.schema
    if kind == "ruling_pair":
        # *** NOT A dbt OBJECT, SO NO BLAST RADIUS AND NOTHING TO SAY A ROW IS. ***
        # It is two verdicts somebody gave, and its ordering is its own.
        out = _ruling_pairs(src.store)
        return out[:limit] if limit else out
    fn = {"model": _models, "edge": _edges, "column": _columns,
          "predicate": _predicates, "expression": _expressions, "window": _windows}[kind]
    out = fn(project, digests, schema)
    if state == "full":
        _add_what_a_row_is(out, project, digests, schema)
    # Most reachable first: a limit should spend itself where a defect costs most.
    out.sort(key=lambda s: -project.blast_radius(s.uid)["descendants"])
    return out[:limit] if limit else out


def _ok(digests, uid):
    d = digests.get(uid)
    return d if d is not None and d.ok else None


def _models(project, digests, schema) -> list[Subject]:
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None:
            continue
        out.append(Subject(
            "model", f"{uid}::model", uid, m.name, file=m.path,
            state={"model": m.name,
                   "columns": list(schema.columns(uid).names)[:50],
                   "filters": [p for p in (d.predicates_atomic or []) if p.strip() not in
                               ("1 = 1", "TRUE", "true")][:12],
                   "groups_by": list(d.group_by or [])[:8] or None,
                   "reads": [project.name_of(p) for p in (m.parents or [])][:8]}))
    return out


def _edges(project, digests, schema) -> list[Subject]:
    from . import relate
    facts, _ = relate.run_all(project, digests, schema)
    declared = relate.declared_keys(project)
    out = []
    for f in facts:
        cd = _ok(digests, f.child)
        if cd is None or not (f.joined_on or f.dropped):
            continue
        st = {"parent": {"model": f.parent_name, "declared_key": declared.get(f.parent) or None,
                         "columns": list(f.carried or [])[:20]},
              "child": {"model": f.child_name, "joins_on": list(f.joined_on or [])[:8],
                        "groups_by": list(cd.group_by or [])[:8] or None},
              "columns_the_child_drops": sorted(f.dropped or [])[:16] or None}
        pre = (cd.pre_aggregated or {}).get(f.parent_name)
        if pre is not None:
            st["the_child_already_collapsed_the_parent_before_joining"] = {
                "relation": f.parent_name, "to_one_row_per": pre or "a distinct"}
        # *** A UNION MEMBER CANNOT MULTIPLY, AND THE QUESTION COULD NOT SEE THAT. ***
        # Ten of twelve disagreements on a hand-ruled warehouse were this hop being read as
        # `silently_multiplied` when the parent is one arm of a union: one parent row is one child
        # row, and the child having more rows than any single parent is a different fact.
        if f.parent_name in (cd.union_members or set()):
            st["the_child_reads_this_parent_as_one_arm_of_a_UNION"] = (
                "so one row of the parent is one row of the child. The child having more rows "
                "than this parent is the union, not a fan-out on this hop.")
        st = _prune(st)
        out.append(Subject("edge", f"{f.child}::edge::{f.parent}", f.child,
                           f"{f.parent_name} -> {f.child_name}",
                           file=(_model_of(project, f.child) or _Blank()).path, state=_prune(st)))
    return out


def _columns(project, digests, schema) -> list[Subject]:
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None:
            continue
        roots = {**(d.output_roots or {}), **(d.resolved_roots or {})}
        for c in list(schema.columns(uid).names)[:80]:
            expr = (d.output_exprs or {}).get(c.lower()) or (d.output_exprs or {}).get(c)
            out.append(Subject(
                "column", f"{uid}::col::{c}", uid, f"{m.name}.{c}", file=m.path,
                state=_prune({"model": m.name, "column": c,
                              "expression": (expr or "")[:300] or None,
                              "derived_from": roots.get(c.lower()),
                              "other_columns": [x for x in schema.columns(uid).names
                                                if x != c][:25]})))
    return out


def _predicates(project, digests, schema) -> list[Subject]:
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None:
            continue
        for i, p in enumerate([x for x in (d.predicates_atomic or [])
                               if x.strip() not in ("1 = 1", "TRUE", "true")][:20]):
            out.append(Subject(
                "predicate", f"{uid}::pred::{i}", uid, f"{m.name}: {p[:48]}", file=m.path,
                state=_prune({"model": m.name, "predicate_under_judgment": p,
                              "the_models_other_filters":
                                  [x for x in (d.predicates_atomic or []) if x != p][:8],
                              "reads": [project.name_of(x) for x in (m.parents or [])][:6]})))
    return out


def _expressions(project, digests, schema) -> list[Subject]:
    """One output expression at a time. Asked for in the field and there was no call site."""
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None:
            continue
        for col, expr in list((d.output_exprs or {}).items())[:60]:
            if not expr or expr.strip().lower() == col.lower():
                continue                      # a bare passthrough asserts nothing
            out.append(Subject(
                "expression", f"{uid}::expr::{col}", uid, f"{m.name}.{col}", file=m.path,
                state=_prune({"model": m.name, "produces_column": col,
                              "expression": expr[:400],
                              "derived_from": (d.output_roots or {}).get(col.lower()),
                              "the_model_groups_by": list(d.group_by or [])[:8] or None,
                              "columns_it_reads": list(d.referenced_columns or [])[:25]})))
    return out


def _windows(project, digests, schema) -> list[Subject]:
    """One window function at a time. The other subject the field asked for by name."""
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None:
            continue
        for i, w in enumerate(d.windows or []):
            out.append(Subject(
                "window", f"{uid}::win::{i}", uid, f"{m.name} window {i + 1}", file=m.path,
                state=_prune({"model": m.name,
                              "where_it_sits": getattr(w, "position", None),
                              "partition_by": list(getattr(w, "partition_by", []) or [])[:8],
                              # *** `order_sql` IS THE FIELD. `order_roots` IS ['column']. ***
                              # Guessed `order_by` first, which does not exist, and fell back to
                              # the roots -- so the state carried the word "column" and 65% of
                              # answers were `cannot_tell`. A subject that cannot see the thing it
                              # is asked about produces a confident non-answer, which is the
                              # failure this whole tool is about.
                              "order_by": list(getattr(w, "order_sql", []) or [])[:8],
                              "order_is_reprojected":
                                  any(getattr(w, "order_reprojected", []) or []) or None,
                              "the_model_filters": [x for x in (d.predicates_atomic or [])
                                                    if x.strip() not in ("1 = 1",)][:8]})))
    return out


def _add_what_a_row_is(subs: list[Subject], project, digests, schema) -> None:
    """*** THE RESIDUE AFTER FIXING THE CRITERIA IS IN THE STATE, NOT THE WORDING. ***

    Reported from the field, and it is the sharpest thing said about this tool: a window subject
    carried the model name, the partition, the order by and the position -- and nothing saying
    what the ROWS ARE. No phrasing of the options can settle "is this ranking water rights or
    sections" when the state never says. The author rewrote the criteria to exclude a case in
    plain words and the model still answered it at 0.55 and 0.63, because the answer was not in
    the state to be found.

    assay already knows. `relate.declared_keys` has what a person wrote down, and the inventory
    has what code worked out. Both go in, labeled by which is which, because a declared key is a
    human judgment and an inferred grain is not.
    """
    from . import relate
    try:
        declared = relate.declared_keys(project)
    except Exception:                                               # noqa: BLE001
        declared = {}
    for s in subs:
        m = project.models.get(s.uid)
        if m is None:
            continue
        what: dict = {}
        if declared.get(s.uid):
            what["a_row_is_one"] = list(declared[s.uid])
            what["and_that_was"] = "declared by a test in this project"
        else:
            d = digests.get(s.uid)
            gb = list(getattr(d, "group_by_columns", None) or []) if d is not None else []
            if gb:
                what["a_row_is_probably_one"] = gb[:6]
                what["and_that_was"] = "inferred from the model's own group by, not declared"
        if not what:
            # *** 118 OF 356 MODELS HAVE NEITHER A DECLARED KEY NOR A GROUP BY. ***
            # For those the columns are the only thing that says what the rows are, and a window
            # ranking `wildfire_pct` over `section_id` is obviously about sections the moment they
            # are in the state. Capped hard, because unrelated detail is a distractor.
            try:
                cols = [c for c in schema.columns(s.uid).names][:14]
            except Exception:                                       # noqa: BLE001
                cols = []
            if cols:
                what["the_model_produces"] = cols
                what["and_that_was"] = "read off its columns; no key is declared for it"
        if m.layer:
            what["the_model_is_in"] = m.layer
        if what:
            s.state.setdefault("what_one_row_of_this_model_is", what)


class _Blank:
    path = ""


def _prune(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, [], {}, "")}


# *** THE FORM THAT HAD A CORPUS, AND THE TWO THAT DID NOT. ***
# Three shapes were specced for judging rulings. Measured on a 357-model warehouse's store first:
#
#   two rulings CONTRADICT each other   -- ZERO eligible pairs. No family had both an agree and a
#                                          disagree, so there was nothing to validate it against
#                                          and nothing for it to find.
#   a reason does not MATCH its finding -- no negative control available.
#   two reasons are the SAME DEFECT     -- 12 of 12 disagreements carried a reason.
#
# Only the third was buildable, and clustering those twelve yields two groups: eight that are the
# union case 0.12.0 fixed, and two that named a blind spot still open at the time. It would have
# produced both fixes. Measuring before building reversed the recommendation.
_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")


def normalize_reason(text: str) -> str:
    """The first sentence, lowercased and stripped, which is the free half of the clustering."""
    first = re.split(r"(?<=[.!?])\s", (text or "").strip(), maxsplit=1)[0]
    return _WS.sub(" ", _PUNCT.sub(" ", first.lower())).strip()


def open_disagreements(store, source: str = "all", limit: int = 200) -> list[dict]:
    """Every disagreement nobody has since agreed with at another version, newest first."""
    if store is None:
        return []
    q = """select subject, question, family, note, source, prompt_version, decided_at
           from adjudications a
           where a.verdict = 'disagree' and coalesce(a.note, '') <> ''
             and not exists (select 1 from adjudications b
                             where b.subject = a.subject and b.question = a.question
                               and b.verdict = 'agree'
                               and b.prompt_version <> a.prompt_version
                               and b.decided_at > a.decided_at)"""
    args: list = []
    if source != "all":
        q += " and a.source = ?"
        args.append(source)
    cols = ("subject", "question", "family", "note", "source", "prompt_version", "decided_at")
    rows = store.con.execute(q + " order by decided_at desc limit ?", [*args, limit]).fetchall()
    return [dict(zip(cols, r, strict=True)) for r in rows]


def candidate_pairs(store, cap: int = 300) -> list[tuple]:
    """(key, first, second) for every same-family pair of open disagreements worth ASKING about.

    *** THE STRUCTURAL TIER FIRST, AS EVERYWHERE ELSE. ***
    Pairing is O(n^2), so only same-family pairs are built and the whole thing is capped. Two
    reasons whose first sentence is already identical are NOT asked about: code settled it, so Jev
    is never asked, and the delta between what code clusters and what judgment clusters is the
    measurement of whether asking was worth anything at all. On the warehouse this was built
    against, eight of ten reasons opened with the same sentence and code grouped them for free.
    """
    rows = open_disagreements(store)
    by_family: dict = {}
    for r in rows:
        by_family.setdefault(r["family"], []).append(r)
    out: list[tuple] = []
    for fam, items in sorted(by_family.items()):
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                if normalize_reason(a["note"]) == normalize_reason(b["note"]):
                    continue                  # code already grouped these; asking adds nothing
                out.append((f"pair::{fam}::{_pair_id(a, b)}", a, b))
                if len(out) >= cap:
                    return out
    return out


def _ruling_pairs(store, cap: int = 300) -> list[Subject]:
    return [Subject("ruling_pair", key, a["subject"], f"{_short(a)} ~ {_short(b)}",
                    state={"check_or_question_both_rulings_are_about": a["family"],
                           "first_reason": (a["note"] or "")[:700],
                           "second_reason": (b["note"] or "")[:700]})
            for key, a, b in candidate_pairs(store, cap)]


def _short(r: dict) -> str:
    return str(r["subject"]).split("::")[0].split(".")[-1]


def _pair_id(a: dict, b: dict) -> str:
    raw = "|".join(sorted([f"{a['subject']}#{a['question']}", f"{b['subject']}#{b['question']}"]))
    return hashlib.sha1(raw.encode()).hexdigest()[:16]
