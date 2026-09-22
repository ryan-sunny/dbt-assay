"""*** IT IS THEIR FILE, AND MOST OF WHAT IS IN IT IS THE COMMENTS. ***

`audit.yml` ships with forty lines of explanation around every block and people add their own.
PyYAML cannot round-trip a comment: load-and-dump returns the same meaning with none of the
reasons, reordered. A form that handed config back and applied it that way would silently delete
the part of the file that took longest to write.
"""
from __future__ import annotations

import yaml

from dbt_assay.configpatch import Change, apply

BEFORE = '''\
# vocab: what the words mean here. Injected into state for EVERY question.
vocab:
  # a Colorado thing, and the comment explaining why is the point
  water_division:
    means: "a Colorado water court region, 1 through 7"
    implies: "a case number is unique only within one division"

  section_id:
    means: "the join grain, and NOT always a PLSS section"

# waivers: reason required, expiry optional but recommended.
waivers: {}
'''


def test_a_comment_survives_the_edit():
    """The one property the whole module exists for."""
    out = apply(BEFORE, [Change(["vocab", "water_division", "applies_to"],
                                {"select": "path:models/water",
                                 "exclude": "path:models/water/az"})])
    assert not out.refused, out.refused
    for comment in ("# vocab: what the words mean here",
                    "# a Colorado thing, and the comment explaining why is the point",
                    "# waivers: reason required"):
        assert comment in out.text, f"lost: {comment}"


def test_the_change_is_actually_there_and_the_file_still_parses():
    out = apply(BEFORE, [Change(["vocab", "water_division", "applies_to"],
                                {"select": "path:models/water",
                                 "exclude": "path:models/water/az"})])
    got = yaml.safe_load(out.text)
    assert got["vocab"]["water_division"]["applies_to"] == {
        "select": "path:models/water", "exclude": "path:models/water/az"}
    # and nothing else moved
    assert got["vocab"]["section_id"]["means"].startswith("the join grain")
    assert got["vocab"]["water_division"]["means"].startswith("a Colorado")


def test_nothing_outside_the_edited_key_changes():
    out = apply(BEFORE, [Change(["vocab", "section_id", "applies_to"], "path:models/water")])
    a = [ln for ln in BEFORE.splitlines() if "applies_to" not in ln]
    b = [ln for ln in out.text.splitlines() if "applies_to" not in ln]
    assert a == b, "a line that was not the target moved"


def test_a_new_term_is_added_under_vocab():
    out = apply(BEFORE, [Change(["vocab", "wdid"],
                                {"means": "a structure id; NOT unique per water right"})])
    assert not out.refused, out.refused
    got = yaml.safe_load(out.text)
    assert got["vocab"]["wdid"]["means"].startswith("a structure id")
    assert len(got["vocab"]) == 3


def test_an_empty_flow_mapping_becomes_a_block():
    """*** THE SHIPPED TEMPLATE IS `vocab: {}` AND THAT IS NOT A BLOCK. ***

    Inserting a child under a flow mapping produces a file that does not parse. Every project that
    ran `assay onboard` and never edited the file is in exactly this state, so it is the common
    case rather than an edge one.
    """
    out = apply("vocab: {}\n", [Change(["vocab", "t"], {"means": "x"})])
    assert not out.refused, out.refused
    assert yaml.safe_load(out.text)["vocab"]["t"]["means"] == "x"


def test_a_non_empty_one_line_mapping_is_refused_not_rewritten():
    """Somebody's deliberate style. Refusing is checkable; rewriting it silently is not."""
    out = apply('vocab: {t: {means: x}}\n', [Change(["vocab", "u"], {"means": "y"})])
    assert not out.applied
    assert out.refused and "one line" in out.refused[0][1]
    assert out.text.strip() == 'vocab: {t: {means: x}}'


def test_replacing_a_scalar_keeps_its_neighbours():
    out = apply(BEFORE, [Change(["vocab", "section_id", "means"], "a different sentence")])
    got = yaml.safe_load(out.text)
    assert got["vocab"]["section_id"]["means"] == "a different sentence"
    assert got["vocab"]["water_division"]["implies"].startswith("a case number")
    assert "# a Colorado thing" in out.text


def test_a_long_sentence_is_folded_rather_than_run_off_the_page():
    """A `means:` is prose somebody wrote. A 200-character line is one nobody edits again."""
    long = ("a water right perfected by actually diverting and using the water, as opposed to a "
            "conditional one which is a claim held open by showing reasonable diligence each "
            "six years")
    out = apply(BEFORE, [Change(["vocab", "absolute"], {"means": long})])
    assert yaml.safe_load(out.text)["vocab"]["absolute"]["means"] == long
    assert all(len(ln) <= 100 for ln in out.text.splitlines()), "a line ran off the page"


def test_a_path_with_no_parent_is_refused_with_a_reason():
    out = apply(BEFORE, [Change(["explanations", "water_rights", "conditional"], "x")])
    assert not out.applied
    assert out.refused and out.refused[0][1]


def test_applying_twice_is_the_same_file():
    """A form handed back twice must not append the term twice."""
    ch = [Change(["vocab", "wdid"], {"means": "a structure id"})]
    once = apply(BEFORE, ch).text
    twice = apply(once, ch).text
    assert once == twice


def test_several_changes_in_one_pass():
    out = apply(BEFORE, [
        Change(["vocab", "water_division", "applies_to"], "path:models/water"),
        Change(["vocab", "wdid"], {"means": "a structure id"}),
        Change(["vocab", "section_id", "implies"], "join on it, not on a PLSS section"),
    ])
    assert not out.refused, out.refused
    got = yaml.safe_load(out.text)
    assert got["vocab"]["water_division"]["applies_to"] == "path:models/water"
    assert got["vocab"]["wdid"]["means"] == "a structure id"
    assert got["vocab"]["section_id"]["implies"].startswith("join on it")
    assert "# a Colorado thing" in out.text
