"""Claims: the things a project asserts about a model, as data rather than as prose.

*** A MODEL DESCRIPTION IS NOT ONE CLAIM, AND JUDGING IT AS ONE PRODUCES A COIN FLIP. ***
Measured. "Boulder commercial building permits, residential filtered out" is two claims: the first
is supported and the second is not. Put to a single `choice` it split 0.51 supports / 0.47
contradicts and flipped between runs. Split into atomic claims, the sharpest one read
`contradicts` at 0.82.

The old family was a noul, which got compound prose right by accident: "asserts something the code
does not do" is EXISTENTIAL, so any false part makes the whole true. That is also why it had
nowhere to put "the code neither does nor contradicts this", which is what made boilerplate flag.

*** EXTRACTION IS SELECTION, NEVER GENERATION. ***
Jev is not trained to generate and performs poorly when forced to. So code splits prose into
sentences and a judgment says what KIND of sentence each one is. The model never writes a claim; it
picks from what the author already wrote, which is also what makes a claim auditable: every one
points at the file and line it came from.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from .contracts import QUESTIONS
from .jev import choice

KIND_Q = QUESTIONS["sentence_is_a_claim"]
ALIGN_Q = QUESTIONS["claim_alignment"]
KIND_VERSION = KIND_Q["prompt_version"]
ALIGN_VERSION = ALIGN_Q["prompt_version"]

# Several sentences share one state, so the prose is paid for once.
CHUNK = 8

SOURCES = ("description", "sql_comment", "meta", "manual")


@dataclass
class Claim:
    claim_id: str
    subject: str                     # model uid, or "<uid>.<column>"
    subject_name: str
    text: str
    source_kind: str                 # description | sql_comment | meta | manual
    source_ref: str = ""             # path, or path:line
    status: str = "active"           # active | suppressed
    confidence: float | None = None  # how sure the extractor was this IS a claim
    citation: str = ""               # an authority this claim names, if any
    meta: dict = field(default_factory=dict)


def claim_id(subject: str, text: str) -> str:
    """Stable across rewording of everything EXCEPT this claim.

    A verdict is attached to this id, so it must not move when a neighboring sentence changes.
    Normalized on whitespace and case so reflowing a paragraph does not orphan a ruling.
    """
    norm = " ".join(text.lower().split())
    return hashlib.sha256(f"{subject}\x00{norm}".encode()).hexdigest()[:16]


# *** AN UNANCHORED CITATION PATTERN MATCHES EVERY DATE IN THE REPO. ***
# `26-09-19` is not a statute. Measured: without the authority marker this found 46 "citations"
# in one project, all of them dates. The marker is what makes a number a citation.
_CITE = re.compile(
    r"(?:C\.R\.S\.?|U\.S\.C\.?|CFR|§)\s*§?\s*(\d{1,2}-\d{1,3}-\d{1,3}(?:\([0-9a-zA-Z]+\))*"
    r"|\d{1,3}\s*§?\s*\d+(?:\([0-9a-zA-Z]+\))*)",
    re.IGNORECASE)


def citation_in(text: str) -> str:
    m = _CITE.search(text or "")
    return m.group(0).strip() if m else ""


# *** A SEMICOLON JOINS TWO CLAIMS AS SURELY AS A FULL STOP. ***
# Measured: "d_class_cn is the readable class; d_class and prop_class are numeric codes" survived
# as ONE claim, and the compound read `contradicts` at 0.92 when neither half is contradicted. The
# whole point of this module is that compound claims produce coin flips, and a splitter that only
# knows about full stops leaves half of them joined.
_SPLIT = re.compile(r"(?<=[.!?;])\s+(?=[A-Za-z`\"'*])")
_ABBREV = ("e.g.", "i.e.", "cf.", "vs.", "etc.", "C.R.S.", "U.S.C.", "approx.", "no.", "fig.")


# *** A SENTENCE THAT OPENS WITH A BARE PRONOUN IS NOT INDEPENDENTLY CHECKABLE. ***
# Found by running this method on assay's own source. "dbt reports that a test passed. It never
# reports that a test was INCAPABLE of failing." -- split on the full stop, the second sentence
# reads as a claim about the FUNCTION, and it was judged `contradicts` at 0.73. The subject is
# dbt, one sentence back.
#
# Splitting prose destroys antecedents, and a claim whose subject is elsewhere cannot be judged
# alone. So it is carried forward and joined to the sentence it depends on, rather than asked.
_LEADING_PRONOUN = re.compile(
    r"^(it|they|this|that|these|those|its|their)\b", re.IGNORECASE)


def _needs_its_antecedent(sentence: str) -> bool:
    return bool(_LEADING_PRONOUN.match(sentence.strip()))


def sentences(text: str, min_len: int = 24) -> list[str]:
    """Prose split into candidate claims.

    Deliberately crude and deliberately NOT a model call: splitting text is code's job, and the
    only cost of a bad split is a sentence the judgment then rejects.
    """
    if not text:
        return []
    out, buf = [], ""
    for part in _SPLIT.split(" ".join(text.split())):
        buf = f"{buf} {part}".strip() if buf else part
        if any(buf.lower().endswith(a) for a in _ABBREV):
            continue                       # the split landed inside an abbreviation
        out.append(buf)
        buf = ""
    if buf:
        out.append(buf)

    # Rejoin anything that opens with a bare pronoun onto what it refers back to.
    joined: list[str] = []
    for part in out:
        if joined and _needs_its_antecedent(part):
            joined[-1] = f"{joined[-1]} {part}"
        else:
            joined.append(part)
    return [s for s in joined if len(s) >= min_len]


def comment_sentences(sql: str) -> list[tuple[str, int]]:
    """(sentence, line) for every `--` comment line, so a claim can point at where it was written.

    Comments carry the real documentation in most projects. They also carry rationale, incident
    notes and instructions to maintainers, which is exactly why what comes out of here is a
    CANDIDATE and not a claim.
    """
    out: list[tuple[str, int]] = []
    run, start = [], 0
    for i, line in enumerate(( sql or "").splitlines(), 1):
        t = line.strip()
        if t.startswith("--"):
            body = t.lstrip("-").strip()
            if not run:
                start = i
            if body:
                run.append(body)
            continue
        if run:
            for s in sentences(" ".join(run)):
                out.append((s, start))
            run, start = [], 0
    for s in sentences(" ".join(run)):
        out.append((s, start))
    return out


_PUNCT = re.compile(r"[^a-z0-9 ]+")


def near_duplicate_key(subject: str, text: str) -> tuple[str, str]:
    """A key that is equal for the same sentence written twice with different punctuation.

    *** THE SAME CLAIM FROM A HEADER AND FROM A SCHEMA DESCRIPTION WAS COUNTED TWICE. ***
    Measured in the field: `int_water_diversion_history` came back contradicted at 0.95 AND at
    0.93, `stg_cdss_dams` at 0.95 and 0.93, each pair one sentence a person had written in two
    places. `claim_id` already collapses byte-identical text, so what survived were the pairs
    differing by a backtick, a trailing full stop or a comma.

    *** IT STRIPS PUNCTUATION AND NOTHING ELSE. ***
    Dropping stop words or stemming would collapse two claims that genuinely differ, and the
    louder a normalizer is the more quietly it loses one of them. Scoped to the model, because
    the same sentence about two models is two claims.
    """
    return subject, " ".join(_PUNCT.sub(" ", (text or "").lower()).split())


def candidates(project, digests, shared: set[str] | None = None) -> list[Claim]:
    """Every sentence that could be a claim, with where it came from. No judgment yet.

    `shared` is the set of descriptions repeated across models. A description 32 models carry is
    not a claim about any one of them, and asking about it 32 times pays to be told so 32 times.
    """
    shared = shared or set()
    out: list[Claim] = []
    for uid, m in project.models.items():
        desc = (m.description or "").strip()
        if desc and desc not in shared:
            for s in sentences(desc):
                out.append(Claim(claim_id(uid, s), uid, m.name, s, "description",
                                 m.path, citation=citation_in(s)))
        d = digests.get(uid)
        if d is None or not getattr(d, "ok", False):
            continue
        for s, line in comment_sentences(m.compiled or ""):
            out.append(Claim(claim_id(uid, s), uid, m.name, s, "sql_comment",
                             f"{m.path}:{line}", citation=citation_in(s)))
    return out


def kind_questions(items: list[Claim]) -> dict:
    # *** THE ID MUST CARRY ITS OWN BANK'S PREFIX, AND FOR EIGHT RELEASES IT CARRIED A NEIGHBOR'S.
    # This is `sentence_is_a_claim`, whose bank declares `id_prefix: sentence`. It filed under
    # `claim__N`, which `claim_alignment` declares, so every resolution from a stored id to a
    # family landed one family over: verdicts counted toward the wrong gate floor, `review -i`
    # printed the wrong family name, and 0.24.0's version filter hid 231 findings on that mapping.
    # `contracts.id_prefix_conflicts` asserts the invariant that was missing.
    return {f"sentence__{i}": choice({"sentence": c.text, **KIND_Q["instructions"]},
                                     KIND_Q["criteria"])
            for i, c in enumerate(items)}


def kind_state(model_name: str, items: list[Claim], purpose: str = "",
               vocab: dict | None = None) -> dict:
    st: dict = {"model": model_name,
                "sentences_under_judgment": [c.text for c in items]}
    if purpose:
        st["what_this_model_is_for"] = purpose[:300]
    if vocab:
        st["vocabulary"] = vocab
    return st


def align_question() -> dict:
    # This is `claim_alignment`, whose bank declares `id_prefix: claim`. It filed under `align`,
    # which `same_concept` declares. See `kind_questions` above: same defect, one family over.
    return {"claim": choice(ALIGN_Q["instructions"], ALIGN_Q["criteria"])}


def align_state(claim: Claim, evidence: dict, vocab: dict | None = None) -> dict:
    """*** THE SMALLEST STATE THAT CAN ANSWER THE QUESTION. ***

    Measured on one question: the structural claim alone read 0.96, and the same claim plus one
    CORRECT extra sentence read 0.47. Nothing was wrong with the extra sentence; it was extra.
    TypeSafe say it outright -- unrelated detail acts as a distractor -- so this sends one claim
    and only the evidence bearing on it.
    """
    st = {"model": claim.subject_name, "claim": claim.text,
          "evidence": {k: v for k, v in evidence.items() if v}}
    # *** `assay config` SAYS THE VOCABULARY GOES TO EVERY QUESTION. ***
    # It went to seven families and not to the two added this week, which made that line false.
    # A claim is exactly where a project's own words matter most: "division" means something
    # specific here, and a judgment that does not know it is guessing.
    if vocab:
        st["vocabulary"] = vocab
    return st


# Only these kinds are claims worth checking. The rest are real sentences doing other jobs, and
# treating an incident note as a claim about today's code is how a findings list fills with history.
CHECKABLE = ("claim_about_output", "claim_about_a_rule")


_IDENT = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`|\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b")


def mentioned_identifiers(text: str) -> set[str]:
    """Column-shaped words in a claim. Snake_case or backticked, because those are what a person
    writing about a column actually types."""
    return {(a or b).lower() for a, b in _IDENT.findall(text or "")}


_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w.])")


def unanswerable_from_sql(claim_text: str, ev: dict) -> str:
    """Why this claim cannot be judged from the code, or "" when it can.

    *** ABSENCE OF EVIDENCE WAS BEING RETURNED AS CONTRADICTION, AGAIN. ***
    Measured over 1,780 claims on a 358-model warehouse: 389 came back `contradicts` (22%), and
    both read by hand were wrong at p=0.95. `stg_cdss_dams` claims something about the Python
    findings layer -- `ponds_covered` appears nowhere in its SQL -- and the model cannot speak to
    it at all. `int_water_diversion_history` claims "the FULL diversion record, 1886 to 2026", the
    data is exactly 1886-2026, and the SQL derives those years without ever stating them.

    Both are the shape claim_alignment v4 already addressed with "absence is not disagreement",
    which holds for one claim at a time and stops holding at scale. So the two cases that cannot
    be answered are now RECOGNIZED BEFORE THE CALL rather than argued into the criteria, which
    also means they cost nothing.
    """
    named = ev.get("columns_this_claim_names") or {}
    missing = ev.get("identifiers_in_the_claim_found_nowhere_in_this_model_or_its_parents") or []
    # NAMES SOMETHING, AND NONE OF IT IS HERE. A claim naming no identifier at all is ordinary
    # prose about the model and stays answerable; this is only the case where the claim points at
    # specific things and every one of them is absent.
    if missing and not named:
        return (f"every identifier this claim names is absent from the model and its parents "
                f"({', '.join(missing[:4])}), so the code can neither support nor contradict it")
    # A LITERAL VALUE IS A STATEMENT ABOUT ROWS. SQL structure cannot settle "1886 to 2026" or
    # "more than 90% of rows"; counting can, which is what `completeness --verify` is for. Sending
    # it to a text judge asks a question the evidence cannot answer in either direction.
    if _NUMBER.search(claim_text or "") and not named:
        return ("this claim asserts a literal value, which is a statement about ROWS rather than "
                "about the SQL. Count it -- `assay completeness --verify` -- rather than reading it")
    return ""


def evidence_for(uid: str, project, digests, schema, observed: dict | None = None,
                 claim_text: str = "") -> dict:
    """What bears on THIS claim, and nothing else.

    *** A GENERIC EVIDENCE DUMP ANSWERS THE WRONG QUESTION CONFIDENTLY. ***
    Measured: a claim about `d_class_cn` read `contradicts` at 0.97 because the evidence listed
    thirty produced columns and the first twenty-five source columns, and `d_class_cn` was in
    neither. The model could not see the thing it was asked about, so it did what TypeSafe
    document it does -- returned a confident non-answer.

    So the claim picks its own evidence: identifiers the claim NAMES are looked up specifically,
    in what this model produces and in what it reads, with the expression behind each. That keeps
    the state small, which is the other measured rule -- 0.96 alone against 0.47 with one extra
    correct sentence.
    """
    d = digests.get(uid)
    m = project.models.get(uid)
    if d is None or m is None or not d.ok:
        return {}
    produced = list(schema.columns(uid).names)
    wanted = mentioned_identifiers(claim_text)

    named: dict = {}
    for c in produced:
        if c.lower() in wanted:
            expr = (d.output_exprs or {}).get(c.lower()) or (d.output_exprs or {}).get(c)
            named[c] = {"produced_here": True, "expression": (expr or "")[:220] or None}
    for parent in (m.parents or [])[:8]:
        try:
            pname, pcols = project.name_of(parent), schema.columns(parent).names
        except Exception:                                               # noqa: BLE001, S112
            continue
        for c in pcols:
            if c.lower() in wanted and c not in named:
                named[c] = {"read_from": pname}

    ev: dict = {
        "columns_this_model_produces": produced[:60],
        "filters_it_applies": [x for x in (d.predicates_atomic or [])
                               if x.strip() not in ("1 = 1", "TRUE", "true")][:14],
        "it_reads": [project.name_of(x) for x in (m.parents or [])][:10],
    }
    if named:
        ev["columns_this_claim_names"] = named
    # An identifier the claim names that exists NOWHERE is itself the answer, so say so rather
    # than letting silence be read as contradiction.
    unknown = sorted(w for w in wanted if w not in {c.lower() for c in named}
                     and w not in {c.lower() for c in produced})
    if claim_text and unknown:
        ev["identifiers_in_the_claim_found_nowhere_in_this_model_or_its_parents"] = unknown[:10]
    if d.group_by:
        ev["it_groups_by"] = list(d.group_by)[:10]
    if getattr(d, "has_qualify", False):
        ev["it_uses_qualify"] = True
    vals = (observed or {}).get(uid) or {}
    if vals:
        ev["values_actually_present"] = {k: v[:12] for k, v in list(vals.items())[:4]}
    return ev
