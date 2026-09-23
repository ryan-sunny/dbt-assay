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

KINDS = ("model", "edge", "column", "predicate", "expression", "window", "ruling_pair",
         "finding", "default", "column_risk", "enumerated_filter", "same_name_measure",
         "time_join", "ranking_window", "sentinel")

# *** A QUESTION CAN ONLY ASK WHAT ITS SUBJECT'S STATE CAN ANSWER, AND NOTHING SAID SO. ***
# Reported from the field, and it cost an hour: two custom questions lint-passed and never fired,
# because they asked about `filters` -- which only a `model` carries. A question about an absent
# predicate cannot be asked one predicate at a time, and an `edge` does not carry filters at all.
# The failure is silent in the worst way: the question is asked, answered, paid for and stored,
# and the answer is about a field that was never in the state.
#
# DECLARED, not inferred from the builders below, because a reader has to be able to see it --
# and a test asserts this map is exactly what the builders produce, so it fails until it is right
# rather than drifting quietly. `_add_what_a_row_is` adds `what_one_row_of_this_model_is` to
# every kind that names a model, which is why it is in all of them but `ruling_pair`.
STATE_FIELDS: dict = {
    "model": {"model", "columns", "filters", "groups_by", "reads",
              "what_one_row_of_this_model_is"},
    "edge": {"parent", "child", "columns_the_child_drops",
             "the_child_already_collapsed_the_parent_before_joining",
             "the_child_reads_this_parent_as_one_arm_of_a_UNION",
             "what_one_row_of_this_model_is"},
    "column": {"model", "column", "description", "expression", "derived_from", "other_columns",
               "what_one_row_of_this_model_is"},
    "predicate": {"model", "predicate_under_judgment", "the_models_other_filters", "reads",
                  "what_one_row_of_this_model_is"},
    "expression": {"model", "produces_column", "expression", "derived_from",
                   "the_model_groups_by", "columns_it_reads",
                   "what_one_row_of_this_model_is"},
    "window": {"model", "where_it_sits", "partition_by", "order_by", "order_is_reprojected",
               "the_model_filters", "what_one_row_of_this_model_is"},
    # `ruling_pair` comes from the STORE and names no model, so it gets no row description.
    "ruling_pair": {"check_or_question_both_rulings_are_about", "first_reason", "second_reason"},
    # One (model, check) pair's findings, and the code they are about: what a review card shows a
    # person, so a reading of it is a reading of what they will be asked to rule on.
    "finding": {"model", "check", "findings", "sql", "description",
                "what_one_row_of_this_model_is"},
}
# The build queue's families, whose subjects are narrowed in `subject_kinds`.
from .subject_kinds import STATE_FIELDS as _MORE_FIELDS

STATE_FIELDS.update(_MORE_FIELDS)


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
    # The findings a `finding` subject is built from. None computes the structural ones; a caller
    # that already holds the full stream -- judged findings included -- passes it.
    findings: list | None = None


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
    from .subject_kinds import BUILDERS as _MORE
    if kind == "finding":
        out = _findings(project, digests, schema, src.findings, src.store)
    elif kind in _MORE:
        out = _MORE[kind](project, digests, schema)
    else:
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


# The SQL is the evidence a reading of a finding needs, and the one field that can be huge. The
# largest real model on the field warehouse is 629 lines; this keeps the state readable and says so.
_SQL_CHARS = 6000


def _findings(project, digests, schema, findings, store) -> list[Subject]:
    """One subject per (model, check), the grain a verdict covers and a review card shows."""
    if findings is None:
        from . import live
        findings = live.all_findings(project, digests, schema, None, store=store)
    by: dict = {}
    for f in findings:
        if not f.subject:
            continue                          # a finding about the whole project has no card
        by.setdefault((f.subject, f.check), []).append(f)
    out = []
    for (uid, check), fs in sorted(by.items()):
        # A source or a seed has a card too, and no SQL: its finding is about where it reaches.
        m = project.models.get(uid) or _Named(fs[0].subject_name or uid.split(".")[-1],
                                              fs[0].file or "")
        sql = m.compiled or ""
        if len(sql) > _SQL_CHARS:
            sql = (sql[:_SQL_CHARS] + f"\n-- assay: {_SQL_CHARS:,} of {len(sql):,} characters; "
                   f"the rest is not in this state")
        fs = sorted(fs, key=lambda x: x.id)
        out.append(Subject(
            "finding", f"{uid}::{check}", uid, f"{m.name} / {check}", file=m.path,
            state=_prune({
                "model": m.name, "check": check,
                # *** THE EVIDENCE, BECAUSE THE DETAIL IS THE CHECK'S AND NOT THE CASE'S. ***
                # The first run read five `models_disagree_about_a_column` cards and answered
                # `cannot_tell` on four, at 0.4: the detail says what the check means in general,
                # and the sixteen sentences it was about were only in `evidence`. An unclear is
                # the state failing to carry the answer, which is fixed by a field.
                "findings": [_prune({"summary": f.summary,
                                     "detail": (f.detail or "")[:700],
                                     "claim": str((f.evidence or {}).get("claim") or ""),
                                     "evidence": _evidence(f.evidence)})
                             for f in fs[:6]],
                "sql": sql,
                "description": (getattr(m, "description", "") or "")[:600]})))
    return out


