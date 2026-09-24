"""Is this question one Jev will answer well, or one it will answer confidently and uselessly?

*** EVERY RULE HERE WAS MEASURED, MOSTLY BY GETTING IT WRONG FIRST. ***

A badly shaped question does not fail. It returns a confident answer to something adjacent, which
is far worse than an error, because nothing tells you. `keys_on_a_non_unique_column` read 0.73 to
0.85 on every model tested, clean or broken, and looked like a working check for weeks.

These are static checks. They cannot tell you a question is GOOD -- only running it against known
cases does that, which is what `--fixtures` is for. They can tell you it has a shape that has
already been measured to fail.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

# TypeSafe publish these as Jev's limitations, and each one cost us a measurement to rediscover.
# *** ANCHORED, BECAUSE THE FIRST VERSION MATCHED "SUMMARY". ***
# Run against assay's own fifteen hand-tuned banks, the unanchored pattern flagged four questions
# and every one was the LINTER being wrong. A linter calibrated on nothing is a nag.
_ARITHMETIC = re.compile(
    r"\b(calculate|calculates|compute|computes|add up|adds up|subtract|subtracts|multiply|"
    r"multiplies|divide|divides|sum of|total of|percentage of|number of days|"
    r"difference between|how many \w+ (?:are|were|is|was))\b", re.IGNORECASE)
_TEMPORAL = re.compile(
    r"\b(before|after|earlier|later|older|newer|date|month|year|day|week|duration|elapsed|"
    r"timestamp)\b", re.IGNORECASE)
_MULTI_HOP = re.compile(
    r"\b(and then|after determining|once you have|first .{0,24}then|based on (?:your|the) "
    r"(?:answer|previous)|having established)\b", re.IGNORECASE)
# *** ASKING WHETHER A NUMBER IS THE RIGHT SIZE IS ARITHMETIC IN DISGUISE. ***
# TypeSafe: Jev "cannot reliably judge whether two values are near each other". Measured on
# assay's OWN units family, which asked whether magnitudes were plausible for a unit: it called
# 218,235 "acres" consistent at 0.82 and 4,073,925 "acre-feet" consistent at 0.54 -- wrong by
# 43,560x and 325,851x. The instructions contained no arithmetic WORD, so the calculator rule
# missed it entirely. Magnitude is its own trap.
#
# "how many" is NOT here. `severity_fit` asks how serious a violation is GIVEN a blast radius that
# code already counted, and it was verified working -- 1.59 on a primary key twenty-four
# dashboards read, 0.14 on a note column nobody reads. Code counts, the model judges consequence:
# that is the correct pattern and the rule must not punish it.
_MAGNITUDE = re.compile(
    r"\b(magnitude|magnitudes|order of magnitude|plausible for|too (?:large|small|big|high|low)|"
    r"how (?:large|big|small)|within range|in the right range|near each other|"
    r"close to|bigger than|smaller than|greater than|less than|reasonable size|"
    r"look like that unit)\b", re.IGNORECASE)
_HEDGE = re.compile(r"\b(medium|average|moderate|somewhat|fairly|reasonable|appropriate|good|"
                    r"bad|nice|proper)\b", re.IGNORECASE)
# Any option that lets the model decline. Measured cost of omitting one: the model must pick a
# wrong answer, and a real division bug surfaced ONLY because it could say "not on the list".
_NO_MATCH = re.compile(r"(cannot|cant|unknown|none|other|no_match|unclear|not_applicable|"
                       r"says_nothing|neither|insufficient|^not_|_not_|nothing|^no_|undetermined|"
                       r"ambiguous|unsure)", re.IGNORECASE)


_STOP = frozenset(
    ["a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "does", "do", "for", "from", "has", "have", "in", "is", "it", "its", "not", "of", "on", "or", "that", "the", "their", "them", "then", "there", "these", "this", "to", "was", "were", "what", "when", "which", "with", "within", "values", "value", "kind", "thing", "things", "something"])


@dataclass
class Issue:
    question: str
    level: str          # error | warn
    rule: str
    detail: str


def _text(v) -> str:
    """Instructions and criteria are strings, dicts or lists. Flatten for scanning."""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return " ".join(_text(x) for x in v.values())
    if isinstance(v, (list, tuple)):
        return " ".join(_text(x) for x in v)
    return ""


def _acknowledged(q: dict) -> dict:
    """*** A LINT WITH NO WAY TO SAY "I KNOW, AND HERE IS WHY" GETS MUTED WHOLESALE. ***

    Reported from the field: a question earned `multi_hop` for reading a partition before an order
    by, the rule was RIGHT that it costs accuracy, and the author kept it because one hop could not
    distinguish the shapes. That is a trade the lint cannot express, so it has to be expressible in
    the question.

    A reason is required, exactly as it is for a waiver. "Acknowledged" with no reason is how a
    finding goes to die.
    """
    ack = q.get("acknowledge") or {}
    return {k: str(v) for k, v in ack.items() if isinstance(ack, dict) and str(v).strip()}


def _walk_strings(node) -> list:
    """Every string anywhere in a question definition: instructions, criteria, options, nested."""
    out = []
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for k, v in node.items():
            out.append(str(k))
            out += _walk_strings(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            out += _walk_strings(v)
    return out


def _names_field(text: str, field: str) -> bool:
    """Whether a question's prose refers to a state field AS A FIELD.

    *** IN BACKTICKS, BECAUSE THAT IS HOW SOMEBODY MEANS THE KEY RATHER THAN THE WORD. ***
    The first version matched the bare word and flagged three shipped questions: "volume
    contradicts a claim about a column" is English about columns, not a reference to the
    `column` key. A rule that fires on almost every question is a rule somebody switches off,
    and then the one that mattered goes with it.

    The real failure from the field wrote it the other way -- "Read `reads` and `filters`" --
    because the author meant the field, and that is the convention every question here follows.
    """
    return f"`{field}`" in text


def lint_question(name: str, q: dict, shipped: dict | None = None) -> list[Issue]:
    """Every rule this codebase learned the hard way, applied to one question.

    `shipped` is the bank to check prefix collisions against, keyed by question name exactly as
    `load_all_banks` returns it. It took a dict of prefixes at first, which is a second shape for
    one fact and duly got passed the wrong way round the first time it was used.
    """
    out: list[Issue] = []
    shipped_prefixes = {v["id_prefix"]: k for k, v in (shipped or {}).items()
                        if isinstance(v, dict) and v.get("id_prefix")}
    ack = _acknowledged(q)
    raw_ack = q.get("acknowledge") or {}
    for k, v in (raw_ack.items() if isinstance(raw_ack, dict) else []):
        if not str(v).strip():
            out.append(Issue(name, "error", "acknowledge",
                             f"`acknowledge: {k}` has no reason. A rule silenced without one is "
                             f"how a finding goes to die."))
    add = lambda lvl, rule, detail: out.append(Issue(name, lvl, rule, detail))

    kind = q.get("type")
    if kind not in ("choice", "noul", "score"):
        add("error", "type", f"type must be choice, noul or score; got {kind!r}")
        return out
    if not q.get("prompt_version"):
        add("error", "prompt_version",
            "no prompt_version. The cache is keyed on it, so a reworded criterion would serve "
            "the old answer forever.")
    if not q.get("id_prefix"):
        add("error", "id_prefix",
            "no id_prefix. Verdicts file under the family a question id names, so without one "
            "every verdict on this question counts toward nothing.")
    elif shipped_prefixes and q["id_prefix"] in shipped_prefixes and \
            shipped_prefixes[q["id_prefix"]] != name:
        add("error", "id_prefix",
            f"prefix {q['id_prefix']!r} is already used by {shipped_prefixes[q['id_prefix']]!r}. "
            f"Two families sharing a prefix means one silently absorbs the other's verdicts.")

    instr = _text(q.get("instructions"))
    crit = q.get("criteria") or {}
    both = f"{instr} {_text(crit)}"

    if not instr.strip():
        add("error", "instructions", "no instructions. Question ids are never sent to the model.")
    if len(instr) > 900:
        add("warn", "state_size",
            f"instructions are {len(instr)} characters. Unrelated detail acts as a distractor: "
            f"measured, one correct extra sentence took a claim from 0.96 to 0.47.")

    if _ARITHMETIC.search(instr):
        add("error", "not_a_calculator",
            "this asks the model to CALCULATE. TypeSafe publish it plainly -- Jev is not a "
            "calculator. Measured here: asked whether a date expression implemented 'the last day "
            "of the second month following', it scored the CORRECT one 0.39 and a WRONG one 0.62. "
            "Settle arithmetic in SQL and ask about the meaning.")
    if _MAGNITUDE.search(both):
        add("error", "numeric_magnitude",
            "this asks whether a NUMBER is the right size, which Jev cannot do -- TypeSafe say it "
            "cannot reliably judge whether two values are near each other. Measured on assay's "
            "own units family: 218,235 passed as plausible 'acres' at 0.82, and 4,073,925 as "
            "'acre-feet' at 0.54, wrong by 43,560x and 325,851x. Compute the range in SQL and ask "
            "the model only what KIND of thing the values are.")
    if len(_TEMPORAL.findall(instr)) >= 2:
        add("warn", "dates_are_text",
            "this leans on temporal ordering. Jev reads dates as TEXT, not as ordered quantities, "
            "so comparisons and durations are unreliable. Compare dates in SQL.")
    if _MULTI_HOP.search(instr):
        add("warn", "multi_hop",
            "this asks for more than one hop of reasoning, which costs accuracy. Split it: "
            "measured, one lumped question over three rules read 0.64 where the split rule that "
            "applied read 0.85.")

    if kind == "choice":
        if len(crit) < 2:
            add("error", "options", "a choice needs at least two options.")
        elif not any(_NO_MATCH.search(k) for k in crit):
            add("error", "no_match_option",
                "no option for 'none of these'. The model cannot say the answer is off the list, "
                "so it must pick a wrong one. A real division bug surfaced here ONLY because a "
                "no-match option existed.")
        _check_options_separate(name, crit, out)
        _check_cross_references(name, crit, out)

    if kind == "score":
        levels = crit if isinstance(crit, list) else list(crit.values())
        if not 2 <= len(levels) <= 10:
            add("error", "levels", f"a score needs 2 to 10 levels; got {len(levels)}.")
        for lv in levels:
            t = _text(lv)
            if _HEDGE.search(t) and len(t) < 60:
                add("warn", "level_names_nothing",
                    "a level describes no concrete situation. The answer may land BETWEEN two "
                    "levels, so each has to stand on its own; 'medium' describes nothing.")
                break

    if kind == "noul":
        if set(map(str, crit)) - {"true", "false"}:
            add("warn", "noul_criteria", "a noul takes only `true` and `false` criteria.")
        # Only the QUESTION, never the note. The note is prose and prose uses "or": scanning it
        # flagged assay's own description family, whose note simply lists what is NOT a
        # contradiction. A heuristic that fires on explanation is a heuristic that gets muted.
        asked = (q.get("instructions") or {})
        asked = asked.get("question", "") if isinstance(asked, dict) else str(asked)
        if asked.lower().count(" or ") >= 2:
            add("warn", "one_rule_per_noul",
                "this looks like several rules in one noul. Use one per rule: measured, a lumped "
                "noul read 0.64 on a model where the split rule read 0.85, and it produced a "
                "false positive on clean code that the split version did not.")
        if "confidence" in both.lower():
            add("warn", "noul_has_no_confidence",
                "a noul returns no confidence field at all. The value IS the probability.")

    # --- a family that declares a subject is run by the generic runner ---
    subj = q.get("subject")
    if subj is not None:
        from .subjects import KINDS, STATE_FIELDS
        if subj not in KINDS:
            add("error", "subject", f"unknown subject {subj!r}. Use one of {sorted(KINDS)}.")
        else:
            # *** A QUESTION CAN ONLY ASK WHAT ITS SUBJECT'S STATE CAN ANSWER. ***
            # Reported from the field and it cost an hour: a question asking about `filters`
            # declared `subject: predicate`, lint-passed, and never fired -- only a `model`
            # carries filters. The failure is silent in the worst way, because the question IS
            # asked, answered, paid for and stored, and the answer is about a field that was
            # never in the state it was sent.
            mine = STATE_FIELDS.get(subj, set())
            elsewhere = {f: k for k, fields in STATE_FIELDS.items() for f in fields
                         if k != subj and f not in mine}
            text = " ".join(str(v) for v in _walk_strings(q))
            wanted = sorted({f for f in elsewhere if _names_field(text, f)})
            for f in wanted[:4]:
                add("error", "state_field_the_subject_does_not_carry",
                    f"this asks about `{f}`, which a `{subj}` state does not carry -- "
                    f"`{elsewhere[f]}` does. The question would be asked, answered, paid for and "
                    f"stored, about a field that was never sent. A `{subj}` carries: "
                    f"{sorted(mine)}.")
        ss = q.get("subject_state")
        if ss is not None and ss not in ("full", "minimal"):
            add("error", "subject_state",
                f"unknown subject_state {ss!r}. Use 'full' (the default) or 'minimal', which "
                f"omits `what_one_row_of_this_model_is` for a family whose criteria already "
                f"reason about a narrower thing.")
        fw = q.get("finding_when")
        if fw is None:
            # *** THIS IS THE DEAD QUESTION PROBLEM WEARING A NEW HAT. ***
            # Before the runner, a family with a new name was asked by nothing. Now it is asked,
            # answered, paid for and stored -- and still produces nothing. Reported from the
            # field, where the old wording ("that is valid") undersold it. It IS valid, and it is
            # almost never what someone writing their first question meant.
            add("error", "finding_when",
                "no `finding_when`, so this is asked, paid for, stored -- and produces no finding "
                "and gates nothing. That is a real mode (the answers still fill the inventory and "
                "`trace`), but it is rarely what a new question means. Name the answers that are "
                "findings, or acknowledge this explicitly: `acknowledge: {finding_when: \"...\"}`.")
        elif kind == "choice":
            unknown = [a for a in (fw if isinstance(fw, list) else [fw]) if a not in crit]
            if unknown:
                add("error", "finding_when",
                    f"{unknown} are not options of this question, so the finding can never fire. "
                    f"Options are {sorted(crit)}.")
    elif q.get("finding_when") is not None and name not in CALLERS:
        # `judged.declared_findings` reads `finding_when` for EVERY family whose answers are filed
        # under a model, so a family a shipped command asks is fine without a `subject`. One that
        # nothing asks has no answers for the declaration to read.
        add("warn", "finding_when",
            "`finding_when` without a `subject` does nothing unless a command asks this family: "
            "only the generic runner asks a family by its subject.")

    if not crit and kind != "noul":
        add("error", "criteria", "no criteria. The options are the question.")
    # An acknowledged rule is silenced HERE, at the end, so the checks above stay simple and an
    # acknowledgment of a rule that never fired is itself visible as dead config.
    return [i for i in out if i.rule not in ack or i.rule == "acknowledge"]


def _check_cross_references(name: str, crit: dict, out: list[Issue]) -> None:
    """*** AN OPTION THAT NAMES ANOTHER OPTION IS ROUTING, AND THE MODEL MAY NOT HONOR IT. ***

    Reported from the field with numbers. A question whose `something_else` said "if they are
    ranked by a non-priority column the answer is something_else, not this" scored `no_overlap`
    0.63 against an overlap mass of 0.35 -- the judged check read the routing as a disjointness
    guarantee and passed it. The answering model did NOT honor it: the same subject came back
    under both options at 0.55 and 0.63.

    A text check believes prose. This is the one shape where believing it is known to be wrong,
    so it is caught statically instead, where no model is asked to be consistent about anything.
    """
    keys = list(crit)
    for k, v in crit.items():
        body = _text(v).lower()
        for other in keys:
            if other == k:
                continue
            # *** SUBSTRING MATCHING FLAGGED "square feet" FOR NAMING "feet". ***
            # And `other` matched inside "some other entity". An option name has to be matched as
            # a whole token, in both its snake_case and spaced spellings, and never when it is
            # merely part of a longer option name that is legitimately being described.
            o = other.lower()
            if any(o != x.lower() and o in x.lower() for x in keys):
                continue                      # `feet` inside `square_feet`: ambiguous, skip it
            pat = re.compile(r"\b" + re.escape(o).replace(r"\_", "[ _]") + r"\b")
            if pat.search(body):
                out.append(Issue(
                    name, "warn", "option_routes_to_another",
                    f"option {k!r} names {other!r} in its own description. That tells the model "
                    f"where to send a case instead of describing this option, and it may not "
                    f"honor it -- measured: a question doing exactly this passed the overlap "
                    f"check at 0.63 while the answering model put one subject under both options. "
                    f"Describe what {k!r} IS; let the other option describe itself."))
                return


def _check_options_separate(name: str, crit: dict, out: list[Issue]) -> None:
    """*** DESCRIPTIONS MUST SEPARATE THE OPTIONS FROM EACH OTHER. ***

    Measured on assay's own question: `documentation_states_it` and `documentation_implies_it`
    both lead to the same action and are not separable, and the answers sat at 0.36-0.39 until
    they were merged. A description that explains an option without distinguishing it from its
    neighbor gives the model nothing to cut on.
    """
    described = {k: _text(v).lower() for k, v in crit.items()}
    for k, t in described.items():
        if not t.strip():
            out.append(Issue(name, "error", "option_undescribed",
                             f"option {k!r} has no description. Option NAMES are not enough."))
        elif len(t) < 24:
            out.append(Issue(name, "warn", "option_thin",
                             f"option {k!r} is described in under 24 characters."))
    keys = list(described)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            # *** JACCARD OVER THE UNION, NOT OVER THE SHORTER ONE. ***
            # Dividing by min() punishes a terse option beside a verbose one: two genuinely
            # distinct feed options scored 0.8+ purely because one was short. And stopwords are
            # dropped, because "the values are a kind of thing" is shared by every option that
            # talks about values at all.
            ta = set(described[a].split()) - _STOP
            tb = set(described[b].split()) - _STOP
            if len(ta) < 4 or len(tb) < 4:
                continue
            overlap = len(ta & tb) / len(ta | tb)
            if overlap > 0.75:
                out.append(Issue(name, "warn", "options_not_separated",
                                 f"{a!r} and {b!r} are described almost identically. The model "
                                 f"has nothing to cut on between them."))


def lint_all(banks: dict, shipped: dict | None = None) -> list[Issue]:
    out: list[Issue] = []
    for name, q in sorted(banks.items()):
        out += lint_question(name, q, shipped)
    return out


def acknowledged_issues(banks: dict, shipped: dict | None = None) -> list[Issue]:
    """What each question silenced, and why. Shown rather than hidden: an acknowledgment is a
    decision someone made, and the next reader deserves to see it."""
    out: list[Issue] = []
    for name, q in sorted(banks.items()):
        for rule, why in _acknowledged(q).items():
            out.append(Issue(name, "acknowledged", rule, why))
    return out


# *** A FAMILY NOTHING ASKS IS NOT COVERAGE, AND IT LOOKS EXACTLY LIKE COVERAGE. ***
# Reported from the field: three custom families were written, linted at zero errors, listed by
# `assay banks` as `yours`, and were never asked by anything. Every call site names a SHIPPED
# family by string literal; there is no generic runner. So a family with a NEW name is loaded,
# validated, displayed, and inert.
#
# Worse, the documented example used a new name, so anyone following the docs wrote a question
# that cannot run. This table is what `assay banks` prints so that is visible at a glance, and a
# test asserts it against the source so it cannot drift into a lie.
#
# `state` is the subject the call site hands the question. A replacement can only ask about what
# its caller already builds: replacing `edge_preserves_the_grain` gets parent, child, join keys
# and grouping, so it can judge a join and cannot judge a window function.
CALLERS: dict[str, tuple[str, str, str]] = {
    # family: (module that asks it, command, the state it receives)
    "column_role":                      ("columns",    "assay columns",   "a chunk of columns"),
    "null_meaning":                     ("columns",    "assay columns",   "a chunk of columns"),
    "column_is_part_of_the_key":        ("contracts",  "assay infer",     "a model's candidate key"),
    "predicate_intent":                 ("semantics",  "assay semantics", "a chunk of predicates"),
    "description_contradicts_the_code": ("semantics",  "assay semantics", "a model's prose + code"),
    "sentence_is_a_claim":              ("claims",     "assay claims",    "a chunk of sentences"),
    "claim_alignment":                  ("claims",     "assay verify",    "one claim + its evidence"),
    "edge_preserves_the_grain":         ("cli",        "assay traverse",  "one parent->child edge"),
    "same_concept":                     ("align",      "assay align",     "a chunk of column pairs"),
    "severity_fit":                     ("testing",    "assay tests",     "a test + its blast radius"),
    "practice_exception":               ("practices",  "assay practices", "one evaluator violation"),
    "field_matches_its_name":           ("feeds",      "assay feeds",     "a column + a sample"),
    "units_are_what_the_column_claims": ("feeds",      "assay feeds",     "a column name"),
    "row_explanation":                  ("rows",       "assay adjudicate", "one failing row"),
    "row_is_internally_coherent":       ("rows",       "assay adjudicate", "one failing row"),
    # assay's linter, asking about assay's own questions.
    "options_overlap":                  ("lint",       "assay banks --judge",
                                         "one question's own option set"),
    # assay's own disagreements, asking whether two of them are one bug.
    "same_defect":                      ("cli",        "assay disagreements --judge",
                                         "two rejected findings' reasons"),
    # Counted tier: Elementary counted the movement, assay brought the claim and the blast radius.
    "volume_contradicts_a_claim":       ("cli",        "assay volume --judge",
                                         "one counted movement + one claim"),
    # The monitoring bank: judged from what Elementary counted, filed under the model.
    "movement_is_expected_for_this_kind_of_table": ("monitoring_bank", "assay volume --judge",
                                                    "one moved table + what kind it is"),
    "monitor_covers_what_matters":      ("monitoring_bank", "assay volume --judge",
                                         "one unwatched model + its reach"),
    "stale_monitor_still_matters":      ("monitoring_bank", "assay volume --judge",
                                         "one stale failed monitor + its model"),
    "test_never_ran_is_a_gap_or_a_leftover": ("monitoring_bank", "assay volume --judge",
                                              "one never-run test + its model"),
    # A reading of a review card, written to the file `review --reads` takes. Never a verdict.
    "finding_is_correct":               ("reads",      "assay read",
                                         "one (model, check) card: findings, evidence, SQL"),
}


def caller_of(name: str, bank: dict | None = None) -> tuple[str, str, str] | None:
    """(module, command, state) for a family, or None when nothing asks it.

    A family that declares a `subject:` is asked by the GENERIC RUNNER, whatever its name. That is
    the whole point of the runner, and reporting it as uncalled would be the same lie in reverse.
    """
    if bank is None and name not in CALLERS:
        from .contracts import QUESTIONS
        bank = QUESTIONS.get(name)
    if bank and bank.get("subject"):
        return ("subjects", "assay ask", f"one {bank['subject']}")
    return CALLERS.get(name)


def bank_state(bank: dict) -> dict:
    """What a question's own options look like to a judge.

    Named and importable because `assay stale --exact` has to build it again from the bank, and a
    dict assembled inside a loop is a state only that loop can produce.
    """
    crit = bank.get("criteria") or {}
    return {"the_question": (bank.get("instructions") or {}).get("question", ""),
            "options": {k: _text(v) for k, v in crit.items()}}


def judge_overlap(banks: dict, client, store=None) -> list[Issue]:
    """Ask whether any two options of a question could both be right about the same subject.

    *** THE STATIC RULE CANNOT SEE AN OVERLAP OF MEANING, AND THAT IS THE COMMON KIND. ***
    Measured on a real pair: `not_a_seniority_order` and `something_else` both correctly describe
    a window ranking wildfire percentiles. Word overlap scored them 0.25 against a 0.75 threshold
    and passed them, because they share a SITUATION and not a vocabulary.

    One call per choice question, so a fifteen-bank project costs about a cent. Opt-in, because
    `assay banks` must stay free and runnable in CI with no key.
    """
    from .contracts import QUESTIONS
    from .jev import choice

    spec = QUESTIONS.get("options_overlap")
    if spec is None:
        return []
    q = {"overlap": choice(spec["instructions"], spec["criteria"])}
    out: list[Issue] = []
    for name, bank in sorted(banks.items()):
        if bank.get("type") != "choice" or name == "options_overlap":
            continue
        crit = bank.get("criteria") or {}
        if len(crit) < 3:
            continue                      # two options cannot overlap without being identical
        state = bank_state(bank)
        # *** A PROBABILITY AGAINST A THRESHOLD FLAPS, AND THIS IS MEANT FOR CI. ***
        # Reported from the field: seven warnings on one run, six on the next, over an UNCHANGED
        # set of banks. A family sitting near the line will flip forever and no one will trust the
        # check. `decide` caches on a hash of the state, so an unchanged question keeps its answer
        # and only a REWORDED one is asked again -- which is exactly when it should be.
        try:
            if store is not None:
                from . import states
                from .jev import decide
                rec = states.make("bank", states.Ctx(store=store), key=f"bank::{name}",
                                  inputs={"family": name})
                got = decide(store, client, rec, q,
                             prompt_version=spec["prompt_version"], caller="assay.banks")
                a = got.get("overlap") or {}
                ans = {"choice": a.get("answer"), "confidence": a.get("confidence"),
                       "probabilities": a.get("probabilities") or {}}
            else:
                ans = client.ask(state, q, caller="assay.banks")["answers"]["overlap"]
        except Exception as e:                                      # noqa: BLE001
            out.append(Issue(name, "warn", "options_overlap", f"could not be judged: {e}"))
            continue
        # *** SUM THE DISTRIBUTION, DO NOT GATE ON CONFIDENCE. ***
        # `two_overlap` and `several_overlap` are two ways of saying YES, so a clear answer splits
        # its mass across them and CONFIDENCE FALLS. Measured on the pair that prompted this
        # check: 0.58 + 0.28 = 0.86 that an overlap exists, at confidence 0.47. Gating on
        # confidence missed it -- the exact mistake TypeSafe warn about and this repo documents:
        # confidence is a statistic about the shape of the distribution, never evidence strength.
        probs = ans.get("probabilities") or {}
        overlap = float(probs.get("two_overlap", 0)) + float(probs.get("several_overlap", 0))
        through = float(probs.get("a_case_falls_through", 0))
        # *** REPORT THE MARGIN, NOT JUST THE SIDE OF THE LINE. ***
        # Reported from the field: four shipped families sit within 0.11 of the threshold, two
        # passing and two warning. Caching stops them flapping; a one-word rewording still crosses.
        # A reader who can see 0.61 knows to treat it differently from 0.89, and a reader shown
        # only "warn" cannot.
        margin = abs(overlap - 0.6)
        if overlap >= 0.6:
            worst = "several" if probs.get("several_overlap", 0) >= probs.get("two_overlap", 0) \
                else "one pair"
            near = "  [NEAR THE LINE]" if margin <= 0.12 else ""
            out.append(Issue(
                name, "warn", "options_overlap",
                f"two or more options could both be correct about the same subject (p={overlap:.2f} "
                f"against a 0.60 threshold, {worst}){near}. Which one comes back is then arbitrary "
                f"for those cases. The static rule cannot see this: it compares words, and an "
                f"overlap of MEANING shares a situation rather than a vocabulary."))
        elif margin <= 0.12:
            out.append(Issue(
                name, "info", "options_overlap_near_the_line",
                f"passes at p={overlap:.2f} against a 0.60 threshold, which is within 0.12 of it. "
                f"A one-word rewording can cross. Treat a pass here as unsettled rather than as "
                f"a clean bill."))
        if through >= 0.6:
            out.append(Issue(
                name, "warn", "options_fall_through",
                f"a realistic subject fits NO option and there is no way to decline (p={through:.2f})."))
    return out


# *** A FORK MADE TO PRESERVE ONE PROPERTY SILENTLY FORFEITS ANOTHER. ***
# Reported from the field, and it is a cost of the pattern rather than a bug. A project forked
# `edge_preserves_the_grain` and copied four options WORD FOR WORD on purpose, so the hand
# verification recorded against that family would still apply. That same copying is what stops
# every later improvement reaching it, and `assay banks` said only `yours`, replacing, and nothing
# more.
#
# assay holds both halves, so it can say it once. Not an error -- a fork is usually deliberate --
# but a copy nobody knows is stale reads as current, which is the same argument as the stale-skill
# check and the same argument as this whole tool.
def _blocks(q: dict) -> dict:
    """The sent text, one addressable piece at a time. Comments are not in here and never were."""
    out: dict = {}
    ins = q.get("instructions") or {}
    if isinstance(ins, dict):
        for k, v in ins.items():
            out[f"instructions.{k}"] = json.dumps(v, sort_keys=True, default=str)
    else:
        out["instructions"] = json.dumps(ins, default=str)
    crit = q.get("criteria") or {}
    if isinstance(crit, dict):
        for name, v in crit.items():
            if isinstance(v, dict):
                for k, vv in v.items():
                    out[f"criteria.{name}.{k}"] = json.dumps(vv, sort_keys=True, default=str)
            else:
                out[f"criteria.{name}"] = json.dumps(v, sort_keys=True, default=str)
    elif isinstance(crit, list):
        for i, v in enumerate(crit):
            out[f"criteria[{i}]"] = json.dumps(v, sort_keys=True, default=str)
    return out


def override_drift(banks: dict, shipped: dict) -> list[Issue]:
    """What a project's own bank copied verbatim, and what it has not taken since.

    `forked_from: role.v2` in the override is the declared half: say which shipped version it was
    taken from and assay can tell you the shipped one has moved. Without it, the identical-block
    count still says which parts are copies rather than changes.
    """
    out: list[Issue] = []
    for name, q in sorted(banks.items()):
        base = shipped.get(name)
        if base is None or q.get("_source") == base.get("_source"):
            continue
        mine, theirs = _blocks(q), _blocks(base)
        shared = [k for k in mine if k in theirs]
        same = [k for k in shared if mine[k] == theirs[k]]
        if q.get("prompt_version") and q["prompt_version"] == base.get("prompt_version") \
                and mine != theirs:
            out.append(Issue(
                name, "error", "override_reuses_a_version",
                f"this replaces `{name}` and keeps its prompt_version "
                f"{base['prompt_version']!r} while the text differs. Two different questions "
                f"under one version: `assay effectiveness` cannot tell their verdicts apart, so "
                f"the agreement rate mixes answers to two questions. Give yours its own."))
        forked = q.get("forked_from")
        if forked and forked != base.get("prompt_version"):
            out.append(Issue(
                name, "warning", "override_is_behind",
                f"forked from {forked!r} and the shipped question is now at "
                f"{base.get('prompt_version')!r}. Whatever changed in between has not reached "
                f"this copy."))
        # *** IT ASKED FOR A `forked_from` THAT WAS ALREADY THERE. *** (25.14) Declared and current,
        # there is nothing left to ask: when the shipped question moves, `override_is_behind` says so.
        if same and not forked:
            out.append(Issue(
                name, "note", "override_copies_the_shipped_text",
                f"{len(same)} of {len(shared)} blocks are byte-identical to the shipped "
                f"`{name}` ({', '.join(sorted(same)[:4])}"
                f"{', ...' if len(same) > 4 else ''}). Those are copies rather than changes, and "
                f"a later improvement to them will not reach this fork. Add "
                f"`forked_from: {base.get('prompt_version')}` and assay will tell you when the "
                f"shipped one moves."))
    return out


# ------------------------------------------------------------------- the vocabulary
# *** `assay banks` LINTS YOUR QUESTIONS HARD AND NOTHING EVER LINTED YOUR WORDS. ***
# `config.py` read `data.get("vocab") or {}` and that was the whole check, while the same file's
# own comment said a vocab entry is "injected into state for EVERY question, which is why it
# improves answers to questions you never wrote" -- and, by the same mechanism, why a term that is
# false here steers every answer wrong at once.
#
# Measured on a real warehouse: 16 terms, six of them asserting Colorado water law, sent to all 358
# models. 4,997 of 19,707 judged answers -- 25% -- were about Arizona models that were told,
# among other things, that prior appropriation decides who gets water and that all seven Colorado
# water divisions are present.

# A jurisdiction is a claim about WHERE a word is true, and a statute is the strongest form of it.
_STATUTE = re.compile(r"\bC\.?R\.?S\.?\b|\bA\.?R\.?S\.?\b|\bU\.?S\.?C\.?\b|§|\bstat(?:ute)?\.?\s*\d",
                      re.IGNORECASE)
_STATES = ("alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
           "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa",
           "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan",
           "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire",
           "new jersey", "new mexico", "new york", "north carolina", "north dakota", "ohio",
           "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota",
           "tennessee", "texas", "utah", "vermont", "virginia", "washington", "west virginia",
           "wisconsin", "wyoming")


# *** A PLACE IN AN EXAMPLE IS AN ILLUSTRATION, NOT A JURISDICTION. ***
# Reported from the field (25.4): `geography` -- "the market a row belongs to, e.g. a Colorado or
# Arizona metro" -- was flagged for naming a state, while `case_number` -- "a water court case ...
# assigned per division", a court's convention asserted to every model -- passed. The first is a
# term about the project's data, true everywhere; its states are examples. The second names an
# institution whose rules hold in one place, and names no state at all.
_EXAMPLE = re.compile(r"\b(e\.g\.|eg\.|for example|such as|for instance|like)\s[^.;:)]*",
                      re.IGNORECASE)
_INSTITUTION = re.compile(r"\b(court|decree[sd]?|statut\w*|ordinance|regulat\w*|adjudicat\w*|"
                          r"jurisdiction\w*|tribunal|docket|water rights?|appropriation)\b",
                          re.IGNORECASE)


def _term_text(body) -> str:
    return _text(body) if not isinstance(body, str) else body


def lint_vocab(vocab: dict, project=None) -> list:
    """Every way a vocabulary can be wrong in a way that costs something.

    `project` is optional and two rules need it: a scope that matches nothing, and a term no model
    in this warehouse ever mentions. Without it those are simply not reported -- never reported as
    passing, which is the rule everything else here follows.
    """
    out: list[Issue] = []
    for term, body in sorted((vocab or {}).items()):
        txt = _term_text(body)
        sel = body.get("applies_to") if isinstance(body, dict) else None

        if isinstance(body, dict) and not (body.get("means") or "").strip():
            # The one thing a term is FOR. A key with no `means` is a word with no definition
            # taking up room in every state that gets sent.
            out.append(Issue(f"vocab.{term}", "error", "term_undefined",
                             "declares no `means`, so it defines nothing and is sent to every "
                             "call anyway."))

        # *** A TERM THAT ASSERTS LAW MUST SAY WHERE THE LAW RUNS. ***
        if not sel:
            statute = _STATUTE.search(txt)
            outside = _EXAMPLE.sub(" ", txt).lower()
            named = [n for n in _STATES if n in outside]
            body_ = _INSTITUTION.search(txt)
            if statute or named or body_:
                what = (f"cites {statute.group(0)!r}" if statute
                        else f"names {named[0].title()}" if named
                        else f"names a {body_.group(0).lower()}")
                out.append(Issue(
                    f"vocab.{term}", "warn", "asserts_law_everywhere",
                    f"{what} and declares no `applies_to`, so it is asserted to every model in "
                    f"the project as universal fact. A term asserted outside where it is true "
                    f"steers every answer wrong at once. Scope it with a selector: "
                    f"`applies_to: \"path:models/...\"`."))

        if sel and project is not None:
            from .selector import SelectorError, resolve
            try:
                if isinstance(sel, dict):
                    keep = resolve(project, str(sel.get("select") or "")) or set()
                    gone = resolve(project, str(sel["exclude"])) if sel.get("exclude") else set()
                    scope = keep - (gone or set())
                else:
                    scope = resolve(project, str(sel))
            except SelectorError as e:
                out.append(Issue(f"vocab.{term}", "error", "scope_unreadable", str(e)))
                continue
            if scope is not None and not scope:
                # *** A SCANNER MATCHING NOTHING PASSES WRONGLY. ***
                # A term scoped to a path that does not exist reaches no state at all, and the
                # config reads as though the word were defined.
                out.append(Issue(
                    f"vocab.{term}", "error", "scope_matches_nothing",
                    f"`applies_to: {sel!r}` matches no model in this project, so this term is "
                    f"never sent anywhere. It reads as defined and defines nothing."))

    if project is not None:
        out += _terms_by_reach(vocab, project, out)
    return out


def _common_dir(paths: list) -> str:
    """The deepest directory every one of these files sits under, or '' when they scatter."""
    if not paths:
        return ""
    parts = [str(p).split("/")[:-1] for p in paths]
    common = parts[0]
    for other in parts[1:]:
        keep = 0
        for a, b in zip(common, other, strict=False):
            if a != b:
                break
            keep += 1
        common = common[:keep]
        if not common:
            return ""
    return "/".join(common)


def _terms_by_reach(vocab: dict, project, said_already=None) -> list:
    """Where each word actually appears, and whether that names a scope.

    *** THE KEYWORD RULE CATCHES A STATUTE AND MISSES A DOCTRINE. ***
    `nontributary` cites C.R.S. and is caught. `conditional` -- "a claim on water not yet
    diverted, held open by showing reasonable diligence" -- asserts one state's prior-appropriation
    law and names no state, so no list of words was going to find it.

    *** AND "USED IN FEW MODELS" FIRES ON EVERYTHING. ***
    The first version of this rule warned on twelve of sixteen real terms, including one used in
    115 models that is genuinely about the whole warehouse. A guard that fires on almost every row
    is a guard somebody switches off, and then the three rows that mattered go with it.
    So the rule is not "few models". It is **the models that use this word all sit under one
    directory, and most of the project does not** -- which is the only version that can name the
    scope it is asking for, and a warning that can write its own fix is one worth reading.
    """
    models = list((getattr(project, "models", {}) or {}).values())
    if len(models) < 8:
        return []
    already = {i.question.split(".", 1)[-1] for i in said_already or []
               if i.rule == "asserts_law_everywhere"}
    blobs = {m.unique_id: " ".join([m.name, m.description or "", " ".join(m.columns or {}),
                                    m.compiled or ""]).lower() for m in models}
    path_of = {m.unique_id: m.path for m in models}
    out = []
    for term, body in sorted((vocab or {}).items()):
        if isinstance(body, dict) and body.get("applies_to"):
            continue                      # already scoped; the scope is the answer
        word, spaced = str(term).lower(), str(term).replace("_", " ").lower()
        hits = [u for u, blob in blobs.items() if word in blob or spaced in blob]
        if not hits:
            out.append(Issue(f"vocab.{term}", "warn", "term_unused",
                             f"no model in this project names this, in SQL, a column or a "
                             f"description. It is still sent with all "
                             f"{len(models):,} of them, on every call."))
            continue
        if term in already:
            continue                      # the law rule said it louder and asks for the same fix
        where = _common_dir([path_of[u] for u in hits])
        # One directory, and it is not the whole models/ tree.
        if not where or where.count("/") < 1:
            continue
        outside = sum(1 for m in models if not path_of[m.unique_id].startswith(where + "/"))
        if outside < len(models) * 0.25:
            continue                      # it is under that directory because nearly everything is
        # *** AND THE SUGGESTION MUST NOT BE CONFIDENTLY WRONG. ***
        # A subdirectory of `where` in which the word never appears is very likely the exception
        # the scope has to subtract. On the warehouse this was built for that is `models/water/az`
        # -- 70 models sitting INSIDE `models/water`, so the obvious suggestion would have sent
        # one state's law to every model of the other one and read as working.
        hit_set = set(hits)
        kids: dict = {}
        for m in models:
            pth = path_of[m.unique_id]
            if not pth.startswith(where + "/"):
                continue
            rest = pth[len(where) + 1:].split("/")
            if len(rest) < 2:
                continue                  # a file directly in `where`, not a subdirectory
            kid = f"{where}/{rest[0]}"
            k = kids.setdefault(kid, [0, 0])
            k[0] += 1
            k[1] += 1 if m.unique_id in hit_set else 0
        silent = sorted(d for d, (n, h) in kids.items() if n >= 5 and h == 0)
        # *** WITH THE `path:` PREFIX, OR THE SUGGESTION IS A SELECTOR THAT MATCHES NOTHING. ***
        # A bare `models/water/az` has no colon, so `selector` reads it as a MODEL NAME, finds no
        # model called that, and the exclusion silently subtracts nothing. A guard handing out a
        # fix that quietly does nothing is the defect this whole tool is about, produced by the
        # part of it that checks for exactly that.
        drop = " ".join(f"path:{d}" for d in silent)
        fix = (f"`applies_to: \"path:{where}\"`" if not silent else
               f"`applies_to: {{select: \"path:{where}\", exclude: \"{drop}\"}}`")
        note = ("" if not silent else
                f" Note {', '.join(f'`{d}`' for d in silent)}: "
                f"{'it is' if len(silent) == 1 else 'they are'} inside `{where}` and "
                f"{'names' if len(silent) == 1 else 'name'} this word nowhere, so a bare "
                f"`path:{where}` would still send it there.")
        out.append(Issue(
            f"vocab.{term}", "warn", "narrower_than_where_it_is_sent",
            f"every model that names this word is under `{where}` ({len(hits):,} of them), and "
            f"the term is sent to all {len(models):,}, including the {outside:,} outside it. If "
            f"it is only true there, say so: {fix}.{note}"))
    return out
