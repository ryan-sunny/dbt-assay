"""A custom question could be asked and could never become a finding.

*** THE TOOL DEMANDED THE FIELD THAT WOULD HAVE FIXED IT. ***
`finding_when:` names the answers that are defects. `lint.py` makes omitting it an ERROR --
"asked, paid for, stored, and produces no finding" -- `assay ask` reads it, and `judged.py` did
not. So an author was told to declare it, declared it, and the error message stayed true in
`check` whatever they wrote.

Measured in the field: two custom questions, 42 and 21 answers up to p=1.00, and zero findings in
any run. Zero of seventeen shipped families declare `finding_when`, because they have hand-written
functions instead, which is why nobody noticed.
"""
import pathlib
from types import SimpleNamespace

import pytest

from dbt_assay import judged


def _entry(name="a", answer="passes_it_through_unchecked", p=0.97, prefix="water.sect"):
    return SimpleNamespace(
        uid=f"model.p.{name}", name=name, path=f"{name}.sql", descendants=3, marts=2,
        judged={prefix: {"answer": answer, "probabilities": {answer: p}, "context": name}})


@pytest.fixture
def custom(monkeypatch):
    """A family declared the way `assay_questions/*.yml` declares one."""
    from dbt_assay import contracts
    q = {"section_id_assumed_to_resolve": {
        "id_prefix": "water.sect", "subject": "model", "type": "choice",
        "finding_when": ["passes_it_through_unchecked"],
        "criteria": {"passes_it_through_unchecked": "hands a section_id downstream unchecked",
                     "guards_it": "checks the section resolves"},
        "instructions": {"question": "Does this model hand a section_id downstream unchecked?"}}}
    monkeypatch.setattr(contracts, "QUESTIONS", q)
    return q


def test_a_declared_family_produces_a_finding(custom):
    got = judged.declared_findings(None, [_entry()])
    assert len(got) == 1
    f = got[0]
    assert f.check == "section_id_assumed_to_resolve"
    assert f.evidence["answer"] == "passes_it_through_unchecked"
    assert f.evidence["probability"] == 0.97


def test_only_the_answers_it_names_are_findings(custom):
    """*** THE POINT OF `finding_when` IS THAT MOST ANSWERS ARE NOT DEFECTS. ***

    The field example says so itself: a model with no section_id is the common case, and making
    it a finding would bury the one that matters under 300 that do not.
    """
    assert not judged.declared_findings(None, [_entry(answer="guards_it")])


def test_a_family_with_no_finding_when_produces_nothing(monkeypatch):
    """Storing an answer is not the same as claiming it is wrong."""
    from dbt_assay import contracts
    monkeypatch.setattr(contracts, "QUESTIONS", {
        "just_curious": {"id_prefix": "water.sect", "criteria": {"x": "y"}}})
    assert not judged.declared_findings(None, [_entry(answer="x")])


def test_it_rests_on_itself_so_the_gate_floor_applies(custom):
    """*** WITHOUT THIS A CUSTOM FAMILY READS AS STRUCTURAL. ***

    An empty `rests_on` means a parser decided it and it may gate immediately -- the opposite of
    true for the one kind of check nobody has ever measured. `apply_policy` refuses `fail` for a
    judged question under `min_adjudications`, and it can only do that if the finding says which
    question it rests on.
    """
    f = judged.declared_findings(None, [_entry()])[0]
    assert f.rests_on == "section_id_assumed_to_resolve"


def test_one_model_carrying_several_answers_of_one_family(custom):
    """The store uniquifies a repeated question id as `<prefix>__N`. Both belong to the family."""
    e = _entry()
    e.judged["water.sect__1"] = {"answer": "passes_it_through_unchecked",
                                 "probabilities": {"passes_it_through_unchecked": 0.8},
                                 "context": "second"}
    assert len(judged.declared_findings(None, [e])) == 2


def test_a_different_family_s_answers_are_not_claimed(custom):
    """`water.sect` must not match `water.sections` or any other prefix that starts the same.

    This is the `align` / `align__N` collision that already cost this project once: a prefix test
    that matched a neighbouring family's answers, and only a filter on the answer value hid it.
    """
    e = _entry()
    e.judged["water.sectional"] = {"answer": "passes_it_through_unchecked",
                                   "probabilities": {"passes_it_through_unchecked": 0.9}}
    got = judged.declared_findings(None, [e])
    assert len(got) == 1, [g.evidence for g in got]


def test_the_summary_is_never_invented(custom):
    """A family whose YAML cannot produce a sentence is a lint problem at authoring time.

    Not a finding with a generated headline at check time -- the fallback names the family and
    the answer and claims nothing else.
    """
    custom["section_id_assumed_to_resolve"]["criteria"] = {}
    f = judged.declared_findings(None, [_entry()])[0]
    assert "section_id_assumed_to_resolve" in f.summary
    assert "passes_it_through_unchecked" in f.summary


def test_ask_and_check_agree_about_what_a_finding_is(custom):
    """*** TWO SPELLINGS OF ONE RULE IS HOW THIS HAPPENED. ***

    `assay ask` decided a hit with `a["answer"] in want`, inline, and nothing else in the tool
    shared that logic. They must not drift again: whatever `ask` would print, `check` must find.
    """
    # Read the file rather than importing it: `cli` pulls in modules that resolve shipped
    # question ids at import time, and this test has replaced the question bank.
    import dbt_assay
    src = (pathlib.Path(dbt_assay.__file__).parent / "cli.py").read_text()
    assert 'want = q.get("finding_when")' in src, "the ask path changed; re-check this pairing"
    for answer, expected in (("passes_it_through_unchecked", 1), ("guards_it", 0)):
        want = custom["section_id_assumed_to_resolve"]["finding_when"]
        ask_would_flag = answer in want
        check_finds = len(judged.declared_findings(None, [_entry(answer=answer)]))
        assert ask_would_flag == bool(check_finds) == bool(expected), answer


