"""*** A MODEL DESCRIPTION IS NOT ONE CLAIM, AND JUDGING IT AS ONE PRODUCES A COIN FLIP. ***

Measured on a real model. "Boulder commercial building permits, residential filtered out" put to a
single choice split 0.51 supports / 0.47 contradicts and flipped between runs, because one half is
true and the other is not. Split into atomic claims, the sharpest read `contradicts` at 0.82.

Four rounds of measurement got the contradiction count on one slice from 10 to 5, and every step
was a fix to how the question was ASKED rather than to the model:

| change                                        | contradicts | says_nothing | supports |
|-----------------------------------------------|-------------|--------------|----------|
| generic evidence, compound claims              | 10          | 10           | 6        |
| split on semicolons too                        | 10          | 10           | 6        |
| evidence chosen BY the claim                   | 8           | 8            | 10       |
| criteria: absence is not disagreement          | 5           | 14           | 7        |
"""
from dbt_assay import claims as C


def test_a_semicolon_joins_two_claims_as_surely_as_a_full_stop():
    """This exact sentence read `contradicts` at 0.92 while joined, and neither half is."""
    text = ("d_class_cn is the readable class ('VACANT LAND'); d_class and prop_class are "
            "numeric codes that would render as digits.")
    got = C.sentences(text)
    assert len(got) == 2, got
    assert got[0].endswith(";")
    assert "numeric codes" in got[1]


def test_a_claim_id_survives_its_neighbors_being_reworded():
    """A verdict hangs off this id. If it moved when the paragraph reflowed, every ruling would be
    orphaned by an unrelated edit."""
    a = C.claim_id("model.p.m", "One row per section.")
    b = C.claim_id("model.p.m", "one row   per   section.")     # whitespace and case
    assert a == b
    assert a != C.claim_id("model.p.other", "One row per section.")
    assert a != C.claim_id("model.p.m", "One row per section and year.")


def test_a_citation_pattern_must_be_anchored_on_its_authority_marker():
    """*** MEASURED: UNANCHORED, THIS FOUND 46 'CITATIONS' IN ONE PROJECT, ALL OF THEM DATES. ***"""
    assert C.citation_in("derived per C.R.S. 37-92-302(1)(c) from the filing month")
    assert not C.citation_in("read 26-09-19, and the backfill ran 02-04-25")
    assert not C.citation_in("the window is 2024-01-01 to 2024-12-31")


def test_only_a_claim_is_checkable_and_the_rest_are_real_sentences_doing_other_jobs():
    assert "rationale" not in C.CHECKABLE
    assert "incident_or_history" not in C.CHECKABLE
    assert "instruction_to_maintainers" not in C.CHECKABLE
    assert set(C.CHECKABLE) == {"claim_about_output", "claim_about_a_rule"}


def test_the_identifiers_a_claim_names_are_what_its_evidence_is_chosen_from():
    """A claim about `d_class_cn` read `contradicts` at 0.97 purely because the evidence listed
    thirty other columns and not that one."""
    got = C.mentioned_identifiers("d_class_cn is readable; land_use carries it")
    assert "d_class_cn" in got
    assert "land_use" in got
    assert "readable" not in got          # a plain word is not a column


def test_comment_sentences_point_at_the_line_a_person_wrote_them_on():
    """A claim has to be traceable to where someone wrote it, or it cannot be audited."""
    sql = ("select 1\n"
           "-- One row per water division and case number in the resume.\n"
           "-- Filings with no issue date are dropped before this point.\n"
           "from t")
    got = C.comment_sentences(sql)
    assert len(got) == 2, got
    assert got[0][0].startswith("One row per water division")
    assert all(ln == 2 for _t, ln in got)      # the line the comment RUN began on


def test_a_fragment_too_short_to_be_a_claim_is_dropped_by_code():
    """`min_len` is a free filter. A model call spent on "see below." buys nothing."""
    assert C.sentences("see below.") == []
    assert C.sentences("One row per water division and case number.")


def test_every_question_this_module_uses_is_in_a_bank():
    from dbt_assay.contracts import check_question_ids, load_all_banks
    banks = load_all_banks()
    assert "claim_alignment" in banks
    assert "sentence_is_a_claim" in banks
    fake = [C.Claim("i", "s", "n", "t", "description")]
    check_question_ids(C.kind_questions(fake))
    check_question_ids(C.align_question())


def test_a_sentence_opening_with_a_bare_pronoun_keeps_its_antecedent():
    """*** FOUND BY RUNNING THIS METHOD ON assay's OWN SOURCE. ***

    "dbt reports that a test passed. It never reports that a test was INCAPABLE of failing."
    Split on the full stop, the second sentence reads as a claim about the FUNCTION rather than
    about dbt, and it was judged `contradicts` at 0.73. The subject is one sentence back.

    Splitting prose destroys antecedents, and a claim whose subject is elsewhere cannot be judged
    alone.
    """
    got = C.sentences("dbt reports that a test passed. It never reports that a test was "
                      "INCAPABLE of failing.")
    assert len(got) == 1, got
    assert "dbt reports" in got[0] and "INCAPABLE" in got[0]


def test_a_sentence_whose_pronoun_has_its_own_subject_still_stands_alone():
    """`This model ...` names what it is about. Only a BARE leading pronoun is the problem, and
    over-joining would undo the whole reason prose is split."""
    got = C.sentences("This model keeps one row per filing and section.")
    assert len(got) == 1
    two = C.sentences("One row per section and year. The legal descriptions carry no meridian.")
    assert len(two) == 2, two
