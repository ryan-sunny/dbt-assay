"""From ruling on all 99 findings of a 357-model warehouse by hand.

*** TWO STRUCTURAL BLIND SPOTS CAUSED EVERY SINGLE DISAGREEMENT. ***
Twelve disagrees: ten were a union member read as a fan-out, two were a tessellation read as an
approximated circle. Both discriminators are readable from the AST with no judgment and no call,
which is the point -- the judgment was allowed to be wrong about something code settles exactly.
"""
from pathlib import Path
from types import SimpleNamespace

from dbt_assay.checks.structural import (
    cannot_fail_by_construction,
    default_literal,
    default_share_sql,
)
from dbt_assay.parse import digest

ROOT = Path(__file__).resolve().parents[1]


def test_a_union_member_cannot_multiply():
    """`dim_business` (16 marts) unions eleven staging feeds and was reported
    `silently_multiplied`. One parent row is one child row; the child having more rows than any
    single parent is the union, not a fan-out on that hop."""
    d = digest("select a from stg_one union all select a from stg_two "
               "union all select a from stg_three", "m", "duckdb")
    assert d.union_members == {"stg_one", "stg_two", "stg_three"}

    joined = digest("select a from x join y on x.k = y.k", "m", "duckdb")
    assert joined.union_members == set(), "a join is not a union"


def test_the_fanout_finding_refuses_a_union_parent():
    """The judgment may be wrong here. The FINDING may not, because a parser settles it.

    Exercised rather than grepped: asserting on the wording of a comment is how a guard passes
    while the code beneath it changed.
    """
    from dbt_assay.inventory import ModelEntry
    from dbt_assay.judged import hop_multiplies_rows

    e = ModelEntry(uid="model.p.dim_business", name="dim_business", path="d.sql",
                   layer="marts", materialized="table")
    e.marts = 16
    e.fanout_hops = [("stg_az_liquor -> dim_business", 0.81)]

    e.union_parents = set()
    assert len(hop_multiplies_rows(None, [e])) == 1, "an ordinary hop still reports"

    e.union_parents = {"stg_az_liquor"}
    assert hop_multiplies_rows(None, [e]) == [], "a union member cannot multiply"


def test_an_envelope_from_stored_bounds_is_a_tessellation():
    """`ST_MakeEnvelope(cx0, cy0, cx1, cy1)` from columns on the same row IS the intended
    geometry. "A box is not a circle" only holds when the envelope stands in for a radius."""
    stored = digest("select 1 from a join b on ST_Intersects(b.geom, "
                    "ST_MakeEnvelope(a.cx0, a.cy0, a.cx1, a.cy1))", "m", "duckdb")
    assert stored.bbox_corners == {"ST_MAKEENVELOPE": "stored_bounds"}

    radius = digest("select 1 from a join b on ST_Intersects(b.geom, "
                    "ST_MakeEnvelope(a.lon-0.02, a.lat-0.02, a.lon+0.02, a.lat+0.02))",
                    "m", "duckdb")
    assert radius.bbox_corners == {"ST_MAKEENVELOPE": "point_plus_offset"}


def test_the_bbox_check_skips_a_tessellation_and_keeps_a_radius():
    import inspect

    from dbt_assay.checks.structural import bbox_used_as_distance
    src = inspect.getsource(bbox_used_as_distance)
    assert "stored_bounds" in src and "continue" in src


def test_a_coalesce_default_is_extracted_so_its_share_can_be_counted():
    """*** "THIS TEST CANNOT FAIL" IS TRUE AND IS NOT THE ACTIONABLE SENTENCE. ***

    Measured on a real warehouse: 170,730 of 172,695 rows of `dwr_analysis_status` are the string
    'not looked up'. The test passes on every row while saying nothing about whether the lookup
    behind it ever ran -- the coalesce conflates "none" with "not measured".
    """
    assert default_literal("COALESCE(amount, 0)") == "0"
    assert default_literal("COALESCE(status, 'not looked up')") == "'not looked up'"
    assert default_literal("COALESCE(a, b)") is None, "a column tail is not a default"
    assert default_literal("") is None


