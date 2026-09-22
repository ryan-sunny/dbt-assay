"""*** NOTHING KNEW WHEN ITS OWN KNOWLEDGE WENT STALE AGAINST THE CODE. ***

`live_decisions` counts an answer stale by `prompt_version` -- whether the QUESTION changed. A
model edited after being judged served its old answer with no signal, and the only way to find out
was to pay to re-ask. dbt has hashed every model's source file the whole time: 358 of 358 on the
field warehouse, and assay read none of them.

The third answer is the one these tests are mostly about. An answer with no checksum is one assay
CANNOT CHECK, and it is counted in its own column rather than folded into "current". An absent
measurement never reads as a pass.
"""
from __future__ import annotations

import json

import pytest
from test_cost import FakeClient, _questions

from dbt_assay import stale
from dbt_assay.jev import decide
from dbt_assay.manifest import Project
from dbt_assay.store import Store


@pytest.fixture
def project(project_dir):
    return Project.load(project_dir)


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "s.duckdb"))
    yield s
    s.close()


def _ask(store, project, key="model.p.stg_bad_notnull", n=2, tokens=1000, call_id="gen-1"):
    store.use_project(project)
    return decide(store, FakeClient(n_answers=n, usage={"input_tokens": tokens},
                                    call_id=call_id),
                  {"sql": "select 1"}, _questions(n),
                  decision_key=key, prompt_version="v1", caller="assay.claims")


def test_the_checksum_is_written_at_decide_time(store, project):
    _ask(store, project)
    got = {r[0] for r in store.con.execute(
        "select file_checksum from model_decisions").fetchall()}
    assert got == {project.models["model.p.stg_bad_notnull"].checksum}
    assert all(got), "an empty checksum is the thing that reports as uncheckable"


def test_an_unchanged_model_reads_as_current_and_a_changed_one_as_moved(store, project,
                                                                        project_dir):
    _ask(store, project)
    out = stale.survey(store, project)
    assert len(out["moved"]) == 0 and out["current"] == 2

    # the file moved: dbt would record a different sha256 on the next parse
    mf = json.loads((project_dir / "manifest.json").read_text())
    mf["nodes"]["model.p.stg_bad_notnull"]["checksum"]["checksum"] = "f" * 64
    (project_dir / "manifest.json").write_text(json.dumps(mf))

    out = stale.survey(store, Project.load(project_dir))
    assert len(out["moved"]) == 2 and out["current"] == 0
    assert out["moved"][0]["model"] == "stg_bad_notnull"
    assert out["by_family"] == [("sentence_is_a_claim", 2)]


def test_a_decision_with_no_checksum_is_uncheckable_and_not_current(store, project):
    """*** THE WHOLE FIELD STORE IS IN THIS STATE, AND IT MUST NOT READ AS FINE. ***

    All 19,707 answers on the field warehouse were decided before the column existed. Backfilling
    today's checksum onto them would make every one report "current", which is precisely the lie
    this tool exists to find.
    """
    _ask(store, project)
    store.con.execute("update model_decisions set file_checksum = null")
    out = stale.survey(store, project)
    assert out["current"] == 0 and len(out["moved"]) == 0
    assert len(out["uncheckable"]) == 2
    assert out["why_uncheckable"] == [("decided before assay recorded a checksum", 2)]


def test_a_key_that_names_no_model_is_uncheckable_by_name(store, project):
    """`pair::` and `bank` keys -- 190 of 19,707 on the field store. Never a model, so never a
    checksum, and the report says which of the three reasons applies."""
    _ask(store, project, key="pair::column_is_part_of_the_key::abc", n=1)
    out = stale.survey(store, project)
    assert len(out["uncheckable"]) == 1
    assert out["why_uncheckable"] == [("the decision key does not name a model", 1)]


def test_only_the_latest_answer_to_a_question_counts(store, project, project_dir):
    """The rule `live_decisions` settled on: the newest answer wins, with no family resolution
    anywhere near it. A superseded row must not be surveyed twice."""
    _ask(store, project, n=2)
    _ask(store, project, n=2, tokens=1100, call_id="gen-2")
    out = stale.survey(store, project)
    assert out["judged"] == 2, "two questions, not four rows"


def test_the_quote_is_what_those_calls_cost_not_an_average(store, project, project_dir):
    """A mean applied to a count is an estimate wearing a measurement's clothes. Each stale answer
    came from a call whose tokens are recorded, so the quote is the sum of those calls."""
    from dbt_assay.jev import USD_PER_INPUT_TOKEN as RATE
    _ask(store, project, key="model.p.stg_bad_notnull", tokens=1000, call_id="c1")
    _ask(store, project, key="model.p.stg_ok_notnull", tokens=3000, call_id="c2")

    mf = json.loads((project_dir / "manifest.json").read_text())
    mf["nodes"]["model.p.stg_bad_notnull"]["checksum"]["checksum"] = "f" * 64
    (project_dir / "manifest.json").write_text(json.dumps(mf))

    out = stale.survey(store, Project.load(project_dir))
    q = stale.quote(store, out["moved"])
    assert q["calls"] == 1 and q["input_tokens"] == 1000
    assert q["usd"] == pytest.approx(1000 * RATE), "only the stale model's call, at its own rate"


def test_the_moved_list_is_ordered_by_blast_radius(store, project, project_dir):
    """Ordering is by what rests on it, and ties break on the name, so two runs agree."""
    _ask(store, project, key="model.p.stg_bad_notnull", call_id="c1")     # has a child
    _ask(store, project, key="model.p.stg_ok_notnull", call_id="c2")      # has none
    mf = json.loads((project_dir / "manifest.json").read_text())
    for uid in ("model.p.stg_bad_notnull", "model.p.stg_ok_notnull"):
        mf["nodes"][uid]["checksum"]["checksum"] = "f" * 64
    (project_dir / "manifest.json").write_text(json.dumps(mf))
    out = stale.survey(store, Project.load(project_dir))
    names = [e["model"] for e in out["by_model"]]
    assert names[0] == "stg_bad_notnull", names
    assert names == [e["model"] for e in
                     stale.survey(store, Project.load(project_dir))["by_model"]]
