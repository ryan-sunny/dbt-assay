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


# ------------------------------------------------------------------- the context sections

def _cfg(tmp_path, body: str):
    from dbt_assay.config import Config
    (tmp_path / "audit.yml").write_text(body)
    return Config.load(tmp_path)


def test_the_form_carries_their_words_with_what_assay_measured(project_dir, tmp_path):
    """*** THE TOOL FORBIDS THE AGENT FROM WRITING A `means:` AND GAVE THE PERSON NOWHERE. ***

    `suggestions()` returns every `means:` empty and the skill says to leave it empty, because a
    definition written from a model name looks exactly like one somebody chose and then rides
    along with every judged question forever. The form is the one surface where a person is
    already typing sentences about their own warehouse.
    """
    from dbt_assay import reviewform
    from dbt_assay.manifest import Project
    cfg = _cfg(tmp_path, 'vocab:\n  stg_bad_notnull:\n    means: "a staging model"\n')
    ctx = reviewform.context(None, Project.load(project_dir), cfg, [])
    w = next(x for x in ctx["words"] if x["term"] == "stg_bad_notnull")
    assert w["means"] == "a staging model"
    assert w["used_by"]["models"] >= 1, "assay did not measure where the word is used"
    assert w["used_by"]["of"] > 1


def test_a_suggestion_that_matches_nothing_is_never_offered(project_dir, tmp_path):
    """*** THE PLACEHOLDER READS LIKE A SELECTOR. ***

    `asserts_law_everywhere` ends with `applies_to: "path:models/..."` -- a literal ellipsis. The
    first version of the form lifted that out and put it on an accept button, so one click would
    have scoped the term to a path matching nothing while reading as configured. Third time this
    shape appeared in two days.
    """
    from dbt_assay import reviewform
    from dbt_assay.manifest import Project
    cfg = _cfg(tmp_path, 'vocab:\n  t:\n    means: "a Colorado water court region"\n')
    ctx = reviewform.context(None, Project.load(project_dir), cfg, [])
    w = next(x for x in ctx["words"] if x["term"] == "t")
    assert any(i["rule"] == "asserts_law_everywhere" for i in w["issues"])
    assert w["suggested"] == "", "offered a suggestion built from the placeholder"


def test_the_page_renders_the_context_and_stays_one_file(project_dir, tmp_path):
    from dbt_assay import reviewform
    from dbt_assay.manifest import Project
    cfg = _cfg(tmp_path, 'vocab:\n  t:\n    means: "x"\n')
    ctx = reviewform.context(None, Project.load(project_dir), cfg, [])
    page = reviewform.form_html([], {}, "p", "now", "0.0", ctx)
    for needed in ("data-pane=\"words\"", "wordsTab", "explanationsTab", "waiversTab",
                   "handback.json"):
        assert needed in page, needed
    assert "fetch(" not in page and "<script src" not in page, "the form reached the network"


# ------------------------------------------------------------------- the handback

def test_config_changes_come_back_as_proposals():
    from dbt_assay import reviewform
    changes, bad = reviewform.load_config({"config": [
        {"path": ["vocab", "wdid", "means"], "value": "a structure id"},
        {"path": ["vocab", "wdid", "applies_to"], "value": "path:models/water"}]})
    assert not bad
    assert [c.dotted for c in changes] == ["vocab.wdid.applies_to", "vocab.wdid.means"]


def test_a_cleared_box_is_not_a_deletion():
    """Removing a word has project-wide reach and is not a decision a blank text box makes."""
    from dbt_assay import reviewform
    changes, bad = reviewform.load_config({"config": [
        {"path": ["vocab", "wdid", "means"], "value": ""},
        {"path": ["vocab", "wdid", "implies"], "value": None}]})
    assert changes == [] and bad == []


def test_the_form_writes_an_ALLOW_LIST_and_nothing_else():
    """*** A DOWNLOADED FILE NAMING AN ARBITRARY CONFIG PATH IS A HOLE. ***

    And the failure is silent: the value lands in somebody's audit.yml under a key nothing reads.
    The list grew -- the settings tab writes the gating floors, the row-loss threshold, the spend
    cap and the rate card, because those end up in the same committed file either way -- but it
    is still a list. A probability expression is not on it: `act:` wants the measured agreement
    rate in front of you, and `assay effectiveness` is that surface.
    """
    from dbt_assay import reviewform
    changes, bad = reviewform.load_config({"config": [
        {"path": ["questions", "grain_unresolved", "act"], "value": "p > 0.9"},
        {"path": ["nonsense", "anything"], "value": 1}]})
    assert changes == []
    assert len(bad) == 2 and all("does not write this key" in b for b in bad)


