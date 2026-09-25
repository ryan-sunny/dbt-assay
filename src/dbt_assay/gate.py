"""One verdict for a change: may it go in?

*** SIX COMMANDS, AND NONE OF THEM SAID YES OR NO. *** (sunny-data, assay-loops.md gap 4) `check
--new-only`, `diff`, `prove`, the premise changes `check` prints, the evaluator's cards and dbt's
own test results each answered part of "is this change safe", in six outputs a reviewer had to
read together. A PR check and an agent after an edit need one answer, with the reasons under it.

Each part says pass, fail, or skipped with why. A part that could not look (no baseline run, no
baseline target for contracts, no run_results) is `skipped`, never a pass, and the verdict says
which parts looked.
"""
from __future__ import annotations

from dataclasses import dataclass, field

HARM = ("test_is_failing", "guarantee_lost", "guarantee_does_not_hold", "key_stopped_holding")


@dataclass
class Part:
    name: str
    status: str = "pass"                      # pass | fail | skipped
    why: str = ""
    items: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"part": self.name, "status": self.status, "why": self.why,
                "items": self.items[:50], "n": len(self.items)}


def _is_evaluator(f) -> bool:
    return bool((getattr(f, "evidence", None) or {}).get("evaluator"))


def _line(f) -> str:
    return f"{f.subject_name}: {f.check}: {(f.summary or '')[:140]}"


def evaluate(*, new: list | None, actions: dict, premises_now: dict, premises_before: dict,
             contract_changes: list | None, allowed: set, test_results: dict | None,
             scope: set | None = None) -> dict:
    """The verdict. `new` is the findings the baseline run did not have (None: no baseline).
    `actions` maps (check, subject) to the configured action. Premises are {id: (status, name)}.
    `contract_changes` is None when there was no baseline target to compare against."""
    parts: list[Part] = []

    p = Part("findings above policy")
    if new is None:
        p.status, p.why = "skipped", "no full `assay check` run in the store to compare against"
    else:
        bad = [f for f in new if actions.get((f.check, f.subject)) == "fail"]
        p.items = [_line(f) for f in bad]
        p.status = "fail" if bad else "pass"
        p.why = (f"{len(bad)} new finding(s) configured to fail" if bad else
                 f"{len(new)} new finding(s), none configured to fail")
    parts.append(p)

    p = Part("evidence of harm")
    if new is None:
        p.status, p.why = "skipped", "no baseline run"
    else:
        bad = [f for f in new if f.check in HARM]
        p.items = [_line(f) for f in bad]
        p.status = "fail" if bad else "pass"
        p.why = (f"{len(bad)} new: a failing test, a lost guarantee, or a key that stopped "
                 f"holding" if bad else "no new failing test, lost guarantee or broken key")
    parts.append(p)

    p = Part("dbt-project-evaluator")
    if new is None:
        p.status, p.why = "skipped", "no baseline run"
    else:
        bad = [f for f in new if _is_evaluator(f)]
        p.items = [_line(f) for f in bad]
        p.status = "fail" if bad else "pass"
        p.why = f"{len(bad)} new violation(s)" if bad else "no new violation"
    parts.append(p)

    p = Part("premises")
    if not premises_before:
        p.status, p.why = "skipped", "no premises recorded by an earlier full run"
    else:
        broke = [(pid, name) for pid, (st, name) in premises_now.items()
                 if st == "broken" and (premises_before.get(pid, ("", ""))[0] != "broken")]
        if scope is not None:
            broke = [b for b in broke if any(s in b[1] for s in scope)]
        p.items = [name for _pid, name in broke]
        p.status = "fail" if broke else "pass"
        p.why = f"{len(broke)} newly broken" if broke else "none newly broken"
    parts.append(p)

    p = Part("contracts")
    if contract_changes is None:
        p.status, p.why = "skipped", "no --baseline target to compare what models mean against"
    else:
        named = [c for c in contract_changes if c.model in allowed]
        unnamed = [c for c in contract_changes if c.model not in allowed]
        p.items = [f"{c.model}: {c.detail}" for c in unnamed]
        p.status = "fail" if unnamed else "pass"
        p.why = ((f"{len(unnamed)} model(s) changed meaning and the change is not named "
                  f"(--allow-contract)") if unnamed else
                 (f"{len(named)} change(s), all named" if named else "no model changed meaning"))
    parts.append(p)

    p = Part("dbt tests")
    if not test_results:
        p.status, p.why = "skipped", "no run_results.json from a build in the target folder"
    else:
        bad = sorted(t for t, (st, _at) in test_results.items() if st in ("fail", "error"))
        if scope is not None:
            bad = [t for t in bad if any(f".{s}" in t or f"_{s}_" in t for s in scope)]
        p.items = bad
        p.status = "fail" if bad else "pass"
        p.why = f"{len(bad)} failing" if bad else f"{len(test_results)} ran, none failing"
    parts.append(p)

    failed = [x for x in parts if x.status == "fail"]
    looked = [x for x in parts if x.status != "skipped"]
    verdict = "fail" if failed else "pass"
    line = (f"FAIL: {', '.join(x.name for x in failed)}" if failed else
            f"PASS: {len(looked)} of {len(parts)} parts looked"
            + (f" ({', '.join(x.name for x in parts if x.status == 'skipped')} skipped)"
               if len(looked) < len(parts) else ""))
    return {"verdict": verdict, "line": line, "parts": [x.as_dict() for x in parts]}


def markdown(result: dict) -> str:
    """For a PR comment."""
    icon = {"pass": "pass", "fail": "**fail**", "skipped": "skipped"}
    out = [f"### assay gate: {result['verdict'].upper()}", "", "| part | result | why |",
           "|---|---|---|"]
    for p in result["parts"]:
        out.append(f"| {p['part']} | {icon[p['status']]} | {p['why']} |")
    for p in result["parts"]:
        if p["status"] == "fail" and p["items"]:
            out += ["", f"**{p['part']}**", *[f"- {x}" for x in p["items"][:20]]]
    return "\n".join(out) + "\n"
