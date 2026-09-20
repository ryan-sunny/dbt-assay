"""*** THE SAME BUG, FOUR TIMES, ALWAYS SILENT. ***

One fact lived in two places under two spellings, and the two never agreed:

| the fact                | written as              | read as                  | cost |
|-------------------------|-------------------------|--------------------------|------|
| the SQL dialect         | manifest adapter_type   | a `duckdb` default       | 12 parse failures became 128 |
| a model's judgments     | `<uid>::desc`           | `decision_key = <uid>`   | nothing reached the reports |
| a verdict's subject     | question family         | finding check name       | 9 of 10 families gated nothing |
| a question's family     | `_FAMILY` by hand       | bank keys in YAML        | `name__`/`unit__` filed under nothing |

Every one failed by producing LESS, never by producing something visibly wrong, so every one was
found by reading rather than by running. This file removes the last of them as a CLASS: the banks
declare their own prefixes, the map is derived, and the check runs where a question is asked rather
than in a test that has to guess at every call site.
"""
import pytest

from dbt_assay.contracts import check_question_ids, family_of, load_all_banks


def test_every_bank_declares_a_prefix():
    missing = [n for n, q in load_all_banks().items() if not q.get("id_prefix")]
    assert not missing, f"{missing} declare no id_prefix, so verdicts on them file under nothing"


def test_no_two_banks_claim_the_same_prefix():
    """Two banks sharing a prefix means one family silently absorbs the other's verdicts."""
    seen: dict = {}
    for name, q in load_all_banks().items():
        p = q.get("id_prefix")
        assert p not in seen, f"{name} and {seen.get(p)} both claim {p!r}"
        seen[p] = name


def test_every_prefix_round_trips_to_its_own_bank():
    for name, q in load_all_banks().items():
        p = q["id_prefix"]
        assert family_of(p) == name                      # a bare id, as `desc` is written
        assert family_of(f"{p}__some_column") == name    # and the `<prefix>__<subject>` form


def test_an_id_no_bank_claims_raises_where_it_is_asked():
    """*** THIS IS THE CLASS, CLOSED. ***

    `name__county` and `unit__cfs` were asked, answered, stored and adjudicated for months under a
    family that does not exist. Nothing failed; the verdicts simply counted toward nothing.
    """
    check_question_ids(["role__zip", "desc", "unit__cfs", "name__county"])   # all real
    with pytest.raises(ValueError) as e:
        check_question_ids(["role__zip", "bogus__thing"])
    assert "bogus__thing" in str(e.value)
    assert "id_prefix" in str(e.value)


def test_decide_refuses_a_question_it_cannot_file():
    """The check has to run on the REAL path, not only in this file."""
    import inspect

    from dbt_assay import jev
    src = inspect.getsource(jev.decide)
    assert "check_question_ids(questions)" in src


def test_the_family_map_is_derived_and_not_typed_out_again():
    """A third copy of this fact is how the first two drifted."""
    from dbt_assay.cli import _FAMILY
    banks = load_all_banks()
    assert len(_FAMILY) == len(banks)
    for prefix, name in _FAMILY.items():
        assert banks[name]["id_prefix"] == prefix


def test_a_config_key_that_matches_no_check_is_reported():
    """*** assay's OWN SHIPPED DEFAULT HAD ONE FOR MONTHS. ***

    `questions:` is keyed by the CHECK a finding carries. The verdict floor is counted by the
    QUESTION FAMILY it rests on. Two namespaces, confusable names, and the example everyone copies
    used a family where a check belongs, so it configured nothing and nothing said so.
    """
    import yaml

    from dbt_assay.config import DEFAULT_YML, Config, known_checks

    assert Config.from_dict(yaml.safe_load(DEFAULT_YML)).unknown_questions == []

    bad = Config.from_dict({"questions": {"column_is_part_of_the_key": {"action": "queue"}}})
    assert bad.unknown_questions == ["column_is_part_of_the_key"]

    # And a real check name is accepted.
    ok = Config.from_dict({"questions": {"test_cannot_fail": {"action": "fail"}}})
    assert ok.unknown_questions == []
    assert "test_cannot_fail" in known_checks()


def test_a_waiver_naming_no_real_check_is_reported():
    """*** A DEAD WAIVER IS THE WORST OF THE THREE. ***

    A dead `questions:` key leaves a finding un-configured, which is visible: the finding is still
    there. A dead waiver is where someone believes a finding HAS been dealt with, and it silences
    nothing. assay's own example waived `figure_is_plausible`, which is neither a check nor a
    question family.
    """
    from dbt_assay.config import Config
    c = Config.from_dict({"waivers": {"m": [{"question": "figure_is_plausible",
                                             "reason": "because"}]}})
    assert "figure_is_plausible" in c.unknown_questions

    ok = Config.from_dict({"waivers": {"m": [{"question": "test_cannot_fail",
                                              "reason": "because"}]}})
    assert ok.unknown_questions == []


def test_every_example_in_the_shipped_default_is_live():
    """The file everyone copies from must not contain a single line that does nothing."""
    import re

    import yaml

    from dbt_assay.config import DEFAULT_YML, Config, known_checks

    checks = known_checks()
    assert checks                               # a reader that finds nothing must not pass

    cfg = Config.from_dict(yaml.safe_load(DEFAULT_YML) or {})
    assert cfg.unknown_questions == [], cfg.unknown_questions

    # The COMMENTED examples are what people uncomment, so they are checked too. Naively
    # stripping `#` does not parse -- the file is mostly prose -- so the example names are read
    # directly: `- question: X` in the waiver block, and any key nested under `questions:`.
    named = set(re.findall(r"^#?\s*-?\s*question:\s*([a-z_]+)", DEFAULT_YML, re.MULTILINE))
    in_questions, indent = set(), None
    for line in DEFAULT_YML.splitlines():
        body = line.lstrip("#").rstrip()
        if body.strip() == "questions:":
            indent = 0
            continue
        if indent is None:
            continue
        if body and not body.startswith(" "):
            indent = None                       # left the questions block
            continue
        m = re.match(r"^  ([a-z_]+):\s*$", body)
        if m:
            in_questions.add(m.group(1))
    dead = sorted((named | in_questions) - checks)
    assert not dead, f"examples in the shipped default that configure nothing: {dead}"
    assert in_questions, "the reader found no question examples at all"