def test_a_case_with_one_branch_cannot_fail_by_construction():
    """`case when max(email) is not null then 'brokerage_office' end` yields exactly one value or
    NULL. An accepted_values test on it cannot fail whatever the data does -- stronger than the
    general finding and previously indistinguishable from it."""
    assert cannot_fail_by_construction("CASE WHEN x IS NOT NULL THEN 'brokerage_office' END")
    assert not cannot_fail_by_construction("CASE WHEN a THEN 'x' WHEN b THEN 'y' ELSE 'z' END")
    assert not cannot_fail_by_construction("COALESCE(a, 0)")


def test_the_default_count_is_one_batched_statement():
    """The same batching `which_have_failures` already does, for the same reason."""
    sql = default_share_sql([("main.t", "a", "0"), ("main.u", "b", "'x'")])
    assert sql.count("union all") == 1
    assert "is not distinct from" in sql, "NULL must not silently count as the default"


def test_the_unclear_findings_carry_what_a_ruling_needs():
    """*** ALL SEVENTEEN UNCLEARS WERE ONE PROBLEM, AND THE MISSING PIECE WAS ALREADY COMPUTED. ***

    Which hop the collapse sits on. Which prose the contradiction is in.
    """
    import inspect

    from dbt_assay.judged import _collapse_note, _prose_judged
    assert "no group by, distinct or union was found" in inspect.getsource(_collapse_note)
    assert "schema_yml_description" in inspect.getsource(_prose_judged)


def test_the_mcp_practices_tool_actually_runs():
    """*** A TOOL NOTHING CALLS IS A TOOL NOTHING CHECKS. ***

    `primary_key_patches` grew a fifth element and `mcp_server.Backend.practices` still unpacked
    four, so the MCP call raised ValueError while the whole suite passed. Asserting on the source
    would have missed it the same way. This calls the tool.
    """
    from dbt_assay import mcp_server
    from dbt_assay import practices as prac

    b = mcp_server.Backend.__new__(mcp_server.Backend)
    b.state = lambda: SimpleNamespace(project=SimpleNamespace(tests=[]), entries=[])
    assert b.practices() == {"missing_uniqueness_tests": []}

    real = prac.primary_key_patches
    prac.primary_key_patches = lambda *_a: [("m", ["a"], "derived", 3, ["dropped_key"])]
    try:
        row = b.practices()["missing_uniqueness_tests"][0]
    finally:
        prac.primary_key_patches = real
    assert row["model"] == "m" and row["but_the_model_does_not_emit"] == ["dropped_key"]


def test_a_fanout_above_one_never_prints_as_one():
    """1.56x printed as `2x`, and the opposite rounding is worse: `1x` reads as 'it holds'."""
    from dbt_assay.practices import fanout

    assert fanout(73608, 47144) == "1.56x"
    assert fanout(2, 1) == "2x"
    assert fanout(1045, 7) == "149x"
    assert fanout(500, 500) == "1x"
    assert fanout(1_000_400, 1_000_000) != "1x", "a grain that does not hold must not read as one"
    assert fanout(5, 0) == "?"


def test_verify_grains_counts_a_model_in_a_custom_schema():
    """Everything in `main` counted; everything in `main_water` came back `(not counted)`."""
    from dbt_assay import practices as prac

    seen = []

    class _Probe:
        @staticmethod
        def run_sql(sql, *_a, **_k):
            seen.append(sql)
            return [{"m": "stg_water", "n": 149, "d": 1}]

    proj = SimpleNamespace(models={"model.p.stg_water": SimpleNamespace(name="stg_water")})
    sch = SimpleNamespace(relation={"model.p.stg_water": '"db"."main_water"."stg_water"'})
    held = prac.verify_grains([("stg_water", ["k"], "derived", 1, [])], proj, _Probe,
                              ".", None, "dbt", schema=sch)
    assert held == {"stg_water": (149, 1)}
    assert "db.main_water.stg_water" in seen[0], seen[0]


