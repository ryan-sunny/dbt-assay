"""*** NINE OF TEN VERDICT FAMILIES AUTHORIZED NOTHING, AND NOTHING COULD SHOW IT. ***

Verdicts are recorded per QUESTION FAMILY (`column_role`). `apply_policy` counted them per FINDING
(`identifier_outside_grain`). No finding was ever named `column_role`, so an afternoon of ruling on
roles left the gate floor at zero and the tool reported nothing wrong. The one family that worked
did so by accident: its finding happened to share its question's name.

A gate nobody can reach is worse than no gate, because the docs say it exists.
"""
import ast
import inspect

from dbt_assay import judged, relate
from dbt_assay.checks import structural
from dbt_assay.contracts import load_all_banks


def _findings_declared_in(mod) -> list[tuple[str, str]]:
    """(check, rests_on) for every `Finding(...)` constructed in a module.

    Parsed, not regexed: a pattern that silently matches nothing passes for the wrong reason, and
    this file exists because a gate was wired to something nobody could see.
    """
    out = []
    for node in ast.walk(ast.parse(inspect.getsource(mod))):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Finding"):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        check = kw.get("check")
        if not isinstance(check, ast.Constant):
            continue
        rests = kw.get("rests_on")
        out.append((check.value,
                    rests.value if isinstance(rests, ast.Constant) else ""))
    return out


def test_the_reader_itself_sees_something():
    """A scanner that matches nothing reports a clean project. Assert it found the findings."""
    assert len(_findings_declared_in(judged)) >= 5
    assert len(_findings_declared_in(structural)) >= 5


def test_every_judged_finding_names_a_question_that_exists():
    banks = set(load_all_banks())
    bad = []
    for check, rests in _findings_declared_in(judged):
        if not rests:
            bad.append(f"{check} declares no rests_on, so its verdicts can never be counted")
        elif rests not in banks:
            bad.append(f"{check} rests on {rests!r}, which is not a question bank")
    assert not bad, bad


def test_structural_findings_rest_on_nothing_and_may_gate_at_once():
    """A parser decided it. There is no error rate to measure, so waiting for verdicts would be
    waiting for a number that means nothing."""
    for check, rests in _findings_declared_in(structural) + _findings_declared_in(relate):
        assert rests == "", f"{check} is structural and must not wait on verdicts"


def test_the_gate_counts_on_the_question_and_not_on_the_finding(tmp_path):
    """The regression itself, exercised rather than read."""
    from dbt_assay.checks.structural import Finding
    from dbt_assay.config import Config

    (tmp_path / "audit.yml").write_text(
        "questions:\n"
        "  identifier_outside_grain:\n"
        "    act:\n"
        '      fail: "p > 0.5"\n')
    cfg = Config.load(tmp_path)
    f = Finding(check="identifier_outside_grain", rests_on="column_role",
                subject="m", subject_name="m", file="m.sql",
                summary="s", detail="d", evidence={"confidence": 0.9})

    class _Store:
        def __init__(self, counts):
            self._c = counts

        def adjudication_counts(self, source="human"):
            return self._c

    # Verdicts recorded under the FINDING's name must not authorize it...
    kept, _w = judged.apply_policy([f], cfg, _Store({"identifier_outside_grain": 99}))
    assert kept[0][1] == "queue", "a finding's own name is not a question and must not gate it"

    # ...and verdicts under the QUESTION it rests on must.
    kept, _w = judged.apply_policy([f], cfg, _Store({"column_role": 99}))
    assert kept[0][1] == "fail"


def test_a_family_with_no_finding_is_declared_rather_than_silently_useless():
    """Some banks have no finding resting on them yet. That is fine and it must be VISIBLE, so
    nobody spends an afternoon ruling on a question that gates nothing."""
    banks = set(load_all_banks())
    used = {r for _c, r in _findings_declared_in(judged) if r}
    orphans = banks - used
    # This is an inventory, not a failure: it documents the real state in one place.
    assert orphans == judged.FAMILIES_WITHOUT_FINDINGS, (
        f"the set of questions that gate nothing changed: {sorted(orphans)}")


def test_the_review_loop_says_which_rows_will_never_move_a_gate(tmp_path, monkeypatch):
    """Someone sitting down to move a gate should be told, before the keypresses, which of the
    rows in front of them cannot move one."""
    from typer.testing import CliRunner

    from dbt_assay.cli import app
    from dbt_assay.store import Store

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    sp = tmp_path / "s.duckdb"
    st = Store(str(sp))
    st.con.execute("""insert into model_decisions
        (decision_key, question, kind, answer, confidence, probabilities, state_hash,
         prompt_version, model_version, call_id, caller, decided_at)
        values ('model.p.m', 'null__amount', 'choice', 'unknown', 0.4, '{}', 'h',
                'v1', 'jev', 'c', 'test', current_timestamp)""")
    st.close()

    r = CliRunner().invoke(app, ["review", "-i", "--store", str(sp), "--limit", "1"],
                           input="s\n")
    assert r.exit_code == 0, r.output
    assert "null_meaning" in r.output
    assert "moves no gate" in r.output, r.output
