from dbt_assay import versioning as v
from dbt_assay.diff import Change
from dbt_assay.inventory import ColumnEntry, Fact, ModelEntry

SCHEMA = """\
version: 2

models:
  # this comment explains something a YAML round-trip would silently destroy
  - name: water_rights
    description: "one row per right"
    columns:
      - name: wdid
  - name: other_model
    meta:
      owner: data
    description: "x"
"""


def test_a_reformat_owes_nothing():
    """Every other version-bump check fires on whitespace and gets switched off. This is the
    whole reason the rule is bearable."""
    assert v.required_level([])[0] == "none"


def test_the_level_comes_from_what_the_change_does_to_a_consumer():
    assert v.required_level([Change("m", "grain_in_sql")])[0] == "major"
    assert v.required_level([Change("m", "column_removed", column="x")])[0] == "major"
    assert v.required_level([Change("m", "role", column="x")])[0] == "major"
    assert v.required_level([Change("m", "provenance", column="x")])[0] == "major"
    assert v.required_level([Change("m", "column_added", column="x")])[0] == "minor"


def test_a_major_change_outranks_a_minor_one_in_the_same_commit():
    level, why = v.required_level([Change("m", "column_added", column="a"),
                                   Change("m", "grain", detail="moved")])
    assert level == "major" and len(why) == 2


def test_a_state_knows_whether_it_was_bumped_and_what_it_owes():
    owed = v.VersionState("m", before=2, after=2, required="major", reasons=[])
    done = v.VersionState("m", before=2, after=3, required="major", reasons=[])
    assert owed.owes and not owed.bumped and owed.suggested == 3
    assert done.bumped and not done.owes


def test_an_unversioned_model_that_changes_meaning_still_owes_one():
    s = v.VersionState("m", before=None, after=None, required="minor", reasons=[])
    assert s.owes and s.suggested == 1


def test_the_bump_is_a_targeted_edit_that_keeps_your_comments(tmp_path):
    """These files carry a lot of hand-written commentary and yaml.safe_dump discards all of it."""
    p = tmp_path / "s.yml"
    p.write_text(SCHEMA)
    assert v.write_bump(str(p), "water_rights", 3)
    out = p.read_text()
    assert "# this comment explains something" in out
    assert 'description: "one row per right"' in out
    import yaml
    d = yaml.safe_load(out)
    m = next(x for x in d["models"] if x["name"] == "water_rights")
    assert m["meta"]["version"] == 3
    assert next(x for x in d["models"] if x["name"] == "other_model")["meta"]["owner"] == "data"


def test_a_bump_uses_an_existing_meta_block_rather_than_adding_a_second(tmp_path):
    p = tmp_path / "s.yml"
    p.write_text(SCHEMA)
    assert v.write_bump(str(p), "other_model", 2)
    import yaml
    m = next(x for x in yaml.safe_load(p.read_text())["models"] if x["name"] == "other_model")
    assert m["meta"] == {"owner": "data", "version": 2}


def test_bumping_a_model_that_is_not_there_reports_failure_rather_than_corrupting(tmp_path):
    p = tmp_path / "s.yml"
    p.write_text(SCHEMA)
    assert v.write_bump(str(p), "no_such_model", 2) is False
    assert p.read_text() == SCHEMA


def test_the_patch_text_names_the_file_and_the_model():
    s = v.VersionState("m", 1, 1, "major", [], patch_path="models/a/schema.yml")
    t = v.patch_text(s)
    assert "models/a/schema.yml" in t and "- name: m" in t and "version: 2" in t


def _entry(name, cols):
    e = ModelEntry(uid=f"model.p.{name}", name=name, path="p.sql", layer="marts",
                   materialized="table")
    e.columns = [ColumnEntry(name=c, provenance=Fact(p, "derived")) for c, p in cols]
    return e


class _Dig:
    def __init__(self, exprs):
        self.output_exprs = exprs


def test_a_version_stamp_is_just_a_constant_column_named_like_one():
    """Provenance already classifies a literal as `constant`, so this costs nothing."""
    e = _entry("m", [("model_version", "constant"), ("amount", "computed")])
    got = v.version_column(e, _Dig({"model_version": "3"}))
    assert got == ("model_version", "3")


def test_a_computed_column_is_not_a_stamp_even_if_it_is_named_like_one():
    e = _entry("m", [("model_version", "computed")])
    assert v.version_column(e, _Dig({"model_version": "x + 1"})) is None
