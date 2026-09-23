"""`waivers` and `explanations` take the selector `vocab` already had."""
import pytest

from dbt_assay import reviewform
from dbt_assay.config import Config, ThresholdError
from dbt_assay.manifest import Project


def test_a_named_explanation_set_covers_what_it_selects_and_a_model_overrides(project_dir):
    project = Project.load(project_dir)
    cfg = Config.from_dict({"explanations": {
        "tildes": {"applies_to": {"select": "stg_bad_tilde stg_ok_tilde",
                                  "exclude": "stg_ok_tilde"},
                   "options": {"upstream_late": "the source had not published"}},
        "stg_bad_tilde": {"known_alias": "an old name for the same city"}}})
    got = cfg.explanations_for("stg_bad_tilde", project, "model.p.stg_bad_tilde")
    assert set(got) == {"upstream_late", "known_alias"}
    assert cfg.explanations_for("stg_ok_tilde", project, "model.p.stg_ok_tilde") == {}


def test_the_list_spelling_the_guide_once_showed_is_read():
    cfg = Config.from_dict({"explanations": {"m": [{"a": "one"}, {"b": "two"}]}})
    assert cfg.explanations == {"m": {"a": "one", "b": "two"}}


@pytest.mark.parametrize("body, needle", [
    ({"s": {"options": {"a": "x"}}}, "applies_to"),
    ({"s": {"applies_to": "tagz:x", "options": {"a": "x"}}}, "does not understand"),
    ({"s": {"applies_to": "m", "options": {"a": "x"}, "extra": 1}}, "unknown key"),
    ({"m": "not options"}, "options are"),
])
def test_an_explanation_set_refuses_what_it_cannot_honour(body, needle):
    with pytest.raises(ThresholdError, match=needle):
        Config.from_dict({"explanations": body})


def test_the_form_shows_a_set_once_instead_of_a_card_per_mart(project_dir):
    project = Project.load(project_dir)
    cfg = Config.from_dict({"explanations": {"tildes": {
        "applies_to": "stg_bad_tilde stg_ok_tilde", "options": {"a": "x"}}}})

    class F:
        def __init__(self, n):
            self.subject_name = n
    rows = reviewform._explanation_rows(
        cfg, [F("stg_bad_tilde"), F("stg_ok_tilde"), F("int_bad_unique")], project)
    names = [r["mart"] for r in rows]
    assert names == ["tildes", "int_bad_unique"], names
    assert rows[0]["named"] and "stg_bad_tilde" in rows[0]["applies_to"]
