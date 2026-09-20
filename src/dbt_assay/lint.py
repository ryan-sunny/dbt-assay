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
        from .subjects import KINDS
        if subj not in KINDS:
            add("error", "subject", f"unknown subject {subj!r}. Use one of {sorted(KINDS)}.")
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
    elif q.get("finding_when") is not None:
        add("warn", "finding_when",
            "`finding_when` without a `subject` does nothing: only the generic runner reads it.")

    if not crit and kind != "noul":
        add("error", "criteria", "no criteria. The options are the question.")
    # An acknowledged rule is silenced HERE, at the end, so the checks above stay simple and an
    # acknowledgement of a rule that never fired is itself visible as dead config.
    return [i for i in out if i.rule not in ack or i.rule == "acknowledge"]


def _check_options_separate(name: str, crit: dict, out: list[Issue]) -> None:
    """*** DESCRIPTIONS MUST SEPARATE THE OPTIONS FROM EACH OTHER. ***

    Measured on assay's own question: `documentation_states_it` and `documentation_implies_it`
    both lead to the same action and are not separable, and the answers sat at 0.36-0.39 until
    they were merged. A description that explains an option without distinguishing it from its
    neighbour gives the model nothing to cut on.
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
    """What each question silenced, and why. Shown rather than hidden: an acknowledgement is a
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
}


def caller_of(name: str, bank: dict | None = None) -> tuple[str, str, str] | None:
    """(module, command, state) for a family, or None when nothing asks it.

    A family that declares a `subject:` is asked by the GENERIC RUNNER, whatever its name. That is
    the whole point of the runner, and reporting it as uncalled would be the same lie in reverse.
    """
    if bank and bank.get("subject"):
        return ("subjects", "assay ask", f"one {bank['subject']}")
    return CALLERS.get(name)


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
        state = {"the_question": (bank.get("instructions") or {}).get("question", ""),
                 "options": {k: _text(v) for k, v in crit.items()}}
        try:
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
        if overlap >= 0.6:
            worst = "several" if probs.get("several_overlap", 0) >= probs.get("two_overlap", 0) \
                else "one pair"
            out.append(Issue(
                name, "warn", "options_overlap",
                f"two or more options could both be correct about the same subject (p={overlap:.2f}, "
                f"{worst}). Which one comes back is then arbitrary for those cases. The static "
                f"rule cannot see this: it compares words, and an overlap of MEANING shares a "
                f"situation rather than a vocabulary."))
        if through >= 0.6:
            out.append(Issue(
                name, "warn", "options_fall_through",
                f"a realistic subject fits NO option and there is no way to decline (p={through:.2f})."))
    return out
