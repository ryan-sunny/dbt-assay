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

from .contracts import QUESTIONS

FAMILY = "finding_is_correct"

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


def reading(answer: dict) -> dict:
    """One entry of the reads file."""
    return {"verdict": VERDICT_OF.get(answer["answer"], "unclear"),
            "why": why(answer),
            "answer": answer["answer"],
            "confidence": answer.get("confidence"),
            "read_by": f"assay read ({bank()['prompt_version']})"}