def test_the_entry_keeps_answers_no_named_field_claimed():
    """`_judgments` loaded every stored answer and the loop kept only what shipped families named.

    Everything else was read out of the store and dropped on the floor, which is why a custom
    family was invisible to everything except the command that asked it.
    """
    import dataclasses

    from dbt_assay.inventory import ModelEntry
    assert any(f.name == "judged" for f in dataclasses.fields(ModelEntry))


def test_no_family_is_both_hand_written_and_declared():
    """*** TWO PRODUCERS FOR ONE FAMILY IS TWO FINDINGS FOR ONE DEFECT. ***

    `CHECKS` stays for families whose finding needs more than the answer -- a claim's text, a
    hop's collapse note. `declared_findings` covers everything that declares its own defect
    answers. A family doing both emits it twice, with different summaries, so the ids differ and
    the dedupe in `live._distinct` cannot collapse them.
    """
    import inspect

    from dbt_assay import judged
    from dbt_assay.contracts import QUESTIONS
    hand = {fn.__name__ for fn in judged.CHECKS}
    hand |= {"grain_contradicts_declared_key", "identifier_outside_the_grain"}
    declared = {k for k, v in QUESTIONS.items() if (v or {}).get("finding_when")}
    both = sorted(hand & declared)
    assert not both, (
        f"{both} would produce a finding twice. Either drop its `finding_when:` or remove its "
        f"hand-written function.")
    # and the hand-written ones really are functions in this module
    for n in hand:
        assert hasattr(judged, n), n
    assert inspect.isfunction(judged.declared_findings)


def test_a_criterion_is_read_whether_it_is_a_string_or_a_dict(custom):
    """*** THE FIXTURE USED THE SHAPE THAT DOES NOT SHIP. ***

    Every real bank writes `{answer: {what: "...", examples: [...]}}`. The test written alongside
    this code used `{answer: "..."}`, passed, and the first run against a real question bank
    raised `'dict' object has no attribute 'strip'`. A test whose fixture is not the shape the
    code will meet is a test that measures the author's assumption.
    """
    custom["section_id_assumed_to_resolve"]["criteria"] = {
        "passes_it_through_unchecked": {
            "what": "hands a section_id downstream without checking it resolves",
            "examples": ["selects section_id and never joins the section relation"]}}
    f = judged.declared_findings(None, [_entry()])[0]
    assert "hands a section_id downstream" in f.summary
    assert "examples" not in f.summary


def test_the_detail_names_values_and_the_answer_never_the_template(monkeypatch):
    """*** "`model` HAS `marts_downstream` MARTS READING IT" WAS THE WHOLE DETAIL. ***

    The bank's question, with the state's field names where the values belong, and nothing
    saying what the answer was. Reported from the page. The detail now says what was asked about
    which model, what came back and at what probability, and why that answer is a finding; the
    question appears only once its fields are values.
    """
    from dbt_assay import contracts
    monkeypatch.setattr(contracts, "QUESTIONS", {"monitor_covers_what_matters": {
        "id_prefix": "mcov", "finding_when": ["worth_watching"],
        "criteria": {"worth_watching": {"what": "Its row count can change for reasons outside "
                     "this project, and the marts would be wrong without anything failing."}},
        "instructions": {"question": "`model` has `marts_downstream` marts reading it and no "
                                     "volume monitor. Is it worth watching?"}}})
    e = _entry(name="stg_cdss_structures", answer="worth_watching", p=0.7, prefix="mcov")
    e.marts, e.descendants = 20, 30
    f = judged.declared_findings(None, [e])[0]
    assert "`marts_downstream`" not in f.detail and "`model` has" not in f.detail, f.detail
    assert "`stg_cdss_structures` has 20 marts reading it" in f.detail
    # N4: the answer and how sure are the evidence, shown as a block; the detail is the reason
    assert f.evidence["answer"] == "worth_watching" and f.evidence["probability"] == 0.7
    assert "was asked about" not in f.detail, "the detail restates the question again"
    assert f.detail.startswith("Its row count can change")
    # and the summary is the whole criterion, not 110 characters of it
    assert f.summary.endswith("without anything failing."), f.summary


def test_a_field_the_finding_cannot_fill_keeps_the_question_out(monkeypatch):
    """A question with a field name left in it is the thing that was reported, so it is omitted
    rather than shown half-filled. What was asked about is used for ONE unknown field."""
    from dbt_assay import contracts
    q = {"test_never_ran": {"id_prefix": "tnvr", "finding_when": ["gap"],
                            "criteria": {"gap": "a hole"},
                            "instructions": {"question": "`test` is declared on `model`. Gap?"}}}
    monkeypatch.setattr(contracts, "QUESTIONS", q)
    e = _entry(name="m", answer="gap", prefix="tnvr")
    e.judged["tnvr"]["context"] = "not_null_m_id"
    f = judged.declared_findings(None, [e])[0]
    assert "`not_null_m_id` is declared on `m`" in f.detail, f.detail

    q["test_never_ran"]["instructions"]["question"] = "`test` on `model` via `monitor`?"
    f = judged.declared_findings(None, [e])[0]
    assert "What was asked" not in f.detail and "`monitor`" not in f.detail, f.detail