def _evidence(ev) -> dict:
    """A finding's evidence, bounded: long lists cut with a count, the whole capped."""
    import json as _json
    out = {}
    for k, v in (ev or {}).items():
        if k in ("claim", "confidence") or v in (None, "", [], {}):
            continue
        if k == "described_in" and (ev or {}).get("one_of_each"):
            continue                            # the variants say what differs; this does not
        if isinstance(v, list) and len(v) > 12:
            v = [*v[:12], f"... and {len(v) - 12} more"]
        if isinstance(v, str) and len(v) > 400:
            v = v[:400] + "..."
        out[k] = v
    while out and len(_json.dumps(out, default=str)) > 1500:
        out.popitem()
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


def described(m) -> dict:
    """{column_lower: description} from this model's schema.yml. The one thing a person wrote.

    *** THE COLUMN STATE WAS BUILT FROM THE NAME AND THE SQL AND NEVER FROM THE SENTENCE. ***
    A role was judged from a column's name, its expression, its roots and its sibling names --
    while `schema.yml` sat there saying what the column IS, in a sentence somebody chose. On a
    real warehouse `decreed_use_codes` came back at 0.52 confidence, which is a model saying it
    cannot tell, next to a description that says exactly.
    """
    out = {}
    for name, body in (getattr(m, "columns", None) or {}).items():
        text = (body or {}).get("description") if isinstance(body, dict) else None
        if text and str(text).strip():
            out[str(name).lower()] = str(text).strip()
    return out


