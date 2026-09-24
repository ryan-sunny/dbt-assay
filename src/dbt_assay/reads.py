"""`assay read`: a judged reading of every card, written to the file `review --reads` takes.

*** THE MOST EXPENSIVE HUMAN STEP IN THE LOOP WAS THE ONLY ONE WITH NO JUDGED PATH. ***
`assay review --reads` pre-fills an agent's reading on each card, and that is what makes a card
cheap to answer. Nothing in assay wrote the file: `reads_path` was only ever read. The skill said an
agent writes it "once, offline", which meant a conversation reading one finding at a time. Priced
from a real ledger it is about $0.000033 a card by the judged tier.

*** A FILE, NEVER A VERDICT. ***
The output is a JSON a person opens beside the form. Nothing here writes to `adjudications`: an
agent is not the authority, and a reading that recorded itself as a ruling would be one.

*** THE WHY IS SELECTED, NEVER WRITTEN. ***
Jev answers with a choice and its probabilities; it does not write prose, and assay would not ask
it to. The `why` is the chosen option's own criterion and how sure the answer was -- the same rule
`claims` follows: code and the bank supply the words, the judgment picks.
"""
from __future__ import annotations

import hashlib
import re

from .contracts import QUESTIONS

FAMILY = "finding_is_correct"
LOCATOR = "reading_rests_on"

# *** A DISMISSAL IS NOT SUGGESTED BELOW THIS. ***
# Reported from the field (25.1): 47 of 407 readings suggested `disagree` -- which removes a
# finding permanently once a person clicks it -- at a median confidence of 0.22, 44 of them below
# 0.5. TypeSafe's own guidance for a choice under 0.5 is "the model is genuinely unsure: route to a
# person, don't guess". Below it the reading is filed `unclear`, which gates nothing and is the
# store's own word for "the evidence does not settle it".
DISMISS_FLOOR = 0.5

# How many lines a card offers the locator. A choice takes up to 255 options; forty keeps the
# state small and still covers the lines any one finding names.
MAX_LINES = 40

# What each option becomes on a card. The form's own verdict vocabulary, so a reading can be
# confirmed with one click rather than translated.
VERDICT_OF = {
    "correct": "agree",
    "correct_and_deliberate": "accept",
    "misreads_the_sql": "disagree",
    "cannot_tell": "unclear",
}


def bank() -> dict:
    return QUESTIONS[FAMILY]


def versions() -> dict:
    """{id_prefix: prompt_version} for the two families a `read` call carries."""
    return {QUESTIONS[f]["id_prefix"]: QUESTIONS[f]["prompt_version"] for f in (FAMILY, LOCATOR)}


def _names(sub_state: dict) -> set[str]:
    """Identifiers the findings mention: backticked names, and string values in their evidence."""
    out: set[str] = set()
    for f in sub_state.get("findings") or []:
        text = " ".join([f.get("summary", ""), f.get("claim", "")])
        out |= {m.lower() for m in re.findall(r"`([A-Za-z_][A-Za-z0-9_.]*)`", text)}
        stack = [f.get("evidence") or {}]
        while stack:
            v = stack.pop()
            if isinstance(v, dict):
                stack += list(v.values())
            elif isinstance(v, list | tuple):
                stack += list(v)
            elif isinstance(v, str):
                out |= {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", v)}
    return {n.split(".")[-1] for n in out if len(n) > 2}


# A parenthesis that opens a CTE, a subquery or a list rather than a call or a window.
_OPENER = re.compile(r"(\b(as|from|join|in|exists|with|union|select)\s*\(|^\()\s*$",
                     re.IGNORECASE)


def _logical_lines(sql: str) -> list[tuple[str, str]]:
    """(the construct, comments dropped and whitespace collapsed; its first line as written).

    A window or a join condition spread over several lines is ONE construct, joined until its
    parentheses close, at most five lines. Offered line by line, the field's first run pointed a
    card at `n.comid) as rn` -- the tail of a window rather than the window. Only a CALL's or an
    `over (`'s parenthesis joins: a CTE, a subquery or an `in (` list opens one too, and joining
    those swallowed whole CTE heads and lost the window inside them.
    """
    code = [re.sub(r"--.*$", "", x).strip() for x in sql.splitlines()]
    raw = [x.strip() for x in sql.splitlines()]
    out, i = [], 0
    while i < len(code):
        t = code[i]
        depth = t.count("(") - t.count(")")
        if depth > 0 and not _OPENER.search(t):
            # Five lines of CODE: a comment inside a window is not part of its size.
            buf, j = [t], i + 1
            while depth > 0 and j < len(code) and len([x for x in buf if x]) < 5:
                buf.append(code[j])
                depth += code[j].count("(") - code[j].count(")")
                j += 1
            if depth <= 0:
                out.append((" ".join(x for x in buf if x), raw[i]))
                i = j
                continue
        out.append((t, raw[i]))
        i += 1
    return out


def candidate_lines(sub_state: dict) -> list[tuple[str, str]]:
    """Constructs of the state's own `sql` that mention something the findings name, in order:
    (construct, its first line as written).

    From the SQL the question is shown, so the answer is a line Jev could see. A comment is never
    a candidate: the claim it makes is already on the card, and the locator's job is the CODE
    that decides it -- the field's first run pointed a `code_contradicts_a_claim` card at the
    comment it was quoting.
    """
    names = _names(sub_state)
    out, seen = [], set()
    for text, first in _logical_lines(sub_state.get("sql") or ""):
        if len(text) < 4 or text in seen or text.startswith(("/*", "*")):
            continue
        words = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text)}
        if names and not (words & names):
            continue
        seen.add(text)
        out.append((text, first))
        if len(out) >= MAX_LINES:
            break
    return out


