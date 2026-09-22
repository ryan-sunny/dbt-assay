"""A finding a person has read and called wrong does not come back.

*** WITHOUT THIS THE REVIEW LOOP DOES NOT COMPOUND. ***
Measured on the field warehouse: record a human `disagree`, run `assay check` again, and the count
is unchanged — 115 before, 115 after. The only thing that ever removed a finding was a
hand-written waiver in `audit.yml`. So reading 115 findings and ruling every one of them wrong
bought nothing, and tomorrow you are handed the same 115.

That is the whole premise of reviewing. A person disposes of a flag ONCE and it stays disposed, or
the flag is a tax rather than a question.
"""
import pytest

from dbt_assay.checks.structural import Finding
from dbt_assay.config import Config
from dbt_assay.judged import apply_policy
from dbt_assay.store import Store


def _f(summary="the code contradicts a claim", check="code_contradicts_a_claim", **kw):
    return Finding(check=check, subject="model.p.a", subject_name="a", file="a.sql",
                   summary=summary, detail="why", base=2, **kw)


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "s.duckdb"))


def _rule(s, f, verdict="disagree", source="human", who="Ryan", exact=True):
    subj = f"{f.subject}::finding::{f.id}" if exact else f.subject
    s.adjudicate(subj, f.check, f.check, "", verdict, "", "line 23 says exactly that", who,
                 source=source)


def test_a_person_calling_a_finding_wrong_removes_it(store):
    f = _f()
    kept, _w = apply_policy([f], Config(), store, None)
    assert len(kept) == 1, "the fixture produced nothing; this test is not measuring anything"
    _rule(store, f)
    kept, waived = apply_policy([f], Config(), store, None)
    assert not kept, "a finding ruled WRONG by a person came back"
    assert len(waived) == 1


def test_it_says_who_dismissed_it_and_why(store):
    """*** SILENTLY DROPPING IT IS THE SAME SHAPE AS THE DEFECT THIS TOOL REPORTS. ***

    A finding that vanishes with no reason is indistinguishable from a check that stopped
    looking. The reason is already written — it is the note on the ruling.
    """
    f = _f()
    _rule(store, f)
    _kept, waived = apply_policy([f], Config(), store, None)
    why = waived[0][1]
    assert "dismissed by Ryan" in why, why
    assert "line 23" in why, "the reason somebody wrote was dropped"


def test_agreeing_with_a_finding_does_not_remove_it(store):
    """`agree` means the finding is RIGHT. Removing it would delete the real ones."""
    f = _f()
    _rule(store, f, verdict="agree")
    kept, _w = apply_policy([f], Config(), store, None)
    assert kept, "agreeing that a finding is correct made it disappear"


def test_unclear_does_not_remove_it(store):
    """`unclear` is evidence about the QUESTION, not a verdict on the model.

    Disagreement means the criteria are wrong; unclear means the state does not carry what the
    question asks. Treating the second as a dismissal would hide a finding nobody judged.
    """
    f = _f()
    _rule(store, f, verdict="unclear")
    kept, _w = apply_policy([f], Config(), store, None)
    assert kept


def test_an_agents_ruling_does_not_remove_it(store):
    """*** AN AGENT MUST NOT BE ABLE TO CLEAR A BUILD. ***

    An agent ruling triages what a person should read first. If it could dismiss, an agent could
    silence every finding on a project by reading none of them carefully.
    """
    f = _f()
    _rule(store, f, source="agent", who="agent")
    kept, _w = apply_policy([f], Config(), store, None)
    assert kept, "an agent's ruling removed a finding"


def test_a_model_level_ruling_does_not_clear_every_finding_on_that_model(store):
    """A verdict on a MODEL lands on every finding that model has, and one model carries eight.

    `az_section_summary` has eight `test_cannot_fail` findings and `dim_business` was ruled a
    union false positive while two of its six edges really did fan out 1.48x. Model-level
    rulings are real triage and they are too coarse to delete evidence with.
    """
    a, b = _f(summary="finding one"), _f(summary="finding two")
    assert a.id != b.id
    _rule(store, a, exact=False)                      # ruled on the MODEL, not the finding
    kept, _w = apply_policy([a, b], Config(), store, None)
    assert len(kept) == 2, "a model-level ruling deleted findings nobody read"


def test_the_dismissal_lapses_when_the_finding_changes(store):
    """*** THIS IS WHY IT IS KEYED ON THE FINDING AND NOT ON (SUBJECT, QUESTION). ***

    The id hashes the check, the subject, the summary and the non-measured evidence, so it
    survives a rerun and changes when the substance changes. Edit the model into a genuinely
    different defect and the dismissal does not follow it — the guarantee a waiver needs an
    expiry date to approximate, for free.
    """
    old = _f(summary="the claim about CLOSED rows")
    _rule(store, old)
    assert not apply_policy([old], Config(), store, None)[0]

    new = _f(summary="the claim about the date format")      # the code moved; a different defect
    assert new.id != old.id
    kept, _w = apply_policy([new], Config(), store, None)
    assert kept, "a stale dismissal silenced a finding it was never about"


def test_no_store_means_nothing_is_dismissed(store):
    """A missing store is not a clean bill. Every finding stands."""
    kept, _w = apply_policy([_f()], Config(), None, None)
    assert kept


def test_the_same_finding_is_never_reported_twice(store):
    """*** `water_reach_screen` REPORTED ONE FINDING THREE TIMES. ***

    Identical id, identical evidence, because the same window appears more than once in the
    compiled SQL and the check walks each occurrence. 4 duplicate rows of 258 on the field
    warehouse, which inflates the headline number, the per-check breakdown, and the review form --
    where a card drew the same sentence three times.

    A finding IS its id, so two rows carrying one id are one finding by this project's own
    definition.
    """
    from dbt_assay.live import _distinct
    a, b = _f(), _f()
    assert a.id == b.id, "the fixture does not produce a duplicate"
    assert len(_distinct([a, b])) == 1


def test_two_findings_of_equal_weight_come_back_in_one_order(store):
    """Sorting by weight alone left ties to the order they happened to be appended.

    That is `arbitrary_pick`, the defect this tool reports in other people's SQL, in the list it
    reports it from.
    """
    from dbt_assay.live import _distinct
    x, y = _f(summary="one"), _f(summary="two")
    assert x.weight == y.weight
    assert [f.id for f in _distinct([x, y])] == [f.id for f in _distinct([y, x])]