def test_a_locked_store_does_not_report_itself_as_a_missing_one():
    """The advice was "run any judged command once", which is the one thing that fails the same
    way. DuckDB is single-writer; that is the actual fact and it names the actual fix."""
    from dbt_assay.mcp_server import Backend

    b = Backend.__new__(Backend)
    b.store_path = None
    assert "nowhere to write" in b._store_or_why()[1]

    b.store_path = str(ROOT / "pyproject.toml")          # exists, is not a duckdb store
    got = b.rule("m", "q", "agree", "because")
    assert "nothing was recorded" in got["error"]

    class _Boom:
        def __init__(self, *_a):
            raise RuntimeError("Could not set lock on file: Conflicting lock is held")

    import dbt_assay.mcp_server as mod
    real = mod.Store
    mod.Store = _Boom
    try:
        why = b._store_or_why()[1]
    finally:
        mod.Store = real
    assert "LOCKED" in why and "one writer" in why
    assert "Run any judged command" not in why, "it told the reader to do the thing that fails"


def test_a_relayed_ruling_names_the_person_and_is_still_an_agent_ruling():
    """*** THE ONE FIELD AN AGENT FILLS IN ITSELF CANNOT BE THE ONE THAT DECIDES AUTHORITY. ***"""
    from dbt_assay.mcp_server import Backend

    wrote = {}

    class _Store:
        con = SimpleNamespace(execute=lambda *_a, **_k: SimpleNamespace(fetchone=lambda: None))

        def adjudicate(self, *a, **k):
            wrote.update(k)

        def ruled_subjects(self):
            return set()

        def agent_rulings(self):
            return []

        def close(self):
            pass

    b = Backend.__new__(Backend)
    b._store_or_why = lambda: (_Store(), "")
    got = b.rule("model.p.m::edge::x", "hop__multiplies", "disagree", "it is a union",
                 decided_by="Ryan")
    assert wrote["source"] == "agent", "a relayed ruling must never be filed as human"
    assert "Ryan" in wrote["who"]
    assert got["relayed_from"] == "Ryan" and "still filed as `agent`" in \
        got["and_still_an_agent_ruling"]


def test_the_review_queue_puts_read_findings_first_and_hides_nothing():
    """An agent could write a hundred rulings and never see whether one had been read."""
    from dbt_assay.mcp_server import Backend

    fs = [SimpleNamespace(check="a", subject="model.p.big", subject_name="big", file="b.sql",
                          summary="s", marts=30),
          SimpleNamespace(check="b", subject="model.p.read", subject_name="read", file="r.sql",
                          summary="s", marts=1),
          SimpleNamespace(check="c", subject="model.p.done", subject_name="done", file="d.sql",
                          summary="s", marts=99)]

    class _Store:
        @staticmethod
        def ruled_subjects():
            return {"model.p.done"}

        @staticmethod
        def agent_rulings():
            return [{"subject": "model.p.read", "verdict": "disagree", "note": "a union"}]

        @staticmethod
        def close():
            pass

    b = Backend.__new__(Backend)
    b.state = lambda: None
    b._store_or_why = lambda: (_Store(), "")
    from dbt_assay import live
    real = live.findings_for
    live.findings_for = lambda *_a, **_k: fs
    try:
        got = b.review_queue()
    finally:
        live.findings_for = real
    names = [r["model"] for r in got["waiting_for_a_person"]]
    assert "done" not in names, "a person already ruled on it"
    assert names[0] == "read", "a finding an agent has read is one keypress; it goes first"
    assert got["waiting_for_a_person"][0]["an_agent_already_said"]["because"] == "a union"
    assert got["already_ruled_by_a_person"] == 1


def test_an_empty_table_reads_the_same_way_in_both_commands():
    """*** `practices` PRINTED `holds: 0 rows, 0 distinct` FOR THE TABLE `patch` REFUSES. ***

    Same model, same run, opposite framings, and `holds` sat in the column a reader scans for
    green. One fact, two spellings: both callers now read `grain_verdict`.
    """
    from dbt_assay.patch import plan
    from dbt_assay.practices import grain_verdict

    assert grain_verdict((0, 0))[0] == "empty"
    assert grain_verdict(None)[0] == "uncounted"
    assert grain_verdict((10, 3))[0] == "fails"
    assert grain_verdict((10, 10))[0] == "holds"

    got = plan([("m", ["k"], "derived", 1, [])], {"m": (0, 0)}, "1.0", Path("/tmp"))
    assert got[0].sql == "" and "EMPTY" in got[0].skipped
    assert got[0].skipped == grain_verdict((0, 0))[1], "the two surfaces must say the same words"


