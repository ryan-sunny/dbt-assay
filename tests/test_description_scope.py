"""`description_contradicts_the_code` judges the DESCRIPTION.

*** IT USED TO SEND THE COMMENT BLOCK TOO, AND THEN FILE THE RESULT AGAINST THE DESCRIPTION. ***
The finding's own evidence had to say "the contradiction is in one of these", because the check
genuinely could not tell you which. Measured on a 358-model warehouse: 72% of the models it fired
on were already carrying a `code_contradicts_a_claim` finding, which quotes the exact sentence.

And the descriptions it fired on were stubs. The project's descriptions run to a median of 26 words
and a 25th percentile of 11; NINE of its eighteen findings were on descriptions under ten --
"Staging: Tempe AZ commercial permits." at p=0.77.
"""
from types import SimpleNamespace

from dbt_assay import semantics as sem


def _subject(purpose, comments="-- the model's own long comment block explaining everything"):
    return SimpleNamespace(name="m", purpose=purpose, header=comments, comments=comments,
                           contract={"grain": "one row per thing"},
                           predicates=["x is not null"], nested=[])


LONG = ("Commercial building permits for Boulder, with residential filtered out by permit type, "
        "one row per permit number, carrying the contractor and the issue date.")


def test_the_comment_block_is_not_sent():
    """It is prose, and it is handled where it can be handled properly.

    `code_contradicts_a_claim` splits the comments into atomic claims and judges each against the
    code with the sentence quoted. That is what this check could never do.
    """
    st = sem.description_state(_subject(LONG))
    assert st is not None
    assert "documentation_in_the_file" not in st, st.keys()
    assert st["description"] == LONG


def test_a_description_too_short_to_say_anything_is_not_judged():
    """There is nothing in four words for SQL to contradict.

    Asking anyway produces a confident answer to a question that was never asked -- p=0.82 on
    "Staging: Gilbert AZ commercial building permits."
    """
    assert sem.description_state(_subject("Staging: Gilbert AZ commercial building permits.")) is None
    assert sem.description_state(_subject("Staging: Tempe AZ commercial permits.")) is None
    assert sem.description_state(_subject(LONG)) is not None


def test_a_model_with_only_comments_is_not_judged():
    """*** AND THIS IS THE CASE THE OLD VERSION EXISTED FOR. ***

    It accepted a model with no description at all, on the strength of its comment block, and then
    reported the result as a description finding. There is no description to contradict.
    """
    s = _subject("", comments="-- a long and detailed comment block about what this model does")
    assert sem.description_state(s) is None


def test_the_threshold_is_named_rather_than_inlined():
    assert sem.MIN_DESCRIPTION_WORDS == 10
    st = sem.description_state(_subject(" ".join(["word"] * sem.MIN_DESCRIPTION_WORDS)))
    assert st is not None, "a description exactly at the floor must be judged"
    st = sem.description_state(_subject(" ".join(["word"] * (sem.MIN_DESCRIPTION_WORDS - 1))))
    assert st is None


def test_the_version_records_that_the_state_changed():
    """*** A VERDICT IS EVIDENCE ABOUT A QUESTION AND THE STATE IT WAS GIVEN. ***

    Dropping the comment block changes the second, so every verdict recorded under `+comments` is
    about a different question from this one. `effectiveness` reports agreement per version and
    would otherwise pool the two.
    """
    assert "comments" not in sem.DESC_VERSION, sem.DESC_VERSION
    assert "description_only" in sem.DESC_VERSION


def test_the_finding_no_longer_claims_the_contradiction_might_be_elsewhere():
    """It named the comment block as also-sent, which is what made the finding unactionable."""
    import inspect

    from dbt_assay import judged
    src = inspect.getsource(judged.description_contradicts_the_code)
    assert "the contradiction is in one of these" not in src
    assert "and_the_models_own_comment_block" not in inspect.getsource(judged._prose_judged)