def locator(sub_state: dict) -> tuple[dict, dict] | tuple[None, None]:
    """({"rests": question}, {option: line}) for this card, or (None, None) with no SQL to point at.

    `a_listed_line` in the bank is a template: each candidate becomes `line_<hash of its text>`,
    described by the template's sentence and the line itself. The model chooses; the line on the card is copied.
    """
    from .jev import choice
    lines = candidate_lines(sub_state)
    if not lines:
        return None, None
    q = QUESTIONS[LOCATOR]
    crit = q["criteria"]
    lead = " ".join(str(crit["a_listed_line"]["what"]).split())
    # *** KEYED BY THE LINE, NEVER BY ITS POSITION. ***
    # The locator shares `finding_is_correct`'s version, and a cached answer is served by
    # (subject, question, version, state). `line_3` would be served back after the candidate list
    # moved -- an edit, or this code changing -- and name whatever line is third now. A key made
    # from the text either names the same text or names nothing, and nothing is dropped.
    keyed = {f"line_{hashlib.sha1(t.encode()).hexdigest()[:10]}": (t, f) for t, f in lines}
    options = {k: {"what": f"{lead} The line: `{t[:220]}`"} for k, (t, _f) in keyed.items()}
    options["none_of_these"] = crit["none_of_these"]
    return ({q["id_prefix"]: choice(q["instructions"], options)},
            {k: {"text": t, "first": f} for k, (t, f) in keyed.items()})


def place(line: str, file_text: str) -> int | None:
    """The 1-based line in the model's own file carrying exactly this text, when exactly one does.

    The candidates come from the compiled SQL a person does not edit; the card points at the file
    they do. Two matches, or none (a macro expanded it), is no line number rather than a guess.
    """
    hits = [i for i, raw in enumerate(file_text.splitlines(), 1) if raw.strip() == line]
    return hits[0] if len(hits) == 1 else None


def why(answer: dict) -> str:
    """The option's own criterion, and how sure the answer was. Nothing composed."""
    crit = (bank().get("criteria") or {}).get(answer["answer"]) or {}
    text = " ".join(str(crit.get("what") or answer["answer"]).split())
    probs = answer.get("probabilities") or {}
    p = probs.get(answer["answer"], answer.get("confidence"))
    ranked = sorted(((v, k) for k, v in probs.items() if k != answer["answer"]), reverse=True)
    tail = ""
    if ranked and ranked[0][0] is not None and ranked[0][0] >= 0.15:
        tail = f"; next most likely: {ranked[0][1]} at {ranked[0][0]:.2f}"
    head = f"{answer['answer']}" + (f" at {float(p):.2f}" if p is not None else "")
    return f"{head}{tail}. {text}"


def reading(answer: dict, rests: dict | None = None, lines: dict | None = None,
            file: str = "", file_text: str = "") -> dict:
    """One entry of the reads file: the verdict it suggests, how sure, and the line it rests on."""
    verdict = VERDICT_OF.get(answer["answer"], "unclear")
    conf = answer.get("confidence")
    text = why(answer)
    floored = verdict == "disagree" and conf is not None and float(conf) < DISMISS_FLOOR
    if floored:
        verdict = "unclear"
        text = (f"read as {answer['answer']} at confidence {float(conf):.2f}, below "
                f"{DISMISS_FLOOR} -- too unsure to suggest a dismissal, so filed unclear. " + text)
    # The parts, so a card can show ONE number with its name instead of a probability and a
    # confidence side by side with nothing saying which is which.
    crit = (bank().get("criteria") or {}).get(answer["answer"]) or {}
    probs = answer.get("probabilities") or {}
    ranked = sorted(((v, k) for k, v in probs.items() if k != answer["answer"] and v is not None),
                    reverse=True)
    out = {"verdict": verdict, "why": text, "answer": answer["answer"], "confidence": conf,
           "reason": " ".join(str(crit.get("what") or answer["answer"]).split()),
           "read_by": f"assay read ({bank()['prompt_version']})"}
    if ranked and ranked[0][0] >= 0.15:
        out["runner_up"] = f"{ranked[0][1]} at {ranked[0][0]:.2f}"
    if floored:
        out["floored"] = True
    chosen = (rests or {}).get("answer")
    if lines and chosen in lines:
        t = lines[chosen]
        out["rests_on"] = {"text": t["text"], "file": file, "line": place(t["first"], file_text),
                           "confidence": (rests or {}).get("confidence")}
    elif lines and chosen == "none_of_these":
        out["rests_on"] = {"text": "", "file": file, "line": None, "none": True,
                           "confidence": (rests or {}).get("confidence")}
    return out