def test_the_dbt_binary_flag_is_spelled_the_same_way_on_every_command():
    """It was `--dbt` on five commands and `--dbt-bin` on four, and `practices` FLIPPED between
    0.9.4 and 0.13.0 -- so a script written against one release breaks on the next.

    This enumerates the app rather than naming commands, because a guard with a hardcoded list is
    a guard that stops seeing the thing it was written for.
    """
    from dbt_assay.cli import app

    seen = 0
    for cmd in app.registered_commands:
        for param in getattr(cmd.callback, "__defaults__", None) or ():
            decls = set(getattr(param, "param_decls", ()) or ())
            if "--dbt" in decls or "--dbt-bin" in decls:
                seen += 1
                assert {"--dbt", "--dbt-bin"} <= decls, (
                    f"{cmd.callback.__name__} accepts only {decls & {'--dbt', '--dbt-bin'}}")
    assert seen >= 8, f"only found {seen} dbt-binary options; the introspection is broken"


def test_the_wrapper_hint_walks_up_to_the_repo_root(tmp_path):
    """`dbt_project.yml` in `transform/` and `uv.lock` at the root is the normal layout for a repo
    that is not only dbt. The hint looked beside `dbt_project.yml` and found nothing."""
    from dbt_assay.cli import _wrapper_hint

    (tmp_path / ".git").mkdir()
    (tmp_path / "uv.lock").write_text("")
    sub = tmp_path / "transform"
    sub.mkdir()
    (sub / "dbt_project.yml").write_text("name: x")
    hint = _wrapper_hint(sub)
    assert 'uv run dbt' in hint and "above the dbt project" in hint

    # It must not reach past the repo root: a lockfile out there is somebody else's project.
    outer = tmp_path / "inner"
    outer.mkdir()
    (outer / ".git").mkdir()
    assert _wrapper_hint(outer) == ""


# --- effectiveness: a verdict is about a VERSION of a question ------------------------------

def _store():
    from dbt_assay.store import Store
    return Store(":memory:")


def test_re_ruling_after_a_rewrite_is_kept_rather_than_overwriting():
    """*** THE ONE MEASUREMENT THAT SAYS WHETHER A REWRITE WORKED. ***

    The key was (subject, question), so ruling again REPLACED the row. `units` went 2/4 to 8/8
    across a rewrite and the store could not have told you, because the 2/4 was gone.
    """
    s = _store()
    s.adjudicate("m", "units__a", "units", "feet", "disagree", prompt_version="units.v1")
    s.adjudicate("m", "units__a", "units", "acres", "agree", prompt_version="units.v2")
    rows = {r["prompt_version"]: r for r in s.effectiveness()}
    assert set(rows) == {"units.v1", "units.v2"}, "the older verdict was overwritten"
    assert rows["units.v1"]["disagree"] == 1
    assert rows["units.v2"]["agree"] == 1


def test_a_disagreement_closes_only_when_somebody_agrees_at_another_version():
    """It falls when a question changed and a person re-read it. A release cannot lower it."""
    s = _store()
    s.adjudicate("m", "q__a", "fam", "x", "disagree", prompt_version="v1")
    assert s.effectiveness()[0]["open_disagreements"] == 1

    s.adjudicate("m", "q__a", "fam", "x", "agree", prompt_version="v1")   # same version
    v1 = next(r for r in s.effectiveness() if r["prompt_version"] == "v1")
    assert v1["open_disagreements"] == 0 or v1["disagree"] == 0, "same-version re-rule replaces"

    s2 = _store()
    s2.adjudicate("n", "q__a", "fam", "x", "disagree", prompt_version="v1")
    s2.adjudicate("n", "q__a", "fam", "y", "agree", prompt_version="v2")
    assert sum(r["open_disagreements"] for r in s2.effectiveness()) == 0


