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


def test_asking_whether_a_number_is_the_right_size_is_refused():
    """*** VERIFIED ON REAL VALUES, AND assay's OWN UNITS FAMILY FAILED IT. ***

    v1 asked whether magnitudes were plausible for the unit a name implies. Against real warehouse
    values it called 218,235 "acres" CONSISTENT at 0.82 and 4,073,925 "acre-feet" CONSISTENT at
    0.54 -- wrong by 43,560x and 325,851x. TypeSafe publish the reason: the model cannot reliably
    judge whether two values are near each other. The instructions held no arithmetic WORD, so the
    calculator rule missed it entirely.
    """
    q = _bad(instructions={"question": "Do the magnitudes look plausible for that unit?"})
    assert "numeric_magnitude" in _rules(q)


def test_it_does_not_punish_code_counting_and_the_model_judging_consequence():
    """`severity_fit` asks how serious a violation is GIVEN a blast radius code already counted,
    and it was verified working: 1.59 on a primary key twenty-four dashboards read, 0.14 on a note
    column nobody reads. That is the CORRECT pattern and must not be flagged."""
    assert "numeric_magnitude" not in {i.rule for i in lint_question(
        "severity_fit", SHIPPED["severity_fit"], SHIPPED)}


def test_a_subject_with_no_finding_when_is_an_error_not_a_footnote():
    """*** THE DEAD QUESTION PROBLEM WEARING A NEW HAT. ***

    Before the runner, a family with a new name was asked by nothing. With it, a family with a
    subject and no `finding_when` is asked, answered, PAID FOR, stored -- and still produces
    nothing. Reported from the field, where the old wording ("that is valid") undersold it.
    """
    q = _bad(subject="window")
    issues = {(i.level, i.rule) for i in lint_question("q", q)}
    assert ("error", "finding_when") in issues
    q2 = _bad(subject="window", finding_when=["yes"])
    q2["criteria"] = {"yes": {"what": "The window orders by an adjudication date."},
                      "no": {"what": "The window orders by an appropriation date."},
                      "cannot_tell": {"what": "The columns do not settle which it is."}}
    assert "finding_when" not in {i.rule for i in lint_question("q", q2)}


def test_a_rule_can_be_acknowledged_with_a_reason_and_not_without_one():
    """*** A LINT WITH NO WAY TO SAY "I KNOW, AND HERE IS WHY" GETS MUTED WHOLESALE. ***

    Reported from the field: a question earned `multi_hop` for reading a partition before an order
    by, the rule was RIGHT that it costs accuracy, and the author kept it because one hop could not
    distinguish the shapes. That trade cannot be expressed by the lint, so it is expressed in the
    question -- with a reason, exactly as a waiver requires one.
    """
    from dbt_assay.lint import acknowledged_issues

    q = _bad(instructions={"question": "Read the PARTITION first, and then the ORDER BY."})
    assert "multi_hop" in {i.rule for i in lint_question("q", q)}

    ok = dict(q, acknowledge={"multi_hop": "one hop cannot distinguish the shapes"})
    assert "multi_hop" not in {i.rule for i in lint_question("q", ok)}
    shown = acknowledged_issues({"q": ok})
    assert [(i.rule, i.level) for i in shown] == [("multi_hop", "acknowledged")]

    silent = dict(q, acknowledge={"multi_hop": ""})
    assert "acknowledge" in {i.rule for i in lint_question("q", silent)}


