"""The form: ruling stops costing a turn each.

*** ONE TURN PER FINDING IS 159 TURNS, AND NOBODY DOES 159 TURNS. ***
An agent walking findings one at a time is the right shape for a CALL and the wrong shape for a
project. So the reading batches and the answering leaves the conversation: a single file, filled in
whenever there is ten minutes, handed back as JSON.
"""
from types import SimpleNamespace

import pytest

from dbt_assay import reviewform
from dbt_assay.store import Store


def _f(subject, check, fid, marts=1, summary="s", detail="d", claim="", file="models/a.sql"):
    return SimpleNamespace(subject=subject, check=check, id=fid, subject_name=subject.split(".")[-1],
                           file=file, summary=summary, detail=detail, marts=marts, descendants=marts,
                           evidence={"claim": claim} if claim else {})


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "s.duckdb"))


def _human(s, subject, question, verdict="agree"):
    s.adjudicate(subject, question, question.split("__")[0], "", verdict, "", "n", "someone")


# --------------------------------------------------------------- one card per (subject, question)

def test_one_card_per_subject_and_question_not_per_finding(store, tmp_path):
    """A verdict covers the pair, so cards per finding ask the same question twice.

    On the field warehouse 260 findings are 212 pairs: 48 would have been asked again, and the
    store would have kept both answers.
    """
    fs = [_f("model.p.a", "check_one", "f1"), _f("model.p.a", "check_one", "f2"),
          _f("model.p.a", "check_two", "f3")]
    cards, _sql = reviewform.cards(fs, store, tmp_path)
    assert len(cards) == 2, [c["key"] for c in cards]
    one = next(c for c in cards if c["question"] == "check_one")
    assert len(one["findings"]) == 2, "both findings must ride on the one card"


def test_a_verdict_hides_only_that_pair_never_the_whole_model(store, tmp_path):
    """*** AND IT HID THEM BY REMOVING THE CARD, WHICH IS WORSE THAN SHOWING IT WRONG. ***

    The first version also skipped on `ruled_subjects()`, which is subject-level. Ruling
    `code_contradicts_a_claim` on a model therefore dropped every OTHER check on it from the form
    whose entire job is showing what nobody has answered. Measured on the field warehouse: four
    verdicts turned 212 cards into 206 instead of 208.
    """
    fs = [_f("model.p.a", "check_one", "f1"), _f("model.p.a", "check_two", "f2"),
          _f("model.p.b", "check_one", "f3")]
    _human(store, "model.p.a", "check_one")
    cards, _sql = reviewform.cards(fs, store, tmp_path)
    keys = {c["key"] for c in cards}
    assert "model.p.a::check_one" not in keys, "an answered pair came back"
    assert "model.p.a::check_two" in keys, "a different check on that model vanished unanswered"
    assert "model.p.b::check_one" in keys


def test_the_order_is_total_so_two_emits_agree(store, tmp_path):
    """Highest blast radius first; ties broken by key, never by whatever order came out."""
    fs = [_f("model.p.a", "c", "f1", marts=3), _f("model.p.b", "c", "f2", marts=9),
          _f("model.p.c", "c", "f3", marts=3)]
    a = [c["key"] for c in reviewform.cards(fs, store, tmp_path)[0]]
    b = [c["key"] for c in reviewform.cards(fs, store, tmp_path)[0]]
    assert a == b
    assert a[0] == "model.p.b::c", a


# --------------------------------------------------------------- the agent's reading

def test_a_model_level_agent_ruling_says_it_may_be_about_something_else(store, tmp_path):
    """*** THIS IS HOW SOMEBODY CONFIRMS A READING NOBODY DID. ***

    An agent ruling is stored per model and lands on every finding that model has, so the same
    note appears under a question it never addressed. Presented flat, it reads as an answer.
    """
    store.adjudicate("model.p.a", "check_one", "check_one", "", "disagree", "",
                     "because of the union", "agent", source="agent")
    fs = [_f("model.p.a", "check_two", "f1")]
    cards, _sql = reviewform.cards(fs, store, tmp_path)
    a = cards[0]["agent"]
    assert a, "the agent's reading was dropped"
    assert "different finding" in a["scope"], a["scope"]


def test_a_card_with_no_reading_says_so_rather_than_showing_nothing(store, tmp_path):
    """A blank panel is the same shape as a reading that said nothing interesting."""
    cards, _sql = reviewform.cards([_f("model.p.a", "c", "f1")], store, tmp_path)
    assert cards[0]["agent"] is None
    html = reviewform.form_html(cards, {}, "p", "x", "0")
    assert reviewform.NO_READ in html


