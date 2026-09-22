"""*** A TERM IS TRUE SOMEWHERE, AND THE WHOLE VOCABULARY WENT INTO EVERY STATE. ***

Measured on a real warehouse: 16 terms, six of them asserting one state's water law -- including a
statute citation -- sent to all 358 models. 4,997 of 19,707 judged answers, 25% of everything ever
paid for there, were about models in a different state and were told, as universal fact, that
prior appropriation decides who gets water.

`config.py`'s own comment already said why that is the worst place in the project to be wrong: a
vocab entry is injected into EVERY state, "which is why it improves answers to questions you never
wrote" -- and by the same mechanism steers every answer wrong at once.
"""
from __future__ import annotations

import pytest

from dbt_assay import states
from dbt_assay.config import Config, ThresholdError
from dbt_assay.lint import lint_vocab
from dbt_assay.manifest import Project

CO = {"means": "a Colorado water court region, 1 through 7",
      "applies_to": "path:models/water"}
EVERYWHERE = {"means": "the join grain"}


@pytest.fixture
def project(project_dir):
    return Project.load(project_dir)


def _ctx(project, vocab):
    return states.Ctx(project=project, vocab=vocab)


def test_an_unscoped_term_still_reaches_everything(project):
    """*** THE UPGRADE MUST BE A NO-OP. *** Every term meant `everywhere` before `applies_to`
    existed, so a config written yesterday behaves identically today."""
    ctx = _ctx(project, {"section_id": EVERYWHERE})
    uid = next(iter(project.models))
    assert ctx.vocab_for(uid) == {"section_id": EVERYWHERE}
    assert not ctx.vocab_drops


def test_a_scoped_term_does_not_reach_a_model_outside_it(project):
    uid = next(iter(project.models))
    path = project.models[uid].path.rsplit("/", 1)[0]
    inside = {"means": "x", "applies_to": f"path:{path}"}
    ctx = _ctx(project, {"t": inside})
    assert ctx.vocab_for(uid) == {"t": inside}

    outside = next((u for u, m in project.models.items()
                    if not m.path.startswith(path + "/")), None)
    if outside:
        assert ctx.vocab_for(outside) == {}
        assert ctx.vocab_drops


def test_a_batch_spanning_two_scopes_gets_neither(project):
    """*** INTERSECTION, BECAUSE A STATE THAT CONTRADICTS ITSELF IS WORSE THAN A THIN ONE. ***

    Under a union rule a chunk pairing a model from one jurisdiction with one from another is
    told both that A decides the question and that B does, in a single call.
    """
    a, b = _two_in_different_directories(project)
    pa = project.models[a].path.rsplit("/", 1)[0]
    ta = {"means": "only where a lives", "applies_to": f"path:{pa}"}
    ctx = _ctx(project, {"ta": ta, "shared": EVERYWHERE})
    assert ctx.vocab_for(a) == {"ta": ta, "shared": EVERYWHERE}
    assert ctx.vocab_for(a, b) == {"shared": EVERYWHERE}, "a mixed batch kept a scoped term"
    assert ctx.vocab_drops, "the drop must be recorded, or a thinning vocabulary is invisible"


def _two_in_different_directories(project):
    """*** A SKIPPED TEST PROVES NOTHING. ***
    The first version skipped when the fixture happened to put both models in one directory,
    which is a guard that passes by not running."""
    by_dir = {}
    for uid, m in sorted(project.models.items()):
        by_dir.setdefault(m.path.rsplit("/", 1)[0], []).append(uid)
    dirs = sorted(by_dir)
    assert len(dirs) >= 2, "the fixture has one directory; this test cannot mean anything"
    return by_dir[dirs[0]][0], by_dir[dirs[1]][0]


def test_a_state_whose_subjects_are_unknown_keeps_no_scoped_term(project):
    """*** `all()` OVER AN EMPTY LIST IS TRUE, WHICH IS HOW SCOPING FAILS OPEN. ***

    Found by writing it wrong: `align`'s pairs carry model NAMES, not uids, so the first version
    resolved none of them, `known` was empty, and every scoped term was kept while the feature
    read as working.
    """
    ctx = _ctx(project, {"t": CO, "shared": EVERYWHERE})
    assert ctx.vocab_for() == {"shared": EVERYWHERE}
    assert ctx.vocab_for(None) == {"shared": EVERYWHERE}


def test_a_scope_can_subtract_because_the_exception_lives_inside_the_rule(project, project_dir):
    """*** models/water/az IS INSIDE models/water. ***

    70 Arizona models sit under the very path a Colorado term would be scoped to, so a bare
    `path:models/water` reaches every one of them and the scoping changes nothing for the case
    that motivated it.
    """
    uids = list(project.models)
    keep, drop = uids[0], uids[1]
    root = "models"
    term = {"means": "x",
            "applies_to": {"select": f"path:{root}",
                           "exclude": f"{project.models[drop].name}"}}
    ctx = _ctx(project, {"t": term})
    assert ctx.vocab_for(keep) == {"t": term}
    assert ctx.vocab_for(drop) == {}, "the exclusion did not subtract"