def test_the_overlap_check_sums_the_distribution_instead_of_gating_on_confidence():
    """*** CONFIDENCE IS DISTRIBUTION CONCENTRATION, NOT EVIDENCE STRENGTH. ***

    `two_overlap` and `several_overlap` are two ways of saying YES, so a clear answer splits its
    mass across them and confidence FALLS. Measured on the pair that prompted this check:
    0.58 + 0.28 = 0.86 that an overlap exists, at confidence 0.47. Gating on confidence missed it
    -- the exact mistake TypeSafe warn about and this repo documents in its own README.
    """
    from dbt_assay.lint import judge_overlap

    class _Client:
        def __init__(self, probs):
            self.probs = probs

        def ask(self, _state, _q, caller=""):
            return {"answers": {"overlap": {
                "choice": max(self.probs, key=self.probs.get),
                "confidence": 0.47,                    # low, because the mass is SPLIT
                "probabilities": self.probs}}}

    bank = {"q": {"type": "choice", "instructions": {"question": "?"},
                  "criteria": {"a": {"what": "one"}, "b": {"what": "two"},
                               "cannot_tell": {"what": "three"}}}}

    split = _Client({"several_overlap": 0.58, "two_overlap": 0.28, "no_overlap": 0.13})
    assert [i.rule for i in judge_overlap(bank, split)] == ["options_overlap"]

    clean = _Client({"no_overlap": 0.91, "two_overlap": 0.05, "several_overlap": 0.02})
    assert judge_overlap(bank, clean) == []

    through = _Client({"a_case_falls_through": 0.72, "no_overlap": 0.2})
    assert [i.rule for i in judge_overlap(bank, through)] == ["options_fall_through"]


def test_a_two_option_question_is_not_asked_about_overlap():
    """Two options cannot overlap without being identical, and the static rule catches that."""
    from dbt_assay.lint import judge_overlap

    class _Boom:
        def ask(self, *_a, **_k):
            raise AssertionError("should not have been asked")

    bank = {"q": {"type": "choice", "criteria": {"a": {"what": "x"}, "b": {"what": "y"}}}}
    assert judge_overlap(bank, _Boom()) == []


def test_a_question_asking_for_state_its_subject_does_not_carry_is_an_error():
    """*** IT LINT-PASSED, WAS ASKED, ANSWERED, PAID FOR AND STORED, AND NEVER FIRED. ***

    Reported from the field and it cost an hour: two custom questions asked about `filters`,
    which only a `model` carries. A question about an ABSENT predicate cannot be asked one
    predicate at a time, and an `edge` does not carry filters at all. Nothing said so.
    """
    from dbt_assay.lint import lint_question

    q = {"id_prefix": "w.x", "subject": "predicate", "type": "choice",
         "prompt_version": "v1", "finding_when": ["unchecked"],
         "instructions": {"question": "Does this model check the id resolves?",
                          "note": "Read `reads` and `filters` to decide."},
         "criteria": {"unchecked": {"what": "nothing here would notice a dangling id"},
                      "checked": {"what": "it joins the relation holding the sections"},
                      "none_of_these": {"what": "the model has no such id at all"}}}
    hits = [i for i in lint_question("w.x", q)
            if i.rule == "state_field_the_subject_does_not_carry"]
    assert len(hits) == 1, [i.detail for i in lint_question("w.x", q)]
    assert "`filters`" in hits[0].detail
    assert "`model` does" in hits[0].detail
    assert hits[0].level == "error"

    # Declared as the kind that DOES carry it, the same question is clean.
    q["subject"] = "model"
    assert not [i for i in lint_question("w.x", q)
                if i.rule == "state_field_the_subject_does_not_carry"]


def test_the_state_field_rule_does_not_fire_on_ordinary_english():
    """*** A RULE THAT FIRES ON ALMOST EVERY QUESTION IS ONE SOMEBODY SWITCHES OFF. ***

    The first version matched the bare word and flagged three shipped questions: "a claim about a
    column" is English about columns, not a reference to the `column` key. Backticks are how
    somebody means the field, and it is the convention every question here follows.
    """
    from dbt_assay.lint import lint_question

    q = {"id_prefix": "w.y", "subject": "model", "type": "choice", "prompt_version": "v1",
         "finding_when": ["bad"],
         "instructions": {"question": "Does the volume contradict a claim about a column?",
                          "note": "A parent model may describe the child differently."},
         "criteria": {"bad": {"what": "the claim is contradicted by what was measured"},
                      "good": {"what": "the claim holds against the measurement"},
                      "none_of_these": {"what": "no claim about volume is made here"}}}
    assert not [i for i in lint_question("w.y", q)
                if i.rule == "state_field_the_subject_does_not_carry"]
