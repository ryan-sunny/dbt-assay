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


def lint_question(name: str, q: dict, shipped: dict | None = None) -> list[Issue]:
    """Every rule this codebase learned the hard way, applied to one question.

    `shipped` is the bank to check prefix collisions against, keyed by question name exactly as
    `load_all_banks` returns it. It took a dict of prefixes at first, which is a second shape for
    one fact and duly got passed the wrong way round the first time it was used.
    """
    out: list[Issue] = []
    shipped_prefixes = {v["id_prefix"]: k for k, v in (shipped or {}).items()
                        if isinstance(v, dict) and v.get("id_prefix")}
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

    if not crit and kind != "noul":
        add("error", "criteria", "no criteria. The options are the question.")
    return out


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