def test_a_setting_out_of_range_is_refused_here_rather_than_breaking_the_next_run():
    """*** `Config.from_dict` RAISES ON A BAD FLOOR. ***

    An unvalidated box would write a file that refuses to load -- discovered later, by somebody
    who did not type it. The message says what the number MEANS rather than quoting a bound.
    """
    from dbt_assay import reviewform
    changes, bad = reviewform.load_config({"config": [
        {"path": ["gating", "min_agreement"], "value": 90},
        {"path": ["completeness", "row_loss_threshold"], "value": 1.0},
        {"path": ["jev", "max_spend_usd"], "value": -1},
        {"path": ["cost", "usd_per_tb_scanned"], "value": "six dollars"}]})
    assert changes == []
    assert len(bad) == 4, bad
    assert "not a percentage" in bad[0]
    assert "strictly between 0 and 1" in bad[1]


def test_a_number_typed_into_a_text_box_is_written_as_a_number():
    """`"20"` out of a text input is the integer 20 in a YAML file, not a quoted string."""
    from dbt_assay import reviewform
    changes, bad = reviewform.load_config({"config": [
        {"path": ["gating", "min_adjudications"], "value": "20"},
        {"path": ["gating", "min_agreement"], "value": "0.7"},
        {"path": ["cost", "engine"], "value": "bigquery"}]})
    assert not bad, bad
    got = {c.dotted: c.value for c in changes}
    assert got["gating.min_adjudications"] == 20 and isinstance(got["gating.min_adjudications"], int)
    assert got["gating.min_agreement"] == 0.7
    assert got["cost.engine"] == "bigquery"


def test_every_setting_the_form_offers_is_one_it_can_write():
    """*** THE TAB AND THE LOADER ARE TWO LISTS THAT MUST NOT DRIFT. ***
    A box the form renders and the loader rejects is a person typing into nothing."""
    from dbt_assay import reviewform
    # A value inside every one of these ranges: the point is that the PATH is accepted, not that
    # any particular number is.
    for path, _key, kind, _shipped, _what, _why in reviewform.SETTINGS:
        probe = "0.5" if kind == "number" else "x"
        changes, bad = reviewform.load_config(
            {"config": [{"path": path.split("."), "value": probe}]})
        assert not bad, f"the tab offers `{path}` and the loader refuses it: {bad}"
        assert len(changes) == 1


def test_an_unnamed_new_option_is_refused_by_name():
    from dbt_assay import reviewform
    changes, bad = reviewform.load_config({"config": [
        {"path": ["explanations", "water_rights", "__new"], "value": "something"}]})
    assert changes == []
    assert bad and "give the new option a name" in bad[0]


def test_a_handback_with_no_config_still_loads_its_verdicts():
    """Every form written before this existed must still load."""
    from dbt_assay import reviewform
    rows, bad = reviewform.load({"verdicts": [
        {"subject": "model.p.a", "question": "q", "verdict": "agree"}]})
    assert len(rows) == 1 and not bad
    assert reviewform.load_config({"verdicts": []}) == ([], [])


def test_the_pagination_belongs_to_the_pane_you_are_looking_at():
    """*** `page 1 of 12 · 2 of 235 answered` SAT OVER THE WORDS TAB. ***

    That bar was the FINDINGS pagination, rendered above a pane showing all 36 of its rows in one
    scroll. Two wrong things at once: the controls did nothing where they were, and the counts
    described something the reader was not looking at.
    """
    from dbt_assay import reviewform
    js = reviewform._JS
    assert "const PAGES" in js, "paging state is not per pane"
    assert "function pageOf(" in js and "paneItems(" in js
    # every pane slices its own items
    for pane in ("'words'", "'explanations'", "'waivers'", "'findings'"):
        assert f"pageOf({pane})" in js, f"{pane} does not page itself"
    # and the single global page is gone
    for gone in ("let page = 0", "page * PER", "page++", "page--"):
        assert gone not in js, f"the global pager survived: {gone}"


def test_a_pane_that_fits_on_one_page_hides_the_controls():
    """Disabled controls over a ten-row pane still say "there is more"; hidden ones do not."""
    from dbt_assay import reviewform
    js = reviewform._JS
    assert "single ? 'none'" in js, "a one-page pane still shows the pager"


def test_the_counter_counts_what_the_pane_holds():
    from dbt_assay import reviewform
    js = reviewform._JS
    assert "pane === 'findings'" in js, "the counter does not switch with the pane"
    assert "box(es) filled" in js, "a non-findings pane still reports verdicts"