def test_unclear_is_never_in_the_agreement_denominator():
    """Disagreement is wrong criteria. Unclear is a state that cannot carry the answer, and the
    two need different repairs. Seventeen unclears on one warehouse were all the second kind."""
    s = _store()
    s.adjudicate("a", "q__1", "fam", "x", "agree", prompt_version="v1")
    s.adjudicate("b", "q__2", "fam", "x", "unclear", prompt_version="v1")
    s.adjudicate("c", "q__3", "fam", "x", "unclear", prompt_version="v1")
    r = s.effectiveness()[0]
    assert r["agreement"] == 1.0, "three unclears must not read as a 33% agreement rate"
    assert r["unclear"] == 2 and r["n"] == 3


def test_the_gate_count_is_subjects_read_not_keys_pressed():
    """Versioning the key must not let one subject ruled twice look like two verdicts."""
    s = _store()
    s.adjudicate("m", "q__a", "fam", "x", "agree", prompt_version="v1")
    s.adjudicate("m", "q__a", "fam", "x", "agree", prompt_version="v2")
    s.adjudicate("n", "q__a", "fam", "x", "agree", prompt_version="v2")
    assert s.adjudication_counts()["fam"] == 2, "two subjects were read, not three"


def test_regress_anchors_on_the_latest_verdict_not_a_withdrawn_one():
    """Agreed at v1, disagreed at v2. Taking both would fail a build over an answer nobody stands
    behind any more, which is worse than having no baseline."""
    s = _store()
    s.adjudicate("m", "q__a", "fam", "old_answer", "agree", prompt_version="v1")
    import time
    time.sleep(0.01)
    s.adjudicate("m", "q__a", "fam", "old_answer", "disagree", prompt_version="v2")
    assert s.confirmed() == [], "the withdrawn agreement is still anchoring regress"


def test_an_unmeasured_agreement_rate_cannot_refuse_anything():
    """An absent measurement must never read as a failing one. It is the rule this codebase keeps
    relearning, and here it would silently downgrade every judged gate on a fresh store."""
    from dbt_assay.config import QuestionConfig, Threshold

    c = QuestionConfig(name="x", act={"fail": Threshold("p > 0.5")})
    ans = {"kind": "noul", "answer": "0.9"}
    assert c.action_for(ans, 99, 20, agreement=None, min_agreement=0.9) == "fail"
    assert c.action_for(ans, 99, 20, agreement=0.4, min_agreement=0.9) == "queue"
    assert c.action_for(ans, 99, 20, agreement=0.4, min_agreement=0.0) == "fail"


def test_a_structural_ruling_carries_assay_s_own_version():
    """*** 99 OF 107 RULINGS ON A REAL STORE WERE STRUCTURAL. ***

    No question is asked, so there is no prompt to version -- and every one of them would have
    read `(unversioned)`, with no before-and-after possible until new rulings came in. The version
    of a structural check IS assay's, because it changed when the check changed. The two families
    0.12.0 fixed were the two sitting at 0% agreement and nothing could have said so.
    """
    import dbt_assay
    from dbt_assay.mcp_server import Backend

    wrote = {}

    class _Store:
        con = SimpleNamespace(execute=lambda *_a, **_k: SimpleNamespace(fetchone=lambda: None))

        def adjudicate(self, *a, **k):
            wrote.update(k)

        def ruled_subjects(self):
            return set()

        def agent_rulings(self):
            return []

        def close(self):
            pass

    b = Backend.__new__(Backend)
    b._store_or_why = lambda: (_Store(), "")
    b.rule("model.p.m", "hop_multiplies_rows", "disagree", "it is a union")
    assert wrote["prompt_version"] == f"assay.{dbt_assay.__version__}"