def _columns(project, digests, schema) -> list[Subject]:
    out = []
    for uid, m in project.models.items():
        d = _ok(digests, uid)
        if d is None:
            continue
        roots = {**(d.output_roots or {}), **(d.resolved_roots or {})}
        docs = described(m)
        for c in list(schema.columns(uid).names)[:80]:
            expr = (d.output_exprs or {}).get(c.lower()) or (d.output_exprs or {}).get(c)
            out.append(Subject(
                "column", f"{uid}::col::{c}", uid, f"{m.name}.{c}", file=m.path,
                state=_prune({"model": m.name, "column": c,
                              # The sentence goes in FIRST, because it is the only part of this
                              # state a person wrote on purpose.
                              "description": docs.get(c.lower()),
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


@dataclass
class _Named:
    """A card's subject that is not a model: a name, a file, and no SQL of its own."""
    name: str
    path: str = ""
    compiled: str = ""
    description: str = ""


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


# ------------------------------------------------------------------------------ calibration

def wilson(k: int, n: int, z: float = 1.96) -> tuple:
    """A 95% interval for a proportion, by the Wilson score.

    *** A BARE PERCENTAGE AT n=18 INVITES A CONCLUSION THE SAMPLE CANNOT CARRY. ***
    Reported from the field about this very report: the bands read 85 / 74 / 50 / 64, which looks
    like a clean inversion, and every adjacent pair overlaps heavily -- even the best and worst
    bands overlap by seven points. "Inverted" was a plausible reading presented as an established
    one, in the one feature whose whole purpose is to stop somebody gating on an axis that is not
    measuring what they think.

    Wilson rather than the normal approximation because the normal one is wrong at exactly the
    sample sizes this report will have for months: it produces intervals that run past 1.0, and it
    is worst when the proportion is near 0 or 1, which is where an interesting band sits.
    """
    if not n:
        return (None, None)
    import math
    p = k / n
    d = 1 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0.0, center - half), 4), round(min(1.0, center + half), 4))


def separated(rows: list) -> bool:
    """Do ANY two bands of one family actually separate, intervals and all?

    Two bands whose intervals overlap have not been shown to differ. If no pair separates, the
    report has measured nothing yet and has to say so rather than let a reader rank the numbers.
    """
    by_fam: dict = {}
    for r in rows:
        if r["lo"] is not None:
            by_fam.setdefault((r["source"], r["family"]), []).append(r)
    for group in by_fam.values():
        for a in group:
            for b in group:
                if a is not b and (a["hi"] < b["lo"] or b["hi"] < a["lo"]):
                    return True
    return False


def calibration(store, bands: tuple = (0.3, 0.5, 0.7)) -> list[dict]:
    """When this thing is confident, is it right more often than when it is not?

    *** `effectiveness` MEASURES AGREEMENT. IT NEVER MEASURED AGREEMENT AGAINST CONFIDENCE. ***
    That is the question a person actually asks of a probability, and "mean confidence 0.50" is a
    bad answer to it: a family averaging 0.50 over 1,629 answers is equally consistent with a
    well-calibrated judge and with a coin. Only the bands tell them apart.

    *** AND THE NUMBER TO BAND BY DEPENDS ON THE QUESTION'S SHAPE. ***
    A `choice` stores distribution concentration in `confidence`. A `noul` has no separate
    confidence ON PURPOSE -- its ANSWER is the probability -- so `confidence` is null for every
    one of them. A report reading only `confidence` finds nulls, concludes the question is
    unanswerable, and is wrong: the field store's richest calibration material is 82 verdicts on a
    noul, with 26 disagreements in them, sitting in the answer column the whole time.

    Sources are never summed. `label` is the project's own declarations, which have been measured
    wrong three times in four when read by hand; `human` is somebody who looked; `agent` is
    triage. They are reported apart because they are different evidence, which is the same rule
    `min_adjudications` already enforces at the gate.
    """
    if store is None:
        return []
    rows = store.con.execute("""
        with live as (
            select decision_key, question, answer, confidence, kind, prompt_version,
                   row_number() over (partition by decision_key, question
                                      order by decided_at desc) rn
            from model_decisions)
        select a.source, a.family, l.kind, l.confidence, l.answer, a.verdict
        from adjudications a
        join live l
          -- Two ways a verdict reaches its decision. `decision_key` is the direct one, recorded
          -- since 0.29.0 when a judged finding is ruled on. The subject match is how every
          -- verdict written before that still counts.
          -- *** coalesce, BECAUSE AN ADDED COLUMN IS NULL AND NOT ''. ***
          -- A store upgraded by `alter table add column` fills the new column with NULL, so
          -- `<> ''` and `= ''` are BOTH false and every row falls out of the join. The report
          -- then shows nothing, which reads exactly like a project with no verdicts.
          on ((coalesce(a.decision_key, '') <> '' and l.decision_key = a.decision_key)
              or (coalesce(a.decision_key, '') =  '' and l.decision_key = a.subject))
         and l.question = a.question and l.rn = 1
        where a.verdict in ('agree', 'disagree')
    """).fetchall()

    out: dict = {}
    for source, family, kind, conf, answer, verdict in rows:
        # *** THE BAND COMES FROM WHICHEVER NUMBER THIS QUESTION SHAPE ACTUALLY CARRIES. ***
        p = conf
        if p is None and (kind or "") == "noul":
            try:
                p = float(answer)
            except (TypeError, ValueError):
                p = None
        if p is None:
            # No probability of any kind. Not a band, and NOT a zero: counted separately so a
            # shrinking table cannot read as a confident judge.
            key = (source, family, "no probability")
        else:
            lo = [b for b in bands if p >= b]
            label = (f"{lo[-1]:.2f}+" if len(lo) == len(bands)
                     else f"{lo[-1]:.2f}-{bands[len(lo)]:.2f}" if lo
                     else f"< {bands[0]:.2f}")
            key = (source, family, label)
        d = out.setdefault(key, {"source": key[0], "family": key[1], "band": key[2],
                                 "ruled": 0, "agree": 0, "disagree": 0})
        d["ruled"] += 1
        d["agree" if verdict == "agree" else "disagree"] += 1
    for d in out.values():
        d["agreement"] = d["agree"] / d["ruled"] if d["ruled"] else None
        d["lo"], d["hi"] = wilson(d["agree"], d["ruled"])
    # A stable order, so two runs of the report produce the same rows in the same places.
    return sorted(out.values(), key=lambda d: (d["source"], d["family"], d["band"]))