def test_an_exclude_with_no_select_is_refused(tmp_path):
    (tmp_path / "audit.yml").write_text(
        "vocab:\n  t:\n    means: x\n    applies_to:\n      exclude: \"path:models/az\"\n")
    with pytest.raises(ThresholdError, match="needs a `select`"):
        Config.load(tmp_path)


def test_a_selector_assay_cannot_read_is_refused_at_load(tmp_path):
    """The same rule `when.select` already has: a selector silently ignored scopes nothing while
    looking as though it did."""
    (tmp_path / "audit.yml").write_text(
        "vocab:\n  t:\n    means: x\n    applies_to: \"tag_typo:finance\"\n")
    with pytest.raises(ThresholdError, match="vocab `t`"):
        Config.load(tmp_path)


# ------------------------------------------------------------------ the lint

def test_a_term_citing_a_statute_with_no_scope_is_warned():
    issues = lint_vocab({"nontributary": {
        "means": "Denver Basin groundwater, C.R.S. 37-90-137(4)"}})
    assert [i.rule for i in issues] == ["asserts_law_everywhere"]
    assert "C.R.S" in issues[0].detail


def test_a_term_naming_a_state_with_no_scope_is_warned():
    issues = lint_vocab({"water_division": {"means": "a Colorado water court region"}})
    assert [i.rule for i in issues] == ["asserts_law_everywhere"]


def test_a_term_that_declares_its_scope_is_not_warned():
    assert lint_vocab({"water_division": {
        "means": "a Colorado water court region", "applies_to": "path:models/water"}}) == []


def test_a_term_with_no_definition_is_an_error():
    issues = lint_vocab({"t": {"implies": "something"}})
    assert [(i.level, i.rule) for i in issues] == [("error", "term_undefined")]


def test_a_scope_that_matches_nothing_is_an_error(project):
    """*** A SCANNER MATCHING NOTHING PASSES WRONGLY. ***
    A term scoped to a path that does not exist reaches no state at all, and the config reads as
    though the word were defined."""
    issues = lint_vocab({"t": {"means": "x", "applies_to": "path:models/does_not_exist"}}, project)
    rules = [i.rule for i in issues]
    assert "scope_matches_nothing" in rules
    assert [i.level for i in issues if i.rule == "scope_matches_nothing"] == ["error"]


def test_the_suggested_exclusion_is_a_selector_that_actually_subtracts(project, project_dir):
    """*** THE FIRST VERSION SUGGESTED `models/water/az` WITH NO `path:` PREFIX. ***

    No colon, so `selector` reads it as a MODEL NAME, finds no model called that, and the
    exclusion subtracts nothing -- a guard handing out a fix that quietly does nothing, produced
    by the part of the tool that checks for exactly that. Measured on the field warehouse:
    `path:models/water` is 192 models, the bad exclusion removed 0 of them and the correct one
    removes 70.

    So the lint's own suggestion is parsed and run here, rather than eyeballed.
    """
    from dbt_assay.selector import resolve, validate
    issues = [i for i in lint_vocab(_narrow_vocab(project), project)
              if i.rule == "narrower_than_where_it_is_sent"]
    if not issues:
        pytest.skip("the fixture has no term concentrated in one subdirectory")
    for i in issues:
        for expr in _selectors_in(i.detail):
            validate(expr)                                  # raises on syntax assay cannot read
            assert resolve(project, expr), (
                f"the lint suggested {expr!r}, which matches no model: a fix that silently "
                f"does nothing")


def _narrow_vocab(project) -> dict:
    """A term named after a word that appears in exactly one model, so the reach rule fires."""
    m = next(iter(project.models.values()))
    return {m.name: {"means": "whatever this model is about"}}


def _selectors_in(detail: str) -> list:
    """Every `path:...` / `tag:...` token the message proposes."""
    import re
    return [t for t in re.findall(r'"([^"]+)"', detail) if ":" in t]


def test_the_lint_does_not_say_the_same_thing_twice(project):
    """A term that cites a statute AND is concentrated in one directory gets one warning, not two
    asking for the same fix. A guard that repeats itself is a guard people stop reading."""
    v = {"nontributary": {"means": "Denver Basin groundwater, C.R.S. 37-90-137(4)"}}
    rules = [i.rule for i in lint_vocab(v, project) if i.question == "vocab.nontributary"]
    assert rules.count("narrower_than_where_it_is_sent") == 0