def test_the_rate_gate_ignores_verdicts_about_an_older_version_of_the_check():
    """Verdicts about v1 are evidence about v1. Counting them for v4 is the same error as letting
    an agent ruling count as a person's: the number is real and it is about something else."""
    s = _store()
    s.adjudicate("a", "hop", "hop_multiplies_rows", "x", "disagree", prompt_version="assay.0.11.0")
    s.adjudicate("b", "hop", "hop_multiplies_rows", "x", "agree", prompt_version="assay.0.12.0")
    got = s.accuracy_by_family({}, default="assay.0.12.0")
    assert got["hop_multiplies_rows"] == (1.0, 1), "an older release's verdicts leaked in"


# --- a join onto a unique key cannot fan out ------------------------------------------------

def _entry(**kw):
    from dbt_assay.inventory import ModelEntry
    e = ModelEntry(uid="model.p.child", name="child", path="c.sql", layer="marts",
                   materialized="table")
    e.marts = 9
    e.fanout_hops = [("lookup -> child", 0.81)]
    e.join_keys = {"lookup": ["abbrev"]}
    for k, v in kw.items():
        setattr(e, k, v)
    return e


def test_a_hop_onto_a_declared_unique_key_is_refused_without_touching_the_warehouse():
    """dbt already says which keys are declared unique, and that half costs nothing."""
    from dbt_assay.judged import hop_multiplies_rows

    assert len(hop_multiplies_rows(None, [_entry()])) == 1
    assert hop_multiplies_rows(None, [_entry(unique_key_parents={"lookup"})]) == []


def test_a_key_that_counts_unique_retires_the_hop_and_one_that_cannot_be_counted_does_not():
    """*** assay BELIEVED THE PROJECT INSTEAD OF THE WAREHOUSE. ***

    Two of twelve disagreements were a LEFT JOIN onto a lookup that IS unique and carries no
    uniqueness test: `int_water_streamflow_summary`, 2,387 rows over 2,387 distinct `abbrev`.
    And an uncounted key must never read as a unique one.
    """
    from dbt_assay import practices as prac

    proj = SimpleNamespace(models={"model.p.lookup": SimpleNamespace(name="lookup")})

    class _Unique:
        @staticmethod
        def run_sql(*_a, **_k):
            return [{"m": "lookup", "n": 2387, "d": 2387}]

    e = _entry()
    assert prac.verify_join_keys([e], proj, _Unique, ".", None, "dbt") == 1
    assert e.unique_key_parents == {"lookup"}

    class _NotUnique:
        @staticmethod
        def run_sql(*_a, **_k):
            return [{"m": "lookup", "n": 2387, "d": 40}]

    e2 = _entry()
    assert prac.verify_join_keys([e2], proj, _NotUnique, ".", None, "dbt") == 0
    assert e2.unique_key_parents == set()

    class _Dead:
        @staticmethod
        def run_sql(*_a, **_k):
            return []

    e3 = _entry()
    assert prac.verify_join_keys([e3], proj, _Dead, ".", None, "dbt") == 0
    assert e3.unique_key_parents == set(), "an uncounted key is not a unique one"


def test_json_output_survives_the_verify_message(project_dir, tmp_path, monkeypatch):
    """*** `--json` IS MACHINE-READABLE AND ONE LINE OF PROSE ENDS THAT. ***

    The retire message printed before the document and every parser downstream got
    `Expecting value: line 1 column 1`. Same class as rich eating `[mcp]` out of the instruction
    telling somebody to install it. This PARSES the output rather than grepping the source, which
    is the only version that would have caught it.
    """
    import json as _j

    from typer.testing import CliRunner

    from dbt_assay import practices as prac
    from dbt_assay.cli import app
    from dbt_assay.store import Store

    store = tmp_path / "s.duckdb"
    Store(store).close()
    # Force the retire path: any non-zero count must still leave the document parseable.
    monkeypatch.setattr(prac, "verify_join_keys", lambda *a, **k: 3)

    got = CliRunner().invoke(app, ["check", "--target", str(project_dir), "--store", str(store),
                                   "--json", "--verify", "--project-dir", str(tmp_path)])
    assert got.exit_code == 0, got.output
    doc = _j.loads(got.stdout)
    assert "findings" in doc and "verified" in doc
