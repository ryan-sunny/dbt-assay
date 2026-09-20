"""*** A BADLY SHAPED QUESTION DOES NOT FAIL. IT ANSWERS CONFIDENTLY AND USELESSLY. ***

`keys_on_a_non_unique_column` read 0.73 to 0.85 on every model tested, clean or broken, and looked
like a working check for weeks. Nothing about it errored. Every rule in `lint.py` is a shape that
has already been measured to fail here or is published by TypeSafe as a limitation of the model.

The linter itself was calibrated the only honest way: run against assay's own fifteen hand-tuned
banks, it flagged four questions, and every one of the four was the LINTER being wrong. `sum`
matched "summary"; `not_a_statement` is a no-match option the pattern missed; the overlap metric
divided by the shorter description and so punished a terse option beside a verbose one.
"""
from dbt_assay.contracts import SHIPPED
from dbt_assay.lint import lint_all, lint_question


def test_the_shipped_banks_are_clean():
    """These are hand-tuned and measured. A linter that nags about them is a linter nobody runs."""
    assert lint_all(SHIPPED, SHIPPED) == []


def test_the_reader_sees_something():
    """A linter that inspects nothing reports a clean project, which is the failure mode this
    codebase has hit twice."""
    assert len(SHIPPED) >= 15


def _bad(**over) -> dict:
    q = {"type": "choice", "prompt_version": "x.v1", "id_prefix": "zz",
         "instructions": {"question": "Is this column an identifier?"},
         "criteria": {"yes": {"what": "It identifies the row uniquely and is part of the key."},
                      "no": {"what": "It does not identify the row and is not part of any key."},
                      "cannot_tell": {"what": "The evidence does not settle it either way."}}}
    q.update(over)
    return q


def _rules(q, shipped=None) -> set:
    return {i.rule for i in lint_question("q", q, shipped)}


def test_it_refuses_a_question_that_asks_for_arithmetic():
    """Measured: asked whether a date expression implemented 'the last day of the second month
    following', Jev scored the CORRECT one 0.39 and a WRONG one 0.62."""
    q = _bad(instructions={"question": "Calculate the difference between the two amounts."})
    assert "not_a_calculator" in _rules(q)
    assert "not_a_calculator" not in _rules(_bad())          # and does not nag otherwise


def test_summary_is_not_arithmetic():
    """The first pattern matched 'sum' inside 'summary' and flagged a shipped question."""
    q = _bad(instructions={"question": "Is the summary accurate about what this model emits?"})
    assert "not_a_calculator" not in _rules(q)


def test_a_choice_must_let_the_model_decline():
    """A real division bug surfaced ONLY because the model could say the answer was off the list."""
    q = _bad(criteria={"yes": {"what": "It identifies the row uniquely, always."},
                       "no": {"what": "It never identifies the row on its own."}})
    assert "no_match_option" in _rules(q)


def test_not_a_statement_counts_as_declining():
    """assay's own `sentence_is_a_claim` uses it, and the first pattern missed it."""
    q = _bad(criteria={"a_claim": {"what": "It asserts something checkable about the output."},
                       "rationale": {"what": "It explains why the code is the way it is."},
                       "not_a_statement": {"what": "A heading or fragment with no assertion."}})
    assert "no_match_option" not in _rules(q)


def test_two_families_cannot_share_a_prefix():
    """One would silently absorb the other's verdicts, which is this codebase's recurring bug."""
    q = _bad(id_prefix="role")
    assert "id_prefix" in _rules(q, SHIPPED)


def test_a_question_with_no_prefix_files_its_verdicts_nowhere():
    q = _bad()
    del q["id_prefix"]
    assert "id_prefix" in _rules(q)


def test_options_described_alike_are_flagged_but_terse_ones_are_not():
    """Dividing by the shorter description punished a terse option beside a verbose one, and
    flagged two genuinely distinct feed options."""
    same = _bad(criteria={
        "a": {"what": "The column identifies one row uniquely across the whole table and is "
                      "part of the declared primary key for this model."},
        "b": {"what": "The column identifies one row uniquely across the whole table and is "
                      "part of the declared primary key for this model, mostly."},
        "cannot_tell": {"what": "The evidence does not settle it either way."}})
    assert "options_not_separated" in _rules(same)
    assert "options_not_separated" not in _rules(_bad())
