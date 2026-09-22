"""*** ONE FACT, ONE SPELLING -- AND THIS FACT HAS TWO PLACES IT CAN BE READ. ***

`input_tokens` is on `model_decisions`, where it is the CALL's number repeated on every answer,
and on `model_calls`, where it is the call's number once. Both are true and only one of them can
be summed. Nothing enforces that by type, so it is enforced here.

The second guard is the one this codebase has paid for three times: a value that must reach every
call site and reaches most of them. `file_checksum` is written by `decide()` from a map the
command registers, and a command that forgets produces answers assay can never check.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import dbt_assay

SRC = Path(dbt_assay.__file__).parent
SUMS = re.compile(r"sum\(\s*[a-z_]*\.?input_tokens\s*\)", re.IGNORECASE)

# *** WHERE A JUDGED CALL LEGITIMATELY HAS NO PROJECT, NAMED RATHER THAN SKIPPED. ***
# A declared exception with a reason, the way `EMITTED_IDS` is. An undeclared one is a failure.
NO_PROJECT = {
    "disagreements": "asks about `pair::` keys -- two verdicts somebody gave, never a dbt model, "
                     "so there is no source file to checksum",
    "judge_overlap": "asks about `bank::` keys -- whether two OPTIONS of a question overlap. The "
                     "subject is assay's own question bank, not anybody's SQL, and it already "
                     "re-asks on a reworded question through the state hash",
}


def test_nothing_sums_the_per_answer_token_column():
    """*** THE SUM THAT PUT $4.24 IN A BUILD SPEC. ***

    A batch of eight questions about one state is one call and eight rows, each carrying the whole
    call's tokens. On the field store that reads 101,163,351 against 31,426,560 spent. Totals go
    through `model_calls`, which has one row per call and cannot double count.
    """
    bad = []
    for f in sorted(SRC.rglob("*.py")):
        body = f.read_text()
        for m in SUMS.finditer(body):
            window = body[max(0, m.start() - 400):m.end() + 400]
            if "model_decisions" in window and "model_calls" not in window:
                line = body[:m.start()].count("\n") + 1
                bad.append(f"{f.name}:{line}: {m.group(0)}")
    assert not bad, (
        f"summing the per-answer token column counts a batched call once per answer: {bad}. "
        f"Totals belong in `model_calls`.")


def _functions_that_decide(tree) -> dict:
    """{function name: its node}, for every function that calls `decide`."""
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in ast.walk(node):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == "decide"):
                out[node.name] = node
                break
    return out


def test_every_judged_call_site_registers_the_project():
    """*** A VALUE THAT MUST REACH SEVENTEEN CALL SITES REACHES MOST OF THEM. ***

    `decide()` gets a store and a client and never a project, so the checksum comes off a map the
    command registers with `store.use_project(project)`. A command that forgets writes answers
    with no checksum, which `assay stale` reports as "cannot be checked" -- honest, and still a
    gap nobody would look for. This finds it at import time instead.
    """
    missing = []
    for f in sorted(SRC.rglob("*.py")):
        tree = ast.parse(f.read_text())
        for name, node in _functions_that_decide(tree).items():
            if name in NO_PROJECT:
                continue
            registers = any(
                isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                and c.func.attr == "use_project"
                for c in ast.walk(node))
            if not registers:
                missing.append(f"{f.name}:{name}")
    assert not missing, (
        f"these call `decide()` and never register a project, so their answers carry no "
        f"checksum and can never be checked for staleness: {missing}. Add "
        f"`store.use_project(project)`, or declare the exception with its reason in NO_PROJECT.")


def test_the_declared_exceptions_still_exist():
    """A named exception for a function that has been renamed or deleted is a guard pointing at
    nothing. A scanner matching nothing passes wrongly, which is the failure this file is about."""
    names = set()
    for f in sorted(SRC.rglob("*.py")):
        names |= set(_functions_that_decide(ast.parse(f.read_text())))
    gone = sorted(set(NO_PROJECT) - names)
    assert not gone, f"NO_PROJECT names functions that no longer ask anything: {gone}"


def test_every_exception_carries_a_reason():
    assert all(len(v) > 30 for v in NO_PROJECT.values()), "a bare exception is not a reason"
