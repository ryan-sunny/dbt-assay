"""From what the check FOUND to what this project should therefore configure.

*** THE MISSING HALF OF ONBOARDING. ***
`guide` teaches what a vocab term is for, `init` writes defaults, `config` shows what resolved, and
nothing at all goes from 257 findings to the four lines of YAML that would settle sixty of them. A
person who built the tool makes that jump in an afternoon. Nobody else does, and "here are your
findings, and here is an essay on how config works, now connect them" is not onboarding.

It is derivable, and it has already been done by hand on this warehouse: the first waiver in
`audit.yml` is a verbatim descendant of an agent ruling sitting in `adjudications`. Somebody read a
ruling and typed it into the config. This automates a path that has already produced good config
here; it does not invent a new one.

*** IT PROPOSES THE CANDIDATE AND THE MEASUREMENT. IT NEVER PROPOSES THE MEANING. ***
It may say `section_id` is joined in 65 hops across 24 models and is absent from a 15-term vocab.
It may not say what `section_id` means. `means:` and `implies:` arrive EMPTY with the evidence
underneath them, and that is not a style preference. A plausible vocab block written from model
names looks like knowledge, is not, and then rides along with every judged question from that point
on -- the tool's worst failure shipped as a feature, and the most confident-sounding output it
produces. The rule is already in the skill for agents. The command holds itself to it too.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

# A reason is "the same reason" when its shape matches, not its wording. Rulings are prose and two
# people describing one defect never type the same sentence.
_STOP = {"the", "a", "an", "is", "it", "this", "that", "and", "or", "not", "of", "to", "in", "on",
         "for", "with", "as", "at", "by", "from", "are", "was", "be", "which", "its", "so", "but"}


# *** A LABEL IS NOT A REASON, AND THE STORE ALREADY KNOWS WHICH IS WHICH. ***
# `source='label'` rows are the project's OWN declarations read back as verdicts, and their note is
# a stub the tool generated: on the field warehouse, 26 of 38 disagreements read "the project
# asserts out" or "the project asserts in", across 22 unrelated questions. They are true and they
# say nothing about why anything is wrong.
#
# Left in, they break both rules that use a reason. The repeated-reason rule collapses them into
# one 13-subject "finding" whose entire content is the word `asserts`. The waiver rule drafts 26
# waivers whose `reason:` is `the project asserts out` -- a permanent silence justified by a
# sentence nobody wrote, which is precisely the "never propose a waiver without a reason drawn
# from a real ruling" line.
#
# The filter is the SOURCE COLUMN, not the length of the text. A length cutoff would be a number
# picked off one warehouse's histogram that quietly drops a real short reason on the next one.
_WROTE_A_REASON = ("human", "agent")


def _shape(text: str) -> str:
    """The content words of a reason, ordered and deduped: two wordings of one defect collide."""
    words = [w for w in re.findall(r"[a-z_]{3,}", (text or "").lower()) if w not in _STOP]
    return " ".join(sorted(set(words))[:12])


def live_pairs(findings) -> set:
    """`{(check, subject)}` from findings, whether they are objects or artifact dicts.

    Three callers hold findings in two shapes. Each normalizing its own would be three spellings
    of one fact, which is the defect this tool reports in other people's warehouses -- and it
    would drift the first time a finding grew a field.
    """
    out = set()
    for f in findings or []:
        if isinstance(f, dict):
            c, subj = f.get("check"), f.get("subject")
        else:
            c, subj = getattr(f, "check", None), getattr(f, "subject", None)
        if c and subj:
            out.add((str(c), str(subj)))
    return out


@dataclass
class Suggestion:
    """One proposal, with what was measured about it and nothing that was not."""
    section: str            # vocab | waivers | questions | explanations | open
    key: str
    headline: str
    measured: list = field(default_factory=list)   # each entry is a measurement, not a judgment
    draft: str = ""         # YAML to paste, with every meaning field left empty
    basis: str = ""         # which derivation rule produced it
    rank: float = 0.0
    # *** WHAT THIS RULE REFUSES TO DECIDE. ***
    # A signal that points at two different files is reported as pointing at two different files.
    decide: str = ""

    def as_dict(self) -> dict:
        return {"section": self.section, "key": self.key, "headline": self.headline,
                "measured": list(self.measured), "draft": self.draft, "basis": self.basis,
                "rank": round(self.rank, 3), "decide": self.decide}


# --------------------------------------------------------------------------- vocab

def _majority_sentence(project, col: str) -> tuple[str, str, list]:
    """(sentence, where, others) when MORE THAN HALF of the models describing `col` use one
    sentence, else ("", "", variants). A majority is selected and says so with its count; a
    plurality is not, because picking one of several contested sentences is an arbitrary pick."""
    if project is None:
        return "", "", []
    from .subjects import described
    said: dict = {}
    for m in project.models.values():
        t = described(m).get(col)
        if t:
            said.setdefault(" ".join(str(t).split()), []).append(m.name)
    total = sum(len(v) for v in said.values())
    if not total:
        return "", "", []
    ranked = sorted(said.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    top, where = ranked[0]
    others = [f"{len(w)} model(s): {t[:120]}" for t, w in ranked[1:4]]
    if len(where) * 2 > total:
        return top, f"{len(where)} of {total} models describing it, e.g. {min(where)}", others
    return "", "", [f"{len(w)} model(s): {t[:120]}" for t, w in ranked[:4]]


def _vocab_from_joins(store, cfg, run_id: str | None, project=None) -> list[Suggestion]:
    """Columns the warehouse joins on constantly that the vocab has never heard of.

    *** SORTED BY THE NUMBER THE HEADLINE READS FIRST. ***
    It ranked on `hops x models` and led with hops, so the list ran 65, 32, 40 -- a correct
    ordering that reads as a broken one, and a reader who cannot see the sort key has to take the
    order on trust. Reported from the field alongside the same shape in the probe line.

    Fixing the legibility fixed the ranking too, because the product was answering the wrong
    question. A vocabulary term is worth writing when it is SHARED: the payoff is every judged
    question that carries it, and those follow the models, not the joins. `city` is joined 40
    times across 4 models -- ten joins inside a handful of models is one team's local habit.
    `geom` is 32 joins across 19 models, and that is nineteen places that need the same word to
    mean the same thing. Models first, hops as the tiebreak, and `city` correctly leaves the top.

    *** THE SET DIFFERENCE HAPPENS BEFORE THE RANKING, NOT AFTER. ***
    Ranking 400 columns and then dropping the configured ones off the top produces a list whose
    ordering was decided by rows that are not in it, and a "top 5" that is the top 5 of something
    else. It also hides the number worth printing: how many of the most-joined columns ARE already
    covered, which is the only evidence that a vocab is doing its job.
    """
    rows = store.con.execute(
        "select joined_on, child_name from edge_facts "
        "where joined_on is not null and joined_on <> '' "
        + ("and run_id = ?" if run_id else "") + " order by parent_name, child_name",
        [run_id] if run_id else []).fetchall()
    hops: Counter = Counter()
    models: dict = defaultdict(set)
    for joined_on, child in rows:
        try:
            cols = json.loads(joined_on) or []
        except (ValueError, TypeError):
            continue
        for c in cols:
            c = str(c).strip().lower()
            if not c:
                continue
            hops[c] += 1
            models[c].add(child)

    known = {str(k).lower() for k in (cfg.vocab or {})}
    covered = sorted(c for c in hops if c in known)
    out = []
    for col in sorted(hops, key=lambda c: (-len(models[c]), -hops[c], c)):
        if col in known:
            continue
        n_hops, n_models = hops[col], len(models[col])
        if n_hops < 3 or n_models < 2:
            continue                       # joined once or twice is not a shared term yet
        out.append(Suggestion(
            section="vocab", key=col,
            # Models dominate and hops break the tie, in one number, so the rank is the order a
            # reader sees rather than a second opinion about it.
            rank=float(n_models * 10_000 + n_hops),
            basis="shared across the most models, absent from vocab",
            headline=f"{n_models} models join on `{col}` ({n_hops} hops) "
                     f"and the vocab does not define it",
            # *** ONLY WHAT VARIES BETWEEN ROWS BELONGS ON A ROW. ***
            # The coverage line and the "assay will not fill this in" paragraph are the same on
            # every one of these, and printed 32 times they stop being read -- so they move to
            # the rule, once, where the instruction is still an instruction.
            # The headline already says the number and so does the comment in the draft. A third
            # copy is not more evidence; it is the same fact spelled three times, which is the
            # defect this tool reports in other people's warehouses.
            decide=(f"vocab defines {len(known)} term(s), {len(covered)} of which are among the "
                    f"columns actually joined on.\n"
                    f"  assay never WRITES a meaning. Where most of the models that describe a "
                    f"column use one sentence,\n"
                    f"  `means:` quotes it and cites them -- theirs, not assay's, and yours to "
                    f"edit. Otherwise it arrives EMPTY:\n"
                    f"  a plausible guess written from a model name looks exactly like knowledge "
                    f"and then rides along with\n"
                    f"  every judged question from that point on."),
            measured=_described_as(project, col),
            draft=_join_draft(project, col, n_models, n_hops)))
    return out


def _described_as(project, col: str) -> list:
    sentence, where, others = _majority_sentence(project, col)
    if sentence:
        return [f"means: selected -- the sentence {where} use"] + \
               [f"other wordings: {o}" for o in others]
    return [f"described as: {o}" for o in others]


def _join_draft(project, col: str, n_models: int, n_hops: int) -> str:
    """The vocab block. `means:` carries the project's own majority sentence when there is one,
    quoted and cited; otherwise it stays empty, as every other rule leaves it."""
    sentence, where, _o = _majority_sentence(project, col)
    means = sentence.replace('"', "'")[:220] if sentence else ""
    cite = (f"    # means: quoted from {where}; edit it, it is theirs not assay's\n"
            if sentence else "")
    return (f"vocab:\n  {col}:\n    means: \"{means}\"\n    implies: \"\"\n{cite}"
            f"    # measured: {n_models} models, {n_hops} hops")


def _vocab_from_descriptions(cfg, project) -> list[Suggestion]:
    """Terms the project has ALREADY defined, in its own column descriptions.

    *** ASSAY WILL NOT WRITE A DEFINITION, AND THIS IS NOT ASSAY WRITING ONE. ***
    Every other vocab rule returns `means:` empty and says so in capitals, because a plausible
    sentence written from a model name looks exactly like a sentence somebody chose and then
    rides along with every judged question forever.

    A column description is different in the one way that matters: a person wrote it, about this
    warehouse, on purpose. Carrying it across is quoting them, not guessing -- and it is quoted
    verbatim with the file it came from, so the draft can be checked against its source in one
    look. "pulling from column descriptions first would be ideal obvi."

    *** A WORD DESCRIBED THE SAME WAY EVERYWHERE IS THE ONLY SAFE ONE TO LIFT. ***
    Where two models describe a column differently, the disagreement is the finding
    (`models_disagree_about_a_column`) and there is no single sentence to propose. Those are
    skipped here rather than resolved by picking one, which would be an arbitrary pick in the
    tool that checks other people's SQL for arbitrary picks.
    """
    from .subjects import described
    known = {str(k).lower() for k in (cfg.vocab or {})}
    by_col: dict = {}
    for uid, m in (getattr(project, "models", None) or {}).items():
        for col, text in described(m).items():
            by_col.setdefault(col, {}).setdefault(" ".join(str(text).lower().split()),
                                                  []).append((uid, m, str(text).strip()))
    out = []
    for col in sorted(by_col):
        if col in known:
            continue
        variants = by_col[col]
        if len(variants) != 1:
            continue                   # described two ways: that is a finding, not a definition
        uses = next(iter(variants.values()))
        if len(uses) < 2:
            continue                   # described once, in one model, is a comment, not a term
        # The earliest by name, so two runs over one project propose the same sentence.
        _uid, m, text = min(uses, key=lambda r: r[1].name)
        out.append(Suggestion(
            section="vocab", key=col,
            # Ranked below the join rule's scale on purpose: this proposes WORDS somebody already
            # wrote, and the join rule proposes the ones the warehouse actually turns on.
            rank=float(len(uses)),
            basis="already defined in this project's own column descriptions",
            headline=f"`{col}` is described identically in {len(uses)} model(s) and the vocab "
                     f"does not carry it",
            measured=[f"described in {len(uses)} model(s), all the same sentence",
                      f"first written in {m.name} ({m.path})"],
            decide="",
            # The ONE draft in this module that arrives with `means:` filled in, because the
            # sentence is theirs and is quoted rather than composed.
            draft=f"vocab:\n  {col}:\n    means: \"{text[:220]}\"\n    implies: \"\"\n"
                  f"    # quoted verbatim from {m.name}'s schema.yml, not written by assay"))
    return out


def _vocab_from_contradicted_names(store) -> list:
    """A column whose OBSERVED uniqueness contradicts what its name promises.

    *** "HAS DUPLICATES" IS NOT THE SIGNAL. HOW NEARLY IT IS A KEY IS THE SIGNAL. ***
    Every `*_id` with a repeat matches the first rule, and on the field warehouse that is 55
    columns, 45 of which identify a minority of their rows. `_dlt_load_id` holds 3 distinct values
    in 1,744,203 rows: it is a batch stamp, the name is odd, and nobody has ever been misled by it.
    Reporting it is how a list of exceptions becomes a list.

    The dangerous one is the opposite shape. `incident_id` is 19,566 distinct in 19,628 rows --
    99.68% unique. It passes every casual inspection, every spot check, and every small sample,
    and it is not a key. That is the `case_number` shape exactly: a name whose membership is not
    what it suggests, and the duplicates hide in the last 0.3%.

    So the cut is at "identifies MOST rows": above half, a reader will reasonably treat it as
    identifying a row and be wrong some of the time. Below half it plainly does not, and the name
    being poor is a different complaint than this one. Ranked by nearness to unique, so the ones
    that are hardest to catch by eye come first.
    """
    # *** ONE ROW PER (RELATION, COLUMN): `observed_keys` IS A HISTORY. ***
    # The probe appends, so a column probed three times is three rows, and grouping without this
    # reports one column three times and ranks it as though it were three findings.
    rows = store.con.execute("""
        select relation, column_name, row_count, distinct_ct, detail
        from (select *, row_number() over (
                  partition by relation, column_name
                  order by observed_at desc, row_count desc) rn
              from observed_keys where status = 'has_duplicates' and row_count > 0)
        where rn = 1 order by relation, column_name""").fetchall()

    by_col: dict = defaultdict(list)
    for rel, col, rc, dc, detail in rows:
        col = (col or "").lower()
        if not re.search(r"(_id|_key|_pk|_no|_number)$", col):
            continue
        ratio = (dc or 0) / rc
        if ratio < 0.5:
            continue          # it never identified most rows; the name is poor, not contested
        by_col[col].append((rel, rc, dc, ratio, detail))

    out = []
    for col, obs in sorted(by_col.items()):
        obs.sort(key=lambda o: (-o[3], o[0]))
        best = obs[0]
        m = [f"{rel}: {dc:,} distinct in {rc:,} rows = {ratio:.2%} unique, "
             f"{rc - dc:,} duplicate row(s)" for rel, rc, dc, ratio, _d in obs[:4]]
        if len(obs) > 4:
            m.append(f"...and {len(obs) - 4} more relation(s)")
        out.append(Suggestion(
            section="vocab", key=col, rank=best[3] * 100.0,
            basis="named like a key, and nearly -- but not -- unique",
            headline=f"`{col}` is {best[3]:.2%} unique in {best[0]} and is named like a key",
            measured=m,
            decide=("A spot check of these would pass. The duplicates are in the fraction a "
                    "sample does not reach, which is\n"
                    "  what makes this shape worth a definition: a name is not a grain. What "
                    "does the column actually\n"
                    "  enumerate, and what identifies a row where it repeats?"),
            draft=f"vocab:\n  {col}:\n    means: \"\"\n    implies: \"\"\n"
                  f"    # measured: {best[2]:,}/{best[1]:,} distinct in {best[0]}"))
    return out


# --------------------------------------------------------------------------- descriptions

# A judged answer is worth drafting from only when it was not close. Below this the draft would
# carry a coin flip dressed as a sentence.
_DRAFT_CONFIDENCE = 0.6

# `column_role`'s options, as the noun a description opens with. The option's own criterion text
# is the same sentence for every dimension in every warehouse, so it says nothing about THIS one;
# the role is worth one word and the expression is worth the rest.
_ROLE_WORD = {"identifier": "Identifier", "foreign_key": "Foreign key", "measure": "Measure",
              "dimension": "Dimension", "event_time": "When the real-world event happened",
              "audit_time": "When the pipeline loaded or refreshed the row",
              "status_flag": "Status flag", "free_text": "Free text", "attribute": "Attribute",
              "geometry": "Geometry"}


def _composed(c, exprs: dict, roots: dict, nulls: dict) -> tuple[str, list]:
    """(sentence, what it was built from) for one column, or ("", []) when nothing is recorded.

    Three parts, each from something assay holds: the judged role as one word, the column's own
    expression quoted from the compiled SQL (or what it is carried from), and the judged meaning of
    a NULL in the option's own words. The expression is the specific part -- "A dimension, derived
    by an expression" is true of half of every warehouse and helps nobody.
    """
    bits, facts = [], []
    if c.role and (c.role.confidence or 0) >= _DRAFT_CONFIDENCE and c.role.value in _ROLE_WORD:
        bits.append(_ROLE_WORD[c.role.value])
        facts.append(f"role {c.role.value} @{c.role.confidence:.2f}")
    kind = str(c.provenance.value)
    expr = " ".join(str((exprs or {}).get(c.name) or "").split())
    root = (roots or {}).get(c.name.lower()) or ""
    if kind == "null_placeholder":
        bits.append("NULL in every row one arm of a union contributes (a CAST(NULL) placeholder)")
        facts.append("provenance null_placeholder")
    elif kind in ("carried", "from_source"):
        src = root if isinstance(root, str) and root and root != "column" else ""
        bits.append(f"Carried unchanged from `{src}`" if src else "Carried unchanged from upstream")
        facts.append(f"provenance {kind}")
    elif expr and expr.lower() != c.name.lower():
        shown = expr if len(expr) <= 140 else expr[:137] + "..."
        verb = {"defaulted": "Defaulted here", "aggregated": "Aggregated here",
                "ranked": "Ranked here", "constant": "The same literal on every row"}.get(
                    kind, "Computed here")
        bits.append(f"{verb} as `{shown}`" if kind != "constant" else f"{verb}: `{shown}`")
        facts.append(f"expression from the compiled SQL ({kind})")
    if c.null_meaning and (c.null_meaning.confidence or 0) >= _DRAFT_CONFIDENCE \
            and c.null_meaning.value in nulls and c.null_meaning.value != "cannot_tell":
        w = " ".join(str(nulls[c.null_meaning.value].get("what", "")).split()).rstrip(".")
        if w:
            bits.append(f"A NULL means {w[0].lower() + w[1:]}")
            facts.append(f"null {c.null_meaning.value} @{c.null_meaning.confidence:.2f}")
    if not facts:
        return "", []
    return ". ".join(b.rstrip(".") for b in bits) + ".", facts


def _descriptions(project, entries, schema, digests=None) -> list[Suggestion]:
    """A draft for every column nobody has described, built from what assay already holds.

    *** SELECTION, NEVER GENERATION -- THE RULE `claims` ALREADY ENFORCES. ***
    Every word in a draft is one of three things, in this order, and the draft says which:
      1. QUOTED from upstream: the column passes through from a model whose own description a
         person wrote. That sentence is theirs, and it is still true here.
      2. QUOTED from elsewhere: the same column is described one way everywhere else in the project.
      3. COMPOSED from recorded facts: the column's judged role and null meaning, each the option's
         own criterion text, and the parser's sentence for where the value comes from. No word is
         written for the occasion, and a judgment below 0.6 is left out rather than drafted from.
    It is a PROPOSAL, keyed to that evidence and written to a separate file. It never touches
    schema.yml; a person edits it and puts it there.
    """
    from .contracts import QUESTIONS
    from .subjects import described
    nulls = (QUESTIONS.get("null_meaning") or {}).get("criteria") or {}
    by_rel = {str(r).replace('"', "").lower(): u
              for u, r in (getattr(schema, "relation", {}) or {}).items() if r}
    said: dict = {}
    for m in project.models.values():
        for col, text in described(m).items():
            said.setdefault(col, {}).setdefault(" ".join(str(text).split()), []).append(m.name)
    out = []
    for e in entries or []:
        m = project.models.get(e.uid)
        if m is None:
            continue
        d = (digests or {}).get(e.uid)
        have = described(m)
        rows, cites = [], []
        for c in e.columns:
            if c.name in have:
                continue
            text, how = "", ""
            origin_uid = by_rel.get(str(c.provenance.origin or "").replace('"', "").lower())
            parent = project.models.get(origin_uid) if origin_uid else None
            if c.provenance.value in ("carried", "from_source") and parent is not None \
                    and described(parent).get(c.name):
                text = " ".join(str(described(parent)[c.name]).split())
                how = f"quoted from {parent.name}, which it passes through from unchanged"
            elif len(said.get(c.name, {})) == 1:
                sentence, where = next(iter(said[c.name].items()))
                text, how = sentence, (f"quoted: described this way in {len(where)} other "
                                       f"model(s), e.g. {min(where)}")
            else:
                text, facts = _composed(c, d.output_exprs if d else {},
                                        d.output_roots if d else {}, nulls)
                if not text:
                    continue                 # nothing recorded to draft from; not drafted
                how = "composed from recorded facts: " + ", ".join(facts)
            rows.append({"name": c.name, "description": text})
            cites.append(f"{c.name}: {how}")
        if not rows:
            continue
        import yaml as _yaml
        body = _yaml.safe_dump({"models": [{"name": e.name, "columns": rows}]},
                               sort_keys=False, width=100, allow_unicode=True)
        quoted = sum(1 for x in cites if ": quoted" in x)
        out.append(Suggestion(
            section="descriptions", key=e.name, rank=float(e.marts * 1000 + len(rows)),
            basis="drafted from what assay already holds, for columns nobody described",
            headline=(f"{e.name}: {len(rows)} undescribed column(s) drafted, {quoted} quoted "
                      f"from a sentence a person wrote"),
            measured=cites[:12] + ([f"...and {len(cites) - 12} more"] if len(cites) > 12 else []),
            decide=("A draft is a proposal. Each line says where its words came from; a quoted one "
                    "is somebody's sentence,\n"
                    "  a composed one is assay's recorded facts in its own option text. Edit, then "
                    "put it in schema.yml yourself --\n"
                    "  assay never writes to it, and a description nobody read is the defect "
                    "`description_contradicts_the_code` exists to find."),
            draft=f"# for {m.path.rsplit('/', 1)[0]}/schema.yml -- a DRAFT, edit before use\n"
                  + body))
    return out


# --------------------------------------------------------------------------- the refusal

def _clusters(store, cfg) -> dict:
    """`shape -> [(subject, question, reason)]`, from written rulings and from waiver reasons.

    ONE implementation, because two readers want the same clustering and opposite halves of it:
    the queue wants clusters that still fire, `effectiveness` wants the ones that stopped. Two
    copies of this query would drift the first time either half moved, which is the defect this
    module reports in other people's SQL.
    """
    seen: dict = defaultdict(list)
    for subj, q, note in store.con.execute(
            "select subject, question, note from adjudications "
            "where verdict in ('disagree', 'accept') and note is not null and note <> '' "
            f"and source in ({', '.join('?' * len(_WROTE_A_REASON))}) "
            "order by subject, question", list(_WROTE_A_REASON)).fetchall():
        sh = _shape(note)
        if sh:
            # *** ONE DISMISSAL, WRITTEN TWICE, IS ONE SUBJECT. *** A card files its verdict on the
            # model AND on each finding it showed; counted apart, one keypress read as "the same
            # reason given on 2 subjects" -- all five of the field's top suggestions were that.
            seen[sh].append((str(subj).split("::finding::")[0], q, note))
    for model, waivers in sorted((cfg.waivers or {}).items()):
        for w in waivers:
            sh = _shape(getattr(w, "reason", "") or "")
            if sh:
                seen[sh].append((model, getattr(w, "question", ""), getattr(w, "reason", "")))
    return {sh: items for sh, items in seen.items()
            if len({s for s, _q, _n in items}) >= 2}


def _repeated_reasons(store, cfg, live: set | None) -> list[Suggestion]:
    """One reason given on two or more subjects.

    *** AND THIS RULE REFUSES TO SAY WHICH FILE IT BELONGS IN. ***
    The spec files this under vocab, and the same evidence reads the other way just as well. On
    this warehouse "A UNION MEMBER EDGE CANNOT MULTIPLY" was given on two models, and the right
    response was NOT two waivers and NOT a vocab term -- it was a structural fix to the check,
    which landed in 0.15.0 and 0.21.1. A reason repeating means something upstream of the config
    is missing; whether that something is a word the checker lacks or a case the checker gets
    wrong is a question about the reason, and only a person reading it can answer it.

    Guessing here would be the expensive kind of wrong: a waiver silences the check on two models
    and leaves the third to be found in production, which is exactly what happened before the
    structural fix. So both readings are printed with what separates them, and neither is ranked
    above the other.

    *** AND A CLUSTER WHOSE FINDINGS ARE ALL GONE IS NOT A DECISION. ***
    The first version left the live check out, and the result inverted the ranking of the whole
    queue: a cluster grew MORE prominent the more successfully it had been fixed, because the
    number it ranked on was how many subjects had once been ruled on. On this warehouse the top
    two items under DECIDE FIRST were 8 subjects and 2 subjects with **zero** live findings
    between them -- both already repaired, one of them by the structural fix the item's own text
    cites. Six models were actually firing `hop_multiplies_rows` that day and none of them was
    in the queue.

    It is the mirror of the 0.24.0 defect. That one hid live evidence; this one promoted dead
    evidence to the top of the list a person reads first.

    So a cluster needs at least one subject that still produces a finding for that question. The
    resolved ones are not worthless -- "this fired on 8 models and no longer does" is evidence a
    question got better -- and that is what `assay effectiveness` is for, so they go there
    instead of being deleted.
    """
    out = []
    for sh, items in sorted(_clusters(store, cfg).items()):
        subjects = sorted({s for s, _q, _n in items})
        questions = sorted({q for _s, q, _n in items if q})
        sample = max((n for _s, _q, n in items), key=len)

        # *** WHICH OF THESE SUBJECTS STILL FIRES. ***
        still = sorted({sub for sub, q, _n in items if (q, sub) in (live or set())})
        if live is None:
            # An absent measurement is not a pass and it is not a failure either. Say which.
            note = ("no --target was given, so assay cannot tell which of these still fire. "
                    "Some may already be fixed.")
        elif not still:
            continue        # entirely resolved: it belongs in `effectiveness`, not in a queue
        else:
            note = (f"{len(still)} of {len(subjects)} still produce a finding today"
                    + (f" ({', '.join(s.split('.')[-1] for s in still[:3])}"
                       f"{'...' if len(still) > 3 else ''})" if still else ""))

        out.append(Suggestion(
            section="open", key=sh[:60],
            # *** RANKED ON WHAT IS STILL TRUE, NOT ON HOW MANY WERE EVER RULED ON. ***
            rank=float(len(still)) if live is not None else float(len(subjects)),
            basis="one reason, given on several subjects",
            headline=f"the same reason is given on {len(subjects)} subjects "
                     f"({', '.join(subjects[:3])}{'...' if len(subjects) > 3 else ''})",
            measured=[note,
                      f"{len(items)} occurrences across {len(subjects)} subjects",
                      f"question(s): {', '.join(questions) or 'unrecorded'}",
                      f"reason as written: {sample[:300]}"],
            decide=(
                "This points at two different files and assay will not pick between them.\n"
                "  vocab  -- if the reason names a THING this warehouse has and the checker has "
                "no word for.\n"
                "           Then one term covers these subjects and the next one too.\n"
                "  the check -- if the reason names a CASE the checker gets wrong.\n"
                "           Then a waiver silences it here and leaves the next occurrence to be "
                "found in production.\n"
                "  Ask: would a reader who knew this term still call the finding correct? Yes "
                "means vocab. No means the check is wrong.\n"
                "  A reason given on several subjects has gone the second way before: two "
                "models were waived for one reason and the checker was the thing that needed "
                "fixing, so the third occurrence was still waiting in production."),
            # No draft. There is nothing to paste until the question above is answered, and a
            # YAML block here would be something to copy -- which is the one thing this rule is
            # trying not to hand over.
            draft=""))
    return out


# --------------------------------------------------------------------------- waivers

def _waivers_from_acceptances(store, cfg) -> list[Suggestion]:
    """An `accept` is a waiver somebody already decided, with the reason they wrote.

    *** THIS PROPOSED WAIVERS FROM `disagree` RULINGS, WHICH SAY THE OPPOSITE. ***
    A disagreement is "the check misread the SQL": the fix is the check, or the dismissal the
    verdict already is. A waiver is "the check is right and I accept it". Drafting the second from
    the first wrote into audit.yml the negation of what the person said. Only a person's `accept`
    proposes a waiver, because an agent's is a reading, not a decision.
    """
    already = set()
    for key, ws in (cfg.waivers or {}).items():
        for w in ws:
            already.add((key if getattr(w, "applies_to", None) is None
                         else str(w.applies_to), getattr(w, "question", "")))
    out, seen = [], set()
    for subj, q, note, when, until in store.con.execute(
            """select subject, question, note, decided_at, coalesce(until, '') from (
                   select *, row_number() over (partition by subject
                                                order by decided_at desc) rn
                   from adjudications where source = 'human')
               where rn = 1 and verdict = 'accept' and coalesce(note, '') <> ''
               order by subject, question, decided_at""").fetchall():
        name = str(subj).split("::finding::")[0].split(".")[-1]
        if (name, q) in already or (name, q) in seen:
            continue
        seen.add((name, q))
        out.append(Suggestion(
            section="waivers", key=f"{name}:{q}", rank=1.0,
            basis="an accept ruling, whose reason is already written",
            headline=f"{name}: `{q}` was accepted -- correct, and left on purpose -- and only the "
                     f"store records it. A waiver puts the decision in git",
            measured=[f"accepted on {str(when)[:10]}" if when else "accepted",
                      f"reason as given: {str(note)[:300]}"]
                     + ([f"until {until}"] if until else []),
            draft=f"waivers:\n  {name}__{q}:\n    question: {q}\n    applies_to: {name}\n"
                  f"    reason: >\n      {str(note)[:400]}\n"
                  + (f"    until: {until}\n" if until else
                     "    until: \"\"   # A waiver with no expiry is a deletion. Pick the date "
                     "you would want to be asked again.\n")))
    return out


# --------------------------------------------------------------------------- questions

def _questions_unconfigured(cfg, firing: set) -> list[Suggestion]:
    """Checks that fired and that `audit.yml` does not name, with the block they need."""
    from .config import ACTIONS, shipped_block
    out = []
    # `unconfigured` already hands back the shipped opinion beside each name. Re-deriving it here
    # would be a second spelling of one fact, and the two would drift the first time either moved.
    for name, act in sorted(cfg.unconfigured(firing) or []):
        act = act or ""
        # *** AND THE OPINION IS NOT ALWAYS A VALUE `action:` ACCEPTS. ***
        # A thresholded default comes back as the sentence `queue above a threshold`, and pasting
        # that after `action:` produces a config that refuses to load. So the real shipped entry
        # is used where there is one, and the summary stays prose in the measurement line.
        block = shipped_block(name)
        if block:
            draft = "questions:\n" + "\n".join("  " + ln for ln in block.splitlines())
            draft += "\n  # this is the entry `assay init` ships for this check."
        else:
            draft = (f"questions:\n  {name}:\n    enabled: true\n    action: annotate\n"
                     f"    # assay ships no opinion for this one, so `annotate` is a starting\n"
                     f"    # point rather than a recommendation.")
        out.append(Suggestion(
            section="questions", key=name, rank=2.0,
            basis="fired, and the config does not name it",
            headline=f"`{name}` is firing and audit.yml does not say what to do about it, "
                     f"so it warns and cannot fail a build",
            measured=[f"shipped opinion: {act or 'none'}"
                      + ("" if act in ACTIONS or not act else " (a threshold, not a flat action)")],
            decide=("With no `questions:` entry the check falls back to severity, so a release "
                    "that adds a check never turns a\n"
                    "  green build red on its own. That is deliberate, and it also means the "
                    "shipped opinion is never applied\n"
                    "  until somebody writes it down. Measure before gating: the agreement line "
                    "for the family is below."),
            draft=draft))
    return out


def _questions_from_agreement(store, cfg) -> list[Suggestion]:
    """Per-family agreement, and what it says about gating that family.

    *** A VERDICT ABOUT v1 SAYS NOTHING ABOUT v4, AND THE FIRST VERSION OF THIS POOLED THEM. ***
    Reported from the field, on a rule written the same day. `hop_multiplies_rows` came back
    "agrees 1/11 (9%)", which reads as a check that is wrong ten times out of eleven -- and on
    the strength of it the advice was to stop queueing it.

    Every one of those ten disagreements was recorded under `assay.0.11.0`. The check was fixed
    structurally in 0.15.0 and 0.21.1, and the single verdict given AFTER the fix, at 0.17.0,
    agrees. The seven findings it raises today have never been ruled on at all. The rate was not
    the check's; it belonged to a version that stopped existing twenty-five releases ago.

    `apply_policy` was never fooled -- it reads `accuracy_by_family`, which counts only the
    shipping version, so a stale rate could not authorize anything. Only the ADVICE was fooled,
    which is worse in one respect: gating fails closed, and advice tells a person to switch off a
    check that had already been repaired. So this reads the same function, and a family with no
    verdicts at the shipping version has no rate rather than an old one.

    *** AND WHERE THERE IS NO MEASUREMENT, THIS SAYS SO RATHER THAN FALLING BACK. ***
    A rule that quietly defers to the shipped default when the numbers are thin produces a
    recommendation indistinguishable from a measured one. Silence has causes -- nobody ruled on
    this family, or people did and there are too few to mean anything -- and those are different
    situations with different next steps, so each is named.

    `unclear` never enters the denominator. Disagreement means the criteria are wrong; unclear
    means the state does not carry what the question asks. Different edits.
    """
    from . import __version__
    from .contracts import QUESTIONS
    shipping = {n: (q or {}).get("prompt_version", "") for n, q in QUESTIONS.items()}
    # Only verdicts given against the question that ships NOW. A structural check carries assay's
    # own version, because that is what moved when the check moved.
    current = store.accuracy_by_family(shipping, default=f"assay.{__version__}")

    # `unclear` is still counted, and still outside the denominator: it is evidence the question
    # cannot be answered from the state it was given, which is a different repair from a wrong
    # criterion. Counted at the shipping version too, or a family whose question was rewritten
    # carries forward unclears about the old one.
    # *** HUMAN ONLY, ON BOTH HALVES. ***
    # The old query filtered by neither version nor source, so an AGENT's ruling counted toward a
    # recommendation about what may gate a build -- against this project's own rule that agent
    # verdicts never authorize one. `accuracy_by_family` has always been human-only; this half
    # had to be told.
    rows = store.con.execute(
        "select family, coalesce(nullif(prompt_version, ''), '') pv, "
        "count(*) filter (where verdict = 'unclear') u "
        "from adjudications where family is not null and family <> '' "
        "and source = 'human' group by 1, 2 order by 1, 2").fetchall()
    unclear: dict = {}
    for fam, pv, u in rows:
        want = shipping.get(fam) or (f"assay.{__version__}" if str(pv).startswith("assay.") else "")
        if want and pv != want:
            continue
        unclear[fam] = unclear.get(fam, 0) + int(u)

    floor = int(getattr(cfg, "min_adjudications", 20) or 20)
    out = []
    for fam in sorted(set(current) | set(unclear)):
        rate_n = current.get(fam)
        a = round(rate_n[0] * rate_n[1]) if rate_n else 0
        ruled = rate_n[1] if rate_n else 0
        u = unclear.get(fam, 0)
        cur = (cfg.questions.get(fam).action if fam in cfg.questions else None) or ""
        if ruled == 0:
            why = (f"{u} ruling(s), all `unclear`. Nobody could answer this from the state it "
                   f"was given, so there is no agreement rate -- and the edit is to the "
                   f"QUESTION, not to the config." if u else
                   "no rulings at all. Run `assay review -i` before gating this family.")
            out.append(Suggestion(
                section="questions", key=fam, rank=0.5,
                basis="no measured agreement, and why",
                headline=(f"`{fam}` has never been ruled on, so nothing measures whether it "
                          f"is right"
                          if not u else
                          f"`{fam}` was ruled `unclear` {u} time(s) and never answered, so the "
                          f"fix is to the QUESTION and not to the config"),
                measured=[why, f"current action: {cur or 'shipped default'}"],
                draft=f"# no measurement for `{fam}`. Not proposing an action from the shipped\n"
                      f"# default alone -- that would read as measured and is not."))
            continue
        rate = a / ruled
        thin = ruled < floor
        note = (f"{ruled} ruling(s), under the `min_adjudications: {floor}` floor, so this rate "
                f"is not yet something to gate on" if thin else
                f"{ruled} rulings, at or over the floor of {floor}")
        want = "fail" if (rate >= 0.9 and not thin) else ("warn" if rate >= 0.7 else "nothing")
        if want == cur and not thin:
            continue
        out.append(Suggestion(
            section="questions", key=fam, rank=float(ruled) * (1.0 if thin else 2.0),
            basis="measured agreement per family",
            headline=(
                f"`{fam}` has only {ruled} ruling(s), under the floor of {floor}, so its "
                f"{rate:.0%} agreement is not a number to gate on yet"
                if thin else
                f"`{fam}` agrees {rate:.0%} of the time over {ruled} rulings, which supports "
                f"`{want}`" + (f" rather than the `{cur}` it is set to" if cur else "")),
            measured=[note, f"{u} unclear, excluded from the denominator",
                      f"current action: {cur or 'shipped default'}"],
            draft=(f"questions:\n  {fam}:\n    action: {want}\n"
                   f"    # measured {a}/{ruled} = {rate:.0%} agreement"
                   + (f"\n    # THIN: {ruled} < {floor}. Rule on more before trusting this."
                      if thin else ""))))
    return out


# --------------------------------------------------------------------------- explanations

def _explanations(store) -> list[Suggestion]:
    """Reasons that keep coming back on row adjudications: the domain, said out loud repeatedly."""
    rows = store.con.execute(
        "select family, note from adjudications "
        "where note is not null and note <> '' and verdict <> 'unclear' "
        f"and source in ({', '.join('?' * len(_WROTE_A_REASON))}) "
        "order by family, note", list(_WROTE_A_REASON)).fetchall()
    groups: dict = defaultdict(list)
    for fam, note in rows:
        sh = _shape(note)
        if sh:
            groups[(fam, sh)].append(note)
    out = []
    for (fam, sh), notes in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(notes) < 3:
            continue
        out.append(Suggestion(
            section="explanations", key=f"{fam}:{sh[:40]}", rank=float(len(notes)),
            basis="a reason given repeatedly on rulings",
            headline=f"the same explanation was given {len(notes)} times on `{fam}`",
            measured=[f"{len(notes)} occurrences",
                      f"as written: {max(notes, key=len)[:300]}"],
            draft=f"explanations:\n  <mart>:\n    - \"\"   # the option a rower should be offered.\n"
                  f"    # Seen {len(notes)} times under `{fam}`; the wording above is a ruling,\n"
                  f"    # not an option. Say it the way someone triaging a row would choose it."))
    return out


# --------------------------------------------------------------------------- entry point

def resolved_clusters(store, cfg, live: set) -> list:
    """Reasons that clustered across subjects and no longer produce a finding on any of them.

    *** NOT A DECISION, AND NOT NOTHING. ***
    The queue drops these on purpose: a cluster of eight already-fixed subjects outranking two
    live ones is a ranking that rewards resolution, and it filled the top of DECIDE FIRST with
    history. But "this reason was given on 8 models and none of them still fires" is the one
    direct measurement of a question getting BETTER that does not need anybody to re-rule.
    `effectiveness` reports agreement per version, which only moves when a person reads again.
    This moves when the check stops being wrong.
    """
    out = []
    for _sh, items in sorted(_clusters(store, cfg).items()):
        if any((q, sub) in live for sub, q, _n in items):
            continue
        out.append({
            "reason": max((n for _s, _q, n in items), key=len)[:300],
            "subjects": sorted({s for s, _q, _n in items}),
            "questions": sorted({q for _s, q, _n in items if q}),
            "n": len({s for s, _q, _n in items})})
    return sorted(out, key=lambda r: (-r["n"], r["reason"]))


def build(store, cfg, firing: set, run_id: str | None = None, live: set | None = None,
          project=None, entries=None, schema=None, digests=None) -> list:
    """Every suggestion the store supports, ordered so the best-evidenced is first.

    Ordering is (section, -rank, key) with a fixed section order, so two runs over one store
    produce one list in one order. A suggestion whose rank ties another is broken by key, never by
    whichever the database happened to hand back first -- the defect this tool checks other
    people's SQL for.
    """
    out: list = []
    if store is not None:
        # *** A FILTER NOBODY CAN SEE IS THE SAME SHAPE AS A GUARD THAT MATCHES NOTHING. ***
        # Three rules below read only rulings that carry a written reason. How many rows that
        # silently excludes belongs in the output, because "no waiver candidates" and "26
        # candidates, all of them label stubs" are different situations.
        labelled = store.con.execute(
            "select count(*) from adjudications where verdict = 'disagree' "
            f"and source not in ({', '.join('?' * len(_WROTE_A_REASON))})",
            list(_WROTE_A_REASON)).fetchone()
        skipped = int(labelled[0]) if labelled else 0
        if skipped:
            out.append(Suggestion(
                section="open", key="_label_stubs", rank=-1.0,
                basis="rulings excluded for carrying no written reason",
                headline=f"{skipped} disagreement(s) are project LABELS, not written reasons",
                measured=[(f"{skipped} rows with source `label`: the project's own "
                            f"declarations read back as verdicts"),
                          ("their note is a generated stub, so it cannot justify a waiver and "
                           "cannot identify a repeated defect")],
                decide=("Nothing to do unless you expected these to produce waivers.\n"
                        "  They are excluded on purpose: a waiver whose reason is a stub is a "
                        "permanent silence nobody argued for.\n"
                        "  To turn one into a waiver, rule on it yourself -- `assay review -i` "
                        "-- and write why."),
                draft=""))
        out += _vocab_from_joins(store, cfg, run_id, project)
        out += _vocab_from_contradicted_names(store)
        out += _repeated_reasons(store, cfg, live)
        out += _waivers_from_acceptances(store, cfg)
        out += _questions_from_agreement(store, cfg)
        out += _explanations(store)
    out += _questions_unconfigured(cfg, firing)
    if project is not None:
        out += _vocab_from_descriptions(cfg, project)
        if entries is not None:
            out += _descriptions(project, entries, schema, digests)
    order = {"open": 0, "vocab": 1, "questions": 2, "waivers": 3, "explanations": 4,
             "descriptions": 5}
    # *** RANK WITHIN A RULE, NEVER ACROSS RULES. ***
    # `section_id` scores 65 hops x 24 models = 1560 and `incident_id` scores 99.68% unique. Both
    # are vocab candidates and the numbers measure different things in different units, so sorting
    # them together silently declares one rule more important than another by an accident of
    # scale -- and buries every near-unique key under the join counts forever. Each rule's list is
    # ordered internally; the rules themselves are ordered by name, which is arbitrary and looks
    # arbitrary, rather than by a number that looks meaningful and is not.
    return sorted(out, key=lambda s: (order.get(s.section, 9), s.basis, -s.rank, s.key))
