"""The setup guidance, and the one thing that would make it worse than nothing.

A guide that quietly disagrees with the linter it describes is worse than no guide: somebody
follows it, the check fires anyway, and now they distrust both. So everything the machine already
knows is READ from the machine, and these assert that it still is.
"""
from __future__ import annotations

import pytest

from dbt_assay import guide as g


def test_every_topic_returns_something_a_person_can_act_on():
    for t in g.TOPICS:
        body = g.guide(t)
        assert len(body) > 400, f"{t} is a stub"
        assert body.lstrip().startswith("#"), f"{t} has no heading"
        # *** EVERY RULE IN HERE WAS MEASURED. ***
        # The failure mode for a guide is confident generic advice, which reads exactly like
        # earned advice and is worth less than nothing. Each topic has to carry a number, a
        # command, or a named mechanism rather than an opinion.
        assert any(ch.isdigit() for ch in body) or "`" in body, f"{t} is opinion only"


def test_an_unknown_topic_gives_the_index_rather_than_nothing():
    """A typo must not read as "there is no guidance"."""
    for bad in ("", "nonsense", "waiver", None):
        body = g.guide(bad or "")
        assert "assay guide" in body, bad
        assert body == g.index(), bad
    # and a topic named sloppily still lands on the topic
    for ok in ("vocab", "VOCAB", " Vocab "):
        assert g.guide(ok) == g.guide("vocab") != g.index(), ok


def test_the_lint_rules_it_teaches_are_read_from_the_linter():
    """*** A SECOND COPY OF A LIST IS THE COPY THAT DRIFTS. ***

    The guide tells somebody how to frame a question, and `assay banks --lint` is what will judge
    what they write. If the guide kept its own list, a new rule would be enforced and untaught,
    or a removed one taught and unenforced. It reads the codes out of the module that raises them.
    """
    from dbt_assay import lint

    rules = g._lint_rules()
    assert len(rules) >= 5, rules
    src = __import__("inspect").getsource(lint)
    for r in rules:
        assert r in src, f"{r} is not a code the linter actually raises"
    body = g.guide("questions")
    for r in rules:
        assert f"`{r}`" in body, f"{r} is enforced and untaught"


def test_the_config_keys_it_names_are_read_from_the_default_file():
    """Naming a key `audit.yml` does not read is how somebody writes config that does nothing --
    the exact defect the shipped example had, where a family name sat where a check name belongs."""
    from dbt_assay.config import DEFAULT_YML

    keys = g._config_keys()
    assert {"vocab", "waivers", "questions", "gating"} <= set(keys), keys
    for k in keys:
        assert f"{k}:" in DEFAULT_YML
        assert f"`{k}`" in g.index(), f"{k} is read and never mentioned"


@pytest.mark.parametrize("topic,must_say", [
    ("questions", ["cannot do arithmetic", "Absence is not disagreement", "decline"]),
    ("waivers", ["A reason is required, and the reason is a measurement", "looks fine"]),
    ("policy", ["min_adjudications", "CHECK a finding carries"]),
    ("vocab", ["sent with every question"]),
    ("ruling", ["evidence and never authority", "FINDING, not the model"]),
    ("start", ["assay onboard", "no spend"]),
])
def test_each_topic_carries_the_rule_it_exists_for(topic, must_say):
    """The specific things that, left out, make the topic actively misleading. Each one is a
    defect that was measured in the field before it became a sentence here."""
    body = g.guide(topic)
    for phrase in must_say:
        assert phrase in body, f"{topic} no longer says: {phrase}"


def test_the_guide_reaches_an_agent_and_a_terminal():
    """*** A TOOL THAT ONLY ITS OWN COMMAND CAN SEE IS NOT PART OF THE TOOL. ***

    The same wiring rule as every other surface: MCP, the skill, and a CLI command that answers
    the same question, so the skill works with or without the server.
    """
    import inspect

    from dbt_assay import cli, mcp_server, skilltext

    names = [t[0] for t in mcp_server.TOOLS]
    assert "guide" in names, "an agent cannot reach it"
    assert "guide" in inspect.getsource(cli), "there is no command"
    assert "`guide(topic)` | `assay guide" in skilltext.SKILL_MD, "the skill has no CLI equivalent"
    # and the skill must TEACH the setup job, not merely list the tool
    for phrase in ('guide("vocab")', 'guide("questions")', "Do not invent configuration"):
        assert phrase in skilltext.SKILL_MD, phrase