def test_reads_prefill_my_read(store, tmp_path):
    """The expensive part is an agent reading the SQL. It batches; the answering does not."""
    fs = [_f("model.p.a", "c", "f1")]
    reads = {"model.p.a::c": {"verdict": "disagree", "why": "the claim is exactly true"}}
    cards, _sql = reviewform.cards(fs, store, tmp_path, reads)
    assert cards[0]["read"] == {"verdict": "disagree", "why": "the claim is exactly true"}


# --------------------------------------------------------------- nothing unanswered is recorded

def test_a_card_with_no_verdict_is_never_recorded():
    """*** THE WHOLE PREMISE OF A `human` ROW IS THAT A PERSON ANSWERED IT. ***

    A default, or an inference from a note being present, makes that false -- and it makes it
    false invisibly, in the one table a build is allowed to gate on.
    """
    rows, bad = reviewform.load({"verdicts": [
        {"subject": "model.p.a", "question": "c", "verdict": "agree"},
        {"subject": "model.p.b", "question": "c", "verdict": "", "note": "scrolled past"},
        {"subject": "model.p.c", "question": "c", "note": "typed a note, never answered"},
    ]})
    assert [r["subject"] for r in rows] == ["model.p.a"]
    assert len(bad) == 2, bad


def test_an_invented_verdict_is_refused_not_coerced():
    rows, bad = reviewform.load({"verdicts": [
        {"subject": "model.p.a", "question": "c", "verdict": "maybe"},
        {"subject": "model.p.b", "question": "c", "verdict": "AGREE"},
    ]})
    assert [r["subject"] for r in rows] == ["model.p.b"], "case should normalize, `maybe` should not"
    assert len(bad) == 1


def test_a_file_that_is_not_ours_is_reported_not_parsed():
    rows, bad = reviewform.load({"something": "else"})
    assert not rows and bad


# --------------------------------------------------------------- the page

def test_a_seed_is_excerpted_and_says_that_it_was(store, tmp_path):
    """*** A SEED'S "CODE" IS ITS DATA, AND ONE OF THEM WAS 4.9 MB. ***

    Two seed CSVs made the form 7.8 MB for 212 cards -- 91% of the page was rows nobody would
    scroll. Capped, and the cap is stated: a file that ends early without saying so looks exactly
    like a file that is really that short.
    """
    csv = "\n".join(["a,b"] + [f"{i},{i}" for i in range(500)])
    (tmp_path / "seeds").mkdir()
    (tmp_path / "seeds" / "s.csv").write_text(csv)
    fs = [_f("model.p.a", "seed_reaches_nothing", "f1", file="seeds/s.csv")]
    _cards, sql = reviewform.cards(fs, store, tmp_path)
    body = sql["seeds/s.csv"]
    assert len(body) < len(csv) / 4
    assert "of 500 rows" in body, body[-120:]


def test_the_page_cannot_be_ended_by_a_model_that_contains_a_script_tag(store, tmp_path):
    """A dbt model with `</script>` in a comment would end the tag and truncate the file."""
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "a.sql").write_text("-- </script><!-- oops\nselect 1")
    cards, sql = reviewform.cards([_f("model.p.a", "c", "f1")], store, tmp_path)
    html = reviewform.form_html(cards, sql, "p", "x", "0")
    body = html.split('<script id="assay-form"', 1)[1].split("</script>", 1)[0]
    assert "</script>" not in body and "<!--" not in body


def test_the_page_reaches_out_to_nothing():
    """Self-contained is the delivery model. On `file://` a blocked request fails silently."""
    from dbt_assay import reviewform as rf
    html = rf.form_html([], {}, "p", "x", "0")
    for bad in ("http://", "https://", "<img", "fetch(", "XMLHttpRequest"):
        assert bad not in html, bad


def test_the_page_carries_no_wall_clock(store, tmp_path):
    """Two emits over one store must be byte-identical, or it cannot be committed or diffed."""
    fs = [_f("model.p.a", "c", "f1")]
    a = reviewform.form_html(*reviewform.cards(fs, store, tmp_path), "p", "gen", "0")
    b = reviewform.form_html(*reviewform.cards(fs, store, tmp_path), "p", "gen", "0")
    assert a == b


def test_every_css_variable_the_form_uses_is_defined():
    """An undefined `var()` drops the declaration silently, and the element looks deliberate."""
    import re

    from dbt_assay import reviewform as rf
    root = rf._CSS[rf._CSS.index(":root{"):rf._CSS.index(":root{") + 400]
    defined = set(re.findall(r"--([a-z-]+)\s*:", root))
    used = set(re.findall(r"var\(--([a-z-]+)\)", rf._CSS))
    assert defined and used
    assert not (used - defined), sorted(used - defined)
