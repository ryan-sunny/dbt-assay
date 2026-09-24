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
from dbt_assay.probe import Result

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
            return Result(rows=[{"m": "stg_water", "n": 149, "d": 1}])

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
    got = b.rule("agree", "because", subject="m", question="q")
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

        def ruled_pairs(self):
            # What a verdict actually COVERS. The queue used to skip on the subject, which hid
            # every other check on a model somebody had ruled one check of.
            return set()

        def ruled_subjects(self):
            return set()

        def agent_rulings(self):
            return []

        def close(self):
            pass

    b = Backend.__new__(Backend)
    b._store_or_why = lambda: (_Store(), "")
    b._resolve_subject = lambda subj, q, st: (subj, "a decision key", "")
    got = b.rule("disagree", "it is a union", subject="model.p.m::edge::x",
                 question="hop__multiplies", decided_by="Ryan")
    assert wrote["source"] == "agent", "a relayed ruling must never be filed as human"
    assert "Ryan" in wrote["who"]
    assert got["relayed_from"] == "Ryan" and "still filed as `agent`" in \
        got["and_still_an_agent_ruling"]


def test_the_review_queue_puts_read_findings_first_and_hides_nothing():
    """An agent could write a hundred rulings and never see whether one had been read."""
    from dbt_assay.mcp_server import Backend

    fs = [SimpleNamespace(check="a", subject="model.p.big", subject_name="big", file="b.sql",
                          summary="s", marts=30, id="idbig"),
          SimpleNamespace(check="b", subject="model.p.read", subject_name="read", file="r.sql",
                          summary="s", marts=1, id="idread"),
          SimpleNamespace(check="c", subject="model.p.done", subject_name="done", file="d.sql",
                          summary="s", marts=99, id="iddone"),
          # *** A SECOND CHECK ON THE MODEL SOMEBODY RULED ONE CHECK OF. ***
          # A verdict covers (subject, question). Skipping on the subject dropped this one from
          # the queue whose entire job is showing what is still waiting -- silently, by omission.
          SimpleNamespace(check="other", subject="model.p.done", subject_name="done",
                          file="d.sql", summary="s", marts=99, id="idother")]

    class _Store:
        @staticmethod
        def ruled_subjects():
            return {"model.p.done"}

        @staticmethod
        def ruled_pairs():
            return {("model.p.done", "c")}

        @staticmethod
        def agent_rulings():
            return [{"subject": "model.p.read", "question": "b", "verdict": "disagree",
                     "note": "a union"}]

        @staticmethod
        def close():
            pass

    b = Backend.__new__(Backend)
    b.state = lambda: SimpleNamespace(project=SimpleNamespace(
        models={"model.p.big": 1, "model.p.read": 1, "model.p.done": 1}))
    b._store_or_why = lambda: (_Store(), "")
    from dbt_assay import live
    real = live.findings_for
    live.findings_for = lambda *_a, **_k: fs
    try:
        got = b.review_queue()
    finally:
        live.findings_for = real
    names = [r["model"] for r in got["waiting_for_a_person"]]
    assert names[0] == "read", "a finding an agent has read is one keypress; it goes first"
    assert got["waiting_for_a_person"][0]["an_agent_already_said"]["because"] == "a union"
    assert got["already_ruled_by_a_person"] == 1

    # *** THIS ASSERTION USED TO READ `"done" not in names`, AND THAT WAS THE BUG. ***
    # It encoded skipping on the SUBJECT, so the test held the over-skip in place: a model with
    # one check ruled dropped out of the queue entirely, taking its unanswered checks with it.
    keys = {(r["model"], r["check"]) for r in got["waiting_for_a_person"]}
    assert ("done", "c") not in keys, "a pair somebody ruled came back"
    assert ("done", "other") in keys, \
        "a DIFFERENT check on that model vanished, unanswered, from the waiting list"

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

        def ruled_pairs(self):
            # What a verdict actually COVERS. The queue used to skip on the subject, which hid
            # every other check on a model somebody had ruled one check of.
            return set()

        def ruled_subjects(self):
            return set()

        def agent_rulings(self):
            return []

        def close(self):
            pass

    b = Backend.__new__(Backend)
    b._store_or_why = lambda: (_Store(), "")
    b._resolve_subject = lambda subj, q, st: (subj, "a model unique_id", "")
    b.rule("disagree", "it is a union", subject="model.p.m", question="hop_multiplies_rows")
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
            return Result(rows=[{"m": "lookup", "n": 2387, "d": 2387}])

    e = _entry()
    assert prac.verify_join_keys([e], proj, _Unique, ".", None, "dbt") == 1
    assert e.unique_key_parents == {"lookup"}

    class _NotUnique:
        @staticmethod
        def run_sql(*_a, **_k):
            return Result(rows=[{"m": "lookup", "n": 2387, "d": 40}])

    e2 = _entry()
    assert prac.verify_join_keys([e2], proj, _NotUnique, ".", None, "dbt") == 0
    assert e2.unique_key_parents == set()

    class _Dead:
        @staticmethod
        def run_sql(*_a, **_k):
            return Result(failed=True, why="could not reach the warehouse")

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


# --- item 3: grouping assay's own rejected findings -------------------------------------------

def _dis_store(rows):
    from dbt_assay.store import Store
    s = Store(":memory:")
    for i, (fam, note) in enumerate(rows):
        s.adjudicate(f"m{i}", f"q{i}", fam, "x", "disagree", note=note,
                     source="agent", prompt_version="assay.0.15.0")
    return s


def test_code_groups_identical_reasons_before_anything_is_asked():
    """*** THE STRUCTURAL TIER FIRST, HERE TOO. ***

    Eight of ten reasons on a real store opened with the same sentence. Code grouped those for
    nothing, and the pairs it settled are never sent: if a parser can answer it, Jev is not asked.
    """
    from dbt_assay.subjects import candidate_pairs, normalize_reason

    assert normalize_reason("A UNION MEMBER cannot multiply. Several of these aggregate.") == \
           normalize_reason("a union member cannot multiply! and then something else entirely")

    s = _dis_store([("hop", "Union member. Detail one."),
                    ("hop", "Union member! Detail two, quite different."),
                    ("hop", "The join key is unique in the data.")])
    try:
        pairs = candidate_pairs(s)
    finally:
        s.close()
    asked = {(a["family"], normalize_reason(a["note"]), normalize_reason(b["note"]))
             for _k, a, b in pairs}
    assert len(pairs) == 2, "the two identical-first-sentence rulings were sent to be judged"
    assert all(x[1] != x[2] for x in asked)


def test_a_ruling_pair_subject_carries_the_two_reasons_and_nothing_else():
    """A claim alone read 0.96 and the same claim plus one CORRECT extra sentence read 0.47.
    This subject is not a dbt object and has no blast radius to rank by."""
    from dbt_assay.subjects import SubjectSource, build

    s = _dis_store([("hop", "First reason here."), ("hop", "A different second reason.")])
    try:
        subs = build("ruling_pair", SubjectSource(store=s))
    finally:
        s.close()
    assert len(subs) == 1
    assert set(subs[0].state) == {"check_or_question_both_rulings_are_about",
                                  "first_reason", "second_reason"}


def test_a_disagreement_somebody_has_since_agreed_with_is_not_open():
    """It closes when the check or the question CHANGED and a person re-read it. Nothing else."""
    from dbt_assay.subjects import open_disagreements

    s = _dis_store([("hop", "Union member.")])
    try:
        assert len(open_disagreements(s)) == 1
        s.adjudicate("m0", "q0", "hop", "x", "agree", note="fixed in 0.16",
                     source="human", prompt_version="assay.0.16.0")
        assert open_disagreements(s) == []
    finally:
        s.close()


def test_grouping_writes_no_verdict_of_its_own(tmp_path):
    """*** IT NEVER CLOSES ANYTHING. *** A release that can resolve its own disagreements makes
    every number downstream of them decoration."""
    from typer.testing import CliRunner

    from dbt_assay.cli import app
    from dbt_assay.store import Store

    path = tmp_path / "s.duckdb"
    s = Store(path)
    s.adjudicate("m0", "q0", "hop", "x", "disagree", note="Union member.", source="agent")
    s.adjudicate("m1", "q1", "hop", "x", "disagree", note="Union member!", source="agent")
    before = s.con.execute("select count(*) from adjudications").fetchone()[0]
    s.close()

    got = CliRunner().invoke(app, ["disagreements", "--store", str(path), "--json"])
    assert got.exit_code == 0, got.output
    import json as _j
    groups = _j.loads(got.stdout)
    assert len(groups) == 1 and groups[0]["size"] == 2
    assert groups[0]["every_ruling_is_an_agent_ruling"] is True

    s = Store(path)
    try:
        assert s.con.execute("select count(*) from adjudications").fetchone()[0] == before
    finally:
        s.close()


# --- rule() must resolve or refuse, and a finding is finer than a model --------------------

def test_rule_refuses_a_subject_that_would_join_to_nothing():
    """*** IT ACCEPTED A BARE NAME, ANSWERED `recorded: true`, AND WROTE 99 ORPHANS. ***

    `findings.subject` is `model.sunny_data.int_azcc_owners`; `rule` was handed
    `int_azcc_owners`. One fact, two spellings, silent when they disagree -- the ninth instance,
    in the write path of the feature built to close the loop. A write that cannot be joined back
    is not a write, and reporting it as one is worse than failing.
    """
    from dbt_assay.mcp_server import Backend

    b = Backend.__new__(Backend)
    b.state = lambda: SimpleNamespace(project=SimpleNamespace(
        models={"model.p.thing": SimpleNamespace(name="thing")}))

    class _Store:
        con = SimpleNamespace(execute=lambda *_a, **_k: SimpleNamespace(fetchone=lambda: None))

        def close(self):
            pass

    st = _Store()
    key, how, err = b._resolve_subject("model.p.thing", "q", st)
    assert key == "model.p.thing" and not err

    key, how, err = b._resolve_subject("thing", "q", st)
    assert key == "model.p.thing" and "resolved from the bare name" in how and not err

    key, how, err = b._resolve_subject("no_such_model", "q", st)
    assert not key and "nothing was recorded" in err and "review_queue" in err


def test_a_finding_id_is_stable_across_runs_and_finer_than_its_model():
    """*** A VERDICT ON A MODEL LANDS ON EVERY FINDING THAT MODEL HAS. ***

    One real model carries eight `test_cannot_fail` findings. It is also how a CORRECT finding was
    ruled wrong: `dim_business` is a union false positive on four of its six edges and a measured
    1.48x fan-out on the other two, and a model-level verdict covered all six.
    """
    from dbt_assay.checks.structural import Finding

    def f(**kw):
        base = {"check": "test_cannot_fail", "subject": "model.p.m", "subject_name": "m",
                "file": "m.sql", "summary": "s", "detail": "d"}
        return Finding(**{**base, **kw})

    assert f(evidence={"column": "a"}).id != f(evidence={"column": "b"}).id
    assert f(summary="one").id != f(summary="two").id
    assert f(check="arbitrary_pick").id != f().id

    # *** A PROBABILITY MOVES EVERY RUN, AND HASHING IT WOULD ORPHAN EVERY RULING. ***
    # Which is the exact bug the id exists to fix, reintroduced one layer down.
    a = f(evidence={"hop": "x -> y", "probability": 0.81, "marts": 3})
    b = f(evidence={"hop": "x -> y", "probability": 0.77, "marts": 9})
    assert a.id == b.id, "the handle moved because a measurement moved"


def test_repair_resolves_a_bare_name_and_refuses_an_ambiguous_one(tmp_path, monkeypatch):
    """It resolves and never guesses. A ruling moved to the WRONG model is worse than an orphaned
    one, because it would look attached."""
    from types import SimpleNamespace as NS

    from dbt_assay import cli
    from dbt_assay.store import Store

    s = Store(tmp_path / "s.duckdb")
    s.adjudicate("thing", "chk", "chk", "x", "disagree", note="n", source="agent")
    s.adjudicate("twice", "chk", "chk", "x", "disagree", note="n", source="agent")
    s.adjudicate("ghost", "chk", "chk", "x", "disagree", note="n", source="agent")

    project = NS(models={"model.p.thing": NS(name="thing"),
                         "model.a.twice": NS(name="twice"), "model.b.twice": NS(name="twice")},
                 sources={}, raw={})
    monkeypatch.setattr(cli, "_find_target", lambda _t: "target")
    monkeypatch.setattr(cli, "_load", lambda *_a, **_k: (project, {}, [], None, None))
    cli._repair_subjects(s, "target", None)

    got = {r[0] for r in s.con.execute("select subject from adjudications").fetchall()}
    s.close()
    assert "model.p.thing" in got, "the unambiguous name was not repaired"
    assert "twice" in got, "an ambiguous name must be left exactly as it is"
    assert "ghost" in got, "a name matching no model must be left exactly as it is"


def test_the_cli_and_mcp_see_the_same_findings(project_dir, tmp_path):
    """*** THE CLI SAW SEVEN FAMILIES AND MCP SAW TWO. ***

    A run held 162 findings across 7 families and `findings()` returned 20 across 2.
    `hop_multiplies_rows` (58) and `description_contradicts_the_code` (18) were absent entirely,
    reachable only by querying the store by hand -- and those are the two worth an agent's time.

    `check` added the judged stream itself and `findings_for` never did: two paths computing one
    fact, which is the shape this codebase has now found ten times. This runs BOTH surfaces and
    compares them, because a guard that reads the source would not have caught the original.
    """
    import json as _j

    from typer.testing import CliRunner

    from dbt_assay import live
    from dbt_assay.cli import app
    from dbt_assay.mcp_server import Backend
    from dbt_assay.store import Store

    store = tmp_path / "s.duckdb"
    Store(store).close()

    got = CliRunner().invoke(app, ["check", "--target", str(project_dir),
                                   "--store", str(store), "--json"])
    assert got.exit_code == 0, got.output
    from_cli = {(f["check"], f["model"], f["summary"]) for f in _j.loads(got.stdout)["findings"]}

    b = Backend(str(project_dir), str(store))
    every = live.findings_for(b.state(), None)
    from_mcp = {(f.check, f.subject_name, f.summary) for f in every}

    assert from_cli, "the fixture produced no findings; the comparison proves nothing"
    assert from_cli == from_mcp, (
        f"only the CLI sees {sorted(x[0] for x in from_cli - from_mcp)}; "
        f"only MCP sees {sorted(x[0] for x in from_mcp - from_cli)}")


def test_findings_says_what_it_is_not_showing(project_dir, tmp_path):
    """A surface that returns a subset and does not say so is the same bug as a scanner that
    matches nothing and reports a pass."""
    from dbt_assay.mcp_server import Backend
    from dbt_assay.store import Store

    store = tmp_path / "s.duckdb"
    Store(store).close()
    b = Backend(str(project_dir), str(store))

    got = b.findings(limit=1)
    assert got["every_check_in_this_project"], "the breakdown is empty; the reader is broken"
    total = sum(got["every_check_in_this_project"].values())
    assert got["showing"] == f"1 of {total}"
    if total > 1:
        assert "what_you_are_not_seeing" in got

    one = next(iter(got["every_check_in_this_project"]))
    only = b.findings(check=one, limit=99)
    assert {f["check"] for f in only["findings"]} == {one}


# --- completeness: coverage of what the project itself declares -----------------------------

def test_a_source_nothing_reads_and_one_only_a_test_reads_are_different_findings():
    """5 and 2 on a real warehouse. One says nothing consumes it; the other says you are paying
    to TEST data nothing consumes, which is weaker and real."""
    from types import SimpleNamespace as NS

    from dbt_assay.checks import sources as sc

    src = NS(source_name="raw", name="t", schema="s", columns={})
    project = NS(sources={"source.p.raw.t": src},
                 raw={"child_map": {}, "nodes": {}})
    assert len(sc.source_reaches_nothing(project)) == 1
    assert sc.source_only_a_test_reads(project) == []

    project.raw["child_map"] = {"source.p.raw.t": ["test.p.x"]}
    assert sc.source_reaches_nothing(project) == []
    assert len(sc.source_only_a_test_reads(project)) == 1

    project.raw["child_map"] = {"source.p.raw.t": ["model.p.y"]}
    assert sc.source_reaches_nothing(project) == []
    assert sc.source_only_a_test_reads(project) == []


def test_freshness_defers_to_the_evaluator_when_it_is_installed():
    """Printing the same finding twice is worse than not printing it: a reader cannot tell
    whether two tools agree or whether one is echoing the other."""
    from types import SimpleNamespace as NS

    from dbt_assay.checks import sources as sc

    src = NS(source_name="raw", name="t", schema="s", columns={})
    project = NS(sources={"source.p.raw.t": src},
                 raw={"child_map": {}, "sources": {"source.p.raw.t": {}},
                      "nodes": {"model.p.a": {"package_name": "mine"}}})
    assert len(sc.source_freshness_undeclared(project)) == 1

    project.raw["nodes"]["model.e.x"] = {"package_name": sc.EVALUATOR}
    assert sc.source_freshness_undeclared(project) == [], "it echoed the evaluator"


def test_a_hop_that_declares_why_it_drops_rows_is_not_a_candidate():
    """*** MOST EDGES DROP ROWS ON PURPOSE. ***

    A raw ratio fires on half a DAG on day one. Seven of the first eight findings on a real
    warehouse were a parent the child had already COLLAPSED in a subquery -- `pre_aggregated`,
    sitting on the entry, naming that exact parent. The refusals ship with the check, not after.
    """
    from dbt_assay.inventory import ModelEntry
    from dbt_assay.practices import row_loss_candidates

    def e(**kw):
        x = ModelEntry(uid="model.p.c", name="c", path="c.sql", layer="marts",
                       materialized="table")
        x.join_keys = {"p": ["k"]}
        for k, v in kw.items():
            setattr(x, k, v)
        return x

    assert len(row_loss_candidates([e()])) == 1
    assert row_loss_candidates([e(filters_rows=True)]) == []
    assert row_loss_candidates([e(aggregates=True)]) == []
    assert row_loss_candidates([e(unreadable=True)]) == []
    assert row_loss_candidates([e(pre_aggregated_parents={"p": ["k"]})]) == [], \
        "the child already collapsed that parent; the drop is declared"


def test_an_uncounted_hop_produces_no_finding_and_a_share_never_reads_as_zero():
    """An absent measurement is not a pass, and `keeps 0%` for 13,694 rows sends somebody looking
    for an empty table."""
    from dbt_assay.inventory import ModelEntry
    from dbt_assay.practices import hop_drops_most_rows, share

    x = ModelEntry(uid="model.p.c", name="c", path="c.sql", layer="marts", materialized="table")
    x.join_keys = {"p": ["k"]}
    assert hop_drops_most_rows(None, [x]) == [], "an uncounted hop must produce nothing"

    x.row_loss = {"p": (3_156_986, 13_694)}
    got = hop_drops_most_rows(None, [x], 0.8)
    assert len(got) == 1 and "0%" not in got[0].summary.split("of")[0]

    assert share(13_694, 3_156_986) == "0.4%"
    assert share(0, 100) == "0%"
    assert share(1, 10_000_000) not in ("0%", "0.0%")
    assert share(5, 0) == "?"


def test_the_check_scanner_sees_every_module_that_builds_a_finding():
    """*** A SCANNER THAT FINDS MOST THINGS PASSES ITS OWN FLOOR. ***

    `known_checks()` read three modules. `practices` and `checks.sources` started constructing
    findings in 0.18.0, so five real checks came back UNKNOWN and `assay config` would have told
    somebody their `hop_drops_most_rows: {action: annotate}` configured nothing. The existing
    floor caught a reader that finds NOTHING and could not catch one that finds most things.

    This walks the package instead of naming modules.
    """
    import ast

    import dbt_assay
    from dbt_assay.config import known_checks

    known = known_checks()
    builds: dict = {}
    root = Path(dbt_assay.__file__).parent
    for f in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(f.read_text())):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Finding"):
                continue
            c = {k.arg: k.value for k in node.keywords}.get("check")
            if isinstance(c, ast.Constant):
                builds.setdefault(c.value, f.name)
    assert len(builds) >= 15, f"only found {len(builds)} construction sites; the reader is broken"
    missed = {c: where for c, where in builds.items() if c not in known}
    assert not missed, f"known_checks() cannot see these, so audit.yml calls them unknown: {missed}"


def test_the_shipped_audit_yml_names_the_completeness_checks():
    """A check nobody can find in the config is a check nobody tunes."""
    import yaml

    from dbt_assay.config import DEFAULT_YML, Config

    cfg = Config.from_dict(yaml.safe_load(DEFAULT_YML))
    assert cfg.unknown_questions == [], f"the shipped template has dead keys: {cfg.unknown_questions}"
    for c in ("source_reaches_nothing", "source_only_a_test_reads", "source_freshness_stale",
              "hop_drops_most_rows"):
        assert cfg.questions.get(c) and cfg.questions[c].action == "annotate", c


def test_the_skill_works_without_the_mcp_server():
    """*** SKILLS ARE THE OTHER WAY IN, AND THE SKILL ONLY DOCUMENTED TOOLS. ***

    An agent with the skill file and no MCP connection had a procedure it could not perform. Every
    tool needs a command that answers the same question, and the CLI's `--json` carries the same
    `finding` ids so `rule` works either way.
    """
    from dbt_assay.mcp_server import TOOLS
    from dbt_assay.skilltext import SKILL_MD

    i = SKILL_MD.find("## Without the MCP server")
    assert i > 0, "the skill has no CLI path at all"
    table = SKILL_MD[i:SKILL_MD.find("\n## ", i + 10)]
    # Tools that are project-wide bookkeeping rather than part of the edit procedure.
    exempt = {"rebase", "practices", "changed_contracts"}
    missing = [n for n, _d in TOOLS if n not in exempt and f"`{n}(" not in table]
    assert not missing, f"no command given for: {missing}"
    assert table.count("assay ") >= 8, "the reader is broken; it found almost no commands"


def test_the_skill_names_the_commands_that_ship_now():
    """A procedure that does not mention a tier is a tier the agent never runs."""
    from dbt_assay.skilltext import SKILL_MD

    for cmd in ("assay completeness", "assay effectiveness", "assay disagreements", "assay page"):
        assert cmd in SKILL_MD, f"{cmd} is not in the agent procedure"
    assert "cannot say whether that is bad" in SKILL_MD, \
        "the skill does not tell the agent completeness findings are coverage, not defects"


# --- the page ------------------------------------------------------------------------------

def _page(**over):
    from dbt_assay.render import page_html
    base = {"project": "p", "models": 3, "generated_at": "2026-01-01", "version": "0.0.0",
            "ruled": 0, "findings_total": 2, "agent_rulings": 0, "effectiveness": [],
            "by_check": [("test_cannot_fail", 2, 5, 0)],
            "completeness": [("models assay could not read", 1, "not audited")],
            "moved": {}, "plain": False,
            "grain": {"declared": 1, "derived": 1, "judged": 0, "none": 1},
            "no_unique_test": 2, "claims": {}, "not_counted_note": "",
            "top_findings": [{"check": "test_cannot_fail", "model": "m", "summary": "s",
                              "marts": 5}], "shown": 1}
    return page_html({**base, **over})


def test_the_record_has_one_style_and_nothing_to_explain():
    """*** THERE USED TO BE TWO STYLESHEETS, AND THE LOUD ONE BECAME AN ACCIDENT. ***

    A report somebody has to explain before a colleague reads it does not get forwarded, which is
    why the sober variant existed. Once the record moved inside the explorer as a tab, the loud
    one was the only styled surface on a page of plain tables and read as a mistake rather than
    as emphasis. Reported from the field in exactly those words. So there is one style now, the
    `plain` flag no longer selects between them, and this asserts the loud one is gone rather
    than merely unused.
    """
    import re

    from dbt_assay import render

    assert not hasattr(render, "PAGE_CSS"), "the loud stylesheet is still shipping"
    a, b = _page(), _page(plain=True)
    assert re.findall(r"<h2>([^<]+)</h2>", a) == re.findall(r"<h2>([^<]+)</h2>", b)
    assert "repeating-radial-gradient" not in a and "repeating-radial-gradient" not in b
    for doc in (a, b):
        assert "{" not in re.sub(r"(?s)<style>.*?</style>", "", doc), "a template field leaked"


def test_the_page_never_adds_a_declared_grain_to_a_judged_one():
    """A grain a person wrote down and one a judgment reached at 0.53 are not the same fact."""
    doc = _page(grain={"declared": 7, "derived": 11, "judged": 3, "none": 5})
    for n in ("7", "11", "3", "5"):
        assert f">{n}</b>" in doc or f">{n}<" in doc, n
    assert ">26<" not in doc and ">21<" not in doc, "it printed a total"


def test_an_empty_section_says_why_rather_than_showing_a_zero():
    """*** AN ABSENT MEASUREMENT IS NOT A PASS, ON THE PAGE TOO. ***"""
    doc = _page(claims={})
    assert "assay claims --extract" in doc and "it has not been asked" in doc
    doc2 = _page(claims={"total": 9, "supported": 7, "contradicted": 2})
    assert "assay claims --extract" not in doc2 and ">9</b>" in doc2

    doc3 = _page(effectiveness=[])
    assert "No verdicts recorded yet" in doc3 and "no question may fail a build" in doc3


def test_the_page_is_deterministic(project_dir, tmp_path):
    """It carries the manifest's `generated_at` and never a wall clock. A page that churns on
    every run cannot be committed, and one that cannot be committed cannot show what moved."""
    from typer.testing import CliRunner

    from dbt_assay.cli import app

    a, b = tmp_path / "a.html", tmp_path / "b.html"
    for out in (a, b):
        r = CliRunner().invoke(app, ["page", str(out), "--target", str(project_dir),
                                     "--store", str(tmp_path / "nope.duckdb")])
        assert r.exit_code == 0, r.output
    assert a.read_text() == b.read_text(), "the page churns between identical runs"
    assert "20" in a.read_text()


# --- round five: two blind spots found by using it -------------------------------------------

def test_a_source_can_declare_a_reader_that_lives_outside_dbt():
    """*** A MANIFEST CHECK CANNOT SEE A PYTHON READER, AND THE OBVIOUS ACTION IS TO DELETE. ***

    `enriched_wells.well_documents` came back as read by nothing, which is true of the dbt graph
    and false of the warehouse: `enrichment/well_scans.py` reads it. Its own description already
    said so and nothing could act on prose.
    """
    from types import SimpleNamespace as NS

    from dbt_assay.checks import sources as sc

    src = NS(source_name="enriched", name="well_documents", schema="s", columns={})
    project = NS(sources={"source.p.enriched.well_documents": src},
                 raw={"child_map": {}, "nodes": {},
                      "sources": {"source.p.enriched.well_documents": {}}})
    got = sc.source_reaches_nothing(project)
    assert len(got) == 1
    assert "in this dbt project" in got[0].summary, "it claimed more than it checked"
    assert "Do not delete" in got[0].detail and sc.READ_BY in got[0].detail

    project.raw["sources"]["source.p.enriched.well_documents"] = {
        "meta": {sc.READ_BY: "enrichment/well_scans.py"}}
    assert sc.source_reaches_nothing(project) == []
    project.raw["sources"]["source.p.enriched.well_documents"] = {
        "config": {"meta": {sc.READ_BY: ["a.py", "b.py"]}}}
    assert sc.source_reaches_nothing(project) == []


def test_row_loss_is_judged_only_on_the_driving_edge_of_an_inner_join():
    """*** A LEFT JOIN CANNOT LOSE ROWS, AND A LOOKUP'S SIZE SAYS NOTHING ABOUT THE CHILD'S. ***

    `mart_acquisition_targets` drives on `int_acquisition_targets` (13,694 rows) and LEFT JOINs
    `dim_owner` (3.1M). The child was never going to be 3.1M rows and nothing is wrong.

    The first fix was a sibling-size heuristic: refuse when the child is the size of SOME parent.
    It gave the right answer on both field cases and it masked the real defect, because the models
    that would trip this legitimately narrow in a sibling CTE and look exactly the same. The
    parser already knew the join kind and the FROM clause, which settle it without a coincidence
    of sizes.

    *** THIS IS ALSO THE NEGATIVE CONTROL THE FIELD CANNOT SUPPLY. ***
    Both field hits are now refused, so a planted case is the only thing standing between this
    check and one that has never said no.
    """
    from dbt_assay.inventory import ModelEntry
    from dbt_assay.practices import hop_drops_most_rows, row_loss_candidates

    def child(**kw):
        x = ModelEntry(uid="model.p.c", name="c", path="c.sql", layer="marts",
                       materialized="table")
        x.join_keys = {"driver": ["k"], "lookup": ["k"]}
        x.row_loss = {"driver": (3_156_986, 13_694), "lookup": (3_156_986, 13_694)}
        x.join_kind = {"driver": "INNER", "lookup": "LEFT"}
        x.driving_parents = {"driver"}
        for k, v in kw.items():
            setattr(x, k, v)
        return x

    assert [p for _e, p in row_loss_candidates([child()])] == ["driver"], \
        "a LEFT join or a non-driving edge was still a candidate"

    # THE CONTROL: the driving edge really does lose the rows, so it fires.
    got = hop_drops_most_rows(None, [child()], 0.8)
    names = {f.evidence["parent"] for f in got}
    assert "driver" in names, "the check no longer fires on anything at all"
    assert "13,694 rows from 3,156,986" in got[0].summary

    # A child whose driving edge keeps everything is silent, however small a lookup made it look.
    ok = child(row_loss={"driver": (13_694, 13_694)})
    assert hop_drops_most_rows(None, [ok], 0.8) == []

    # No FROM information at all: fall back to judging every INNER edge rather than nothing.
    blind = child(driving_parents=set())
    assert [p for _e, p in row_loss_candidates([blind])] == ["driver"]


# --- it must translate to a warehouse that is not this one -----------------------------------

DOMAIN_WORDS = ("water", "decree", "wdid", "adwr", "cdss", "diversion", "appropriation",
                "aquifer", "streamflow", "well_depth", "irrigated", "parcel", "permit",
                "lead", "broker", "contractor")


def _sent_text(q: dict) -> str:
    """Only what actually reaches the model. Comments and `_source` do not."""
    import json as _j
    return _j.dumps({k: v for k, v in q.items()
                     if k in ("instructions", "criteria")}, default=str).lower()


def test_no_shipped_question_sends_this_warehouse_s_vocabulary():
    """*** `criteria.examples` ARE SENT, AND DOMAIN NOUNS IN THEM STEER THE ANSWER. ***

    The shipped unit question carried `examples: ["decreed_af", "amount_acre_feet",
    "storage_af"]` and the claim question `"one row per water division and case number"`. On a
    retail warehouse that is water-rights vocabulary arriving as the definition of the question.

    Comments in the YAML are fine and stay: they are for whoever maintains the bank and are never
    sent. This reads the sent half only.
    """
    from dbt_assay.contracts import SHIPPED

    assert len(SHIPPED) >= 15, "the reader is broken; it found almost no questions"
    bad = {}
    for name, q in SHIPPED.items():
        text = _sent_text(q)
        hits = sorted({w for w in DOMAIN_WORDS if w in text})
        if hits:
            bad[name] = hits
    assert not bad, f"shipped questions send domain vocabulary: {bad}"


def test_nothing_in_the_package_hardcodes_a_model_or_column_from_one_project():
    """A default, a fallback or a help string naming a real table here is a tell that the tool was
    fitted to one warehouse."""
    import ast

    import dbt_assay

    root = Path(dbt_assay.__file__).parent
    bad = []
    seen = 0
    for f in sorted(root.rglob("*.py")):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            seen += 1
            v = node.value.lower()
            if len(v) > 220 or "\n" in v:
                continue                      # a docstring or a paragraph of prose
            if any(w in v for w in ("wdid", "adwr", "cdss", "water_right", "decreed_af")):
                bad.append(f"{f.name}:{node.lineno}: {node.value[:70]}")
    assert seen > 500, "the reader is broken"
    assert not bad, "project-specific identifiers in shipped strings:\n  " + "\n  ".join(bad)


def test_a_question_whose_sent_text_changed_carries_a_new_version():
    """*** A VERSION BUMP THAT IS NOT TIED TO A REAL CHANGE IS A LIE ABOUT WHAT MOVED. ***

    Removing domain vocabulary from four criteria blocks, a blanket regex bumped EVERY question
    in those files -- including four whose sent text never changed. `effectiveness` compares
    agreement per version, so a false bump splits a family's verdicts across two versions that
    are the same question and makes the before-and-after meaningless.

    This cannot check history, so it checks the invariant that made the mistake possible: every
    shipped question has a version, and two questions never share one.
    """
    from dbt_assay.contracts import SHIPPED

    versions = {}
    for name, q in SHIPPED.items():
        v = q.get("prompt_version")
        assert v, f"{name} has no prompt_version, so its verdicts cannot be dated"
        versions.setdefault(v, []).append(name)
    shared = {v: n for v, n in versions.items() if len(n) > 1}
    assert not shared, f"two questions share a version, so their verdicts cannot be told apart: {shared}"


# --- round seven: a fork that preserves one property forfeits another ------------------------

def _bank(**over):
    base = {"id_prefix": "x", "type": "choice", "prompt_version": "x.v1",
            "instructions": {"question": "Q?", "note": "N"},
            "criteria": {"a": {"what": "A", "examples": ["one"]},
                         "b": {"what": "B", "examples": ["two"]}},
            "_source": "/shipped/x.yml"}
    return {**base, **over}


def test_a_fork_is_told_which_of_its_blocks_are_copies():
    """*** `yours, replacing` WAS THE WHOLE STORY A FORK GOT. ***

    A project forked `edge_preserves_the_grain` and copied four options WORD FOR WORD on purpose,
    so the hand verification recorded against that family would still apply. That same copying is
    what stops every later improvement reaching it, and a copy nobody knows is stale reads as
    current.
    """
    from dbt_assay.lint import override_drift

    shipped = {"x": _bank()}
    # An override that changed one option and copied the rest.
    mine = _bank(prompt_version="x.mine.v1", _source="/mine/x.yml",
                 criteria={"a": {"what": "A", "examples": ["one"]},
                           "b": {"what": "DIFFERENT", "examples": ["two"]}})
    got = {i.rule: i for i in override_drift({"x": mine}, shipped)}
    assert "override_copies_the_shipped_text" in got
    d = got["override_copies_the_shipped_text"].detail
    assert "5 of 6 blocks" in d, d
    assert got["override_copies_the_shipped_text"].level == "note", \
        "a deliberate fork is not a warning and must not fail --strict"

    # Not an override at all: shipped and user are the same file.
    assert override_drift(shipped, shipped) == []


def test_an_override_that_keeps_the_shipped_version_string_is_an_error():
    """Two different questions under one version: `effectiveness` cannot tell their verdicts
    apart, so the agreement rate mixes answers to two questions."""
    from dbt_assay.lint import override_drift

    shipped = {"x": _bank()}
    same_version = _bank(_source="/mine/x.yml",
                         instructions={"question": "DIFFERENT?", "note": "N"})
    rules = {i.rule for i in override_drift({"x": same_version}, shipped)}
    assert "override_reuses_a_version" in rules
    errs = [i for i in override_drift({"x": same_version}, shipped) if i.level == "error"]
    assert errs, "it did not rise above a warning"


def test_a_fork_that_declares_where_it_came_from_is_told_when_that_moved():
    """`forked_from` is the declared half, the same pattern as `meta.read_by`."""
    from dbt_assay.lint import override_drift

    shipped = {"x": _bank(prompt_version="x.v3")}
    behind = _bank(prompt_version="x.mine.v1", forked_from="x.v1", _source="/mine/x.yml")
    got = [i for i in override_drift({"x": behind}, shipped) if i.rule == "override_is_behind"]
    assert got and "x.v3" in got[0].detail

    current = _bank(prompt_version="x.mine.v1", forked_from="x.v3", _source="/mine/x.yml")
    assert not [i for i in override_drift({"x": current}, shipped)
                if i.rule == "override_is_behind"]


# --- round eight: answers to a question that no longer exists --------------------------------

def _decisions(rows):
    """A store holding (question, prompt_version, decision_key, answer) tuples."""
    from dbt_assay.store import Store
    s = Store(":memory:")
    for q, pv, key, ans in rows:
        s.con.execute(
            """insert or replace into model_decisions
               (decision_key, question, kind, answer, confidence, probabilities, state_hash,
                prompt_version, model_version, call_id, caller, context, input_tokens, decided_at)
               values (?,?,'choice',?,0.9,'{}','h',?,'m','c','t',?,1, current_timestamp)""",
            [key, q, ans, pv, f"ctx {key}"])
    return s


def test_the_reader_never_hides_an_answer_on_a_guess_about_its_family():
    """*** THE FIRST VERSION HID 7,536 ANSWERS AND CALLED IT `246 resolved`. ***

    It resolved "the version shipping now" by splitting the question id on `__` and looking the
    prefix up as a bank's id_prefix. Three shipped questions file under an id that is NOT their
    own bank's prefix, so each lookup landed on a neighboring family: `sentence_is_a_claim`
    files under `claim__N` and read `claim_alignment`'s version; `claim_alignment` files under
    `align` and read `same_concept`'s. Two whole families went to exactly zero, 213 findings and
    18, while nothing about those models had changed.

    So there is no family resolution in the reader at all. The latest answer to a question wins.

    0.24.2 moved those two ids onto their own banks' prefixes (`sentence__N` and `claim`), which
    removes the CAUSE. This test keeps the PROPERTY, because the property has to hold for a
    custom bank whose prefix assay has never seen either.
    """
    s = _decisions([
        ("sentence__0", "sentence.v1", "m::sentence::a", "claim_about_output"),
        ("claim", "claim.v2", "m::claim::a", "supports"),
        ("desc", "desc.v1+comments+scoped", "m::desc", "0.7"),
        ("edge", "edge.v1", "m::edge::p", "silently_multiplied"),
        ("edge", "edge.v2", "m::edge::p", "same_thing"),
    ])
    try:
        got = dict(s.live_decisions("1 = 1", [], columns="question, answer"))
        assert set(got) == {"sentence__0", "claim", "desc", "edge"}, \
            f"an answer was hidden on a guess about which family asked it: {got}"
        assert got["edge"] == "same_thing", "the latest answer did not win"
        assert s.superseded_decisions == 1
    finally:
        s.close()


def test_a_served_answer_whose_question_has_changed_is_DATED_not_hidden():
    """Hiding it leaves the caller with nothing, which is strictly worse than serving it dated.
    Re-running writes a newer row and that row wins by itself."""
    s = _decisions([("edge", "edge.retired.v0", "m::edge::p", "silently_multiplied")])
    try:
        got = s.live_decisions("1 = 1", [], columns="question, answer")
        assert len(got) == 1, "it hid the only answer there was"
        assert s.stale_decisions == 1, "and it did not say the question had changed"
    finally:
        s.close()

    # A writer's state-shape suffix is not a different question.
    from dbt_assay.contracts import SHIPPED
    live = SHIPPED["description_contradicts_the_code"]["prompt_version"]
    s2 = _decisions([("desc", f"{live}+comments+scoped", "m::desc", "0.7")])
    try:
        s2.live_decisions("1 = 1", [], columns="question, answer")
        assert s2.stale_decisions == 0, "the `+comments+scoped` suffix read as a retired question"
    finally:
        s2.close()


def test_one_answer_per_question_and_the_uniquifier_is_not_a_hardcoded_list():
    """`("align", "edge")` was a second copy of a fact the rows already carry: any id that repeats
    needs a distinct key, and a list goes stale the first time a new family asks per-something."""
    from dbt_assay.inventory import _judgments

    # Two answers to the SAME question about different subjects of one model, and a third
    # question that is not in any hardcoded list. All three must survive with distinct keys.
    s = _decisions([("edge", "v1", "m::edge::p", "a"),
                    ("edge", "v1", "m::edge::q", "c"),
                    ("sev__0", "v1", "m::sev::t1", "1.2"),
                    ("sev__0", "v1", "m::sev::t2", "0.3")])
    try:
        got = _judgments(s, "m")
        assert len(got) == 4, f"an answer was lost to a key collision: {sorted(got)}"
        assert len({v["answer"] for v in got.values()}) == 4
    finally:
        s.close()


def test_the_mcp_install_line_writes_a_file_the_repo_can_keep():
    """Without `--scope project` the entry goes to ~/.claude.json keyed to one absolute path on
    one machine: invisible from a directory above, absent from a fresh clone."""
    import inspect

    from dbt_assay import cli, mcp_server

    for mod in (cli, mcp_server):
        src = inspect.getsource(mod)
        i = src.find("claude mcp add assay")
        assert i > 0, f"{mod.__name__} no longer tells anyone how to add the server"
        assert "--scope project" in src[i:i + 60], f"{mod.__name__} still writes machine-local"


def test_no_question_id_resolves_to_another_family():
    """*** `check_question_ids` ASSERTED THE WRONG INVARIANT FOR EIGHT RELEASES. ***

    It asserts that SOME bank claims a question's prefix, and one always does. It never asserted
    that the prefix claims the RIGHT bank. Three shipped questions violate that:
    `sentence_is_a_claim` (prefix `sentence`) files under `claim__N` and read `claim_alignment`'s
    version; `claim_alignment` (prefix `claim`) files under `align` and read `same_concept`'s.
    A reader built on that mapping hid 7,536 answers and reported `246 resolved`.

    RECONCILED IN 0.24.2. `kind_questions` emits `sentence__N` and `align_question` emits
    `claim`, each matching its own bank's declared `id_prefix`, and `Store.MOVED_QUESTION_IDS`
    carried the 7,536 stored answers across without re-asking anything. This was an xfail for one
    release; it is an assertion now, and a new question that files under a neighbor's prefix
    fails here rather than being discovered by 231 findings going missing.
    """
    from dbt_assay.contracts import id_prefix_conflicts

    conflicts = id_prefix_conflicts()
    assert not conflicts, (
        "a question files under a prefix another bank declares, so every resolution from a "
        "stored id to a family lands one family over:\n  " + "\n  ".join(conflicts))


def test_the_emitted_id_table_matches_the_writers():
    """The table naming what each writer emits is a second copy, so it is checked against the
    source. A stale copy here would make `id_prefix_conflicts` miss the thing it exists to find."""
    import inspect

    from dbt_assay.contracts import EMITTED_IDS

    seen = {}
    for mod_name in ("claims", "align", "testing", "semantics", "practices", "lint"):
        mod = __import__(f"dbt_assay.{mod_name}", fromlist=["x"])
        src = inspect.getsource(mod)
        for fam, (emitted, where) in EMITTED_IDS.items():
            if not where.startswith(mod_name + "."):
                continue
            literal = emitted.replace("<i>", "{i}")
            assert f'"{literal}"' in src or f"'{literal}'" in src, \
                f"{where} does not emit {emitted!r}; the table is stale"
            seen[fam] = True
    assert len(seen) >= 7, f"the reader is broken; it checked only {len(seen)} writers"


# --------------------------------------------------------------------- 0.24.2: the ids that moved

def _planted_store(tmp_path, decisions, adjudications=()):
    """A store holding PRE-rename rows, written straight to duckdb so the migration is what
    moves them rather than the writer never having produced them."""
    from dbt_assay.store import Store
    p = str(tmp_path / "assay.duckdb")
    s = Store(p)
    for key, q, pv, ans in decisions:
        s.con.execute(
            """insert into model_decisions (decision_key, question, kind, answer, confidence,
                   probabilities, state_hash, prompt_version, model_version, call_id, caller,
                   context, input_tokens, decided_at)
               values (?,?,'choice',?,0.9,'{}',?,?,'jev-1','c','t','x',1, current_timestamp)""",
            [key, q, ans, f"h-{key}-{q}", pv])
    for subj, q, fam in adjudications:
        s.con.execute(
            """insert into adjudications (subject, question, family, answered, verdict,
                   correction, note, decided_by, source, prompt_version, model_version, decided_at)
               values (?,?,?,'contradicts','disagree','','','ryan','human','claim.v2','jev-1',
                       current_timestamp)""",
            [subj, q, fam])
    s.close()
    return p


def test_a_question_that_moved_prefix_takes_its_stored_answers_with_it(tmp_path):
    """*** A RENAME, NOT A RE-ASK. ***

    `sentence_is_a_claim` filed under `claim__N` and `claim_alignment` under `align`, each a
    prefix a neighboring bank declares. Fixing the emitted ids alone would have orphaned 7,536
    answers on the field store: the writer asks under the new id, finds no cached answer, and pays
    to re-ask a question whose TEXT never changed. The answers move with the id.

    Exercised against a real store rather than by reading the source, because a source grep has
    passed while the thing it guarded was broken several times in this file.
    """
    from dbt_assay.store import Store

    p = _planted_store(
        tmp_path,
        [("m::sentence::a", "claim__0", "sentence.v1", "claim_about_output"),
         ("m::sentence::a", "claim__1", "sentence.v1", "claim_about_output"),
         ("m::claim::a", "align", "claim.v2", "contradicts")],
        [("m::claim::a", "align", "same_concept")])

    s = Store(p)
    try:
        qs = sorted(r[0] for r in s.con.execute("select question from model_decisions").fetchall())
        assert qs == ["claim", "sentence__0", "sentence__1"], qs
        # NOT A RE-ASK: the state hash is what decides a cache hit, and it did not move.
        hashes = {r[0] for r in s.con.execute("select state_hash from model_decisions").fetchall()}
        assert hashes == {"h-m::sentence::a-claim__0", "h-m::sentence::a-claim__1",
                          "h-m::claim::a-align"}, hashes
        # *** AND THE VERDICT MOVED WITH THE ANSWER, OR IT STOPS JOINING TO IT. ***
        got = s.con.execute("select question, family from adjudications").fetchall()
        assert got == [("claim", "claim_alignment")], got
        assert ("model_decisions", "align", "claim", 1) in \
            [(t, o, n, c) for t, o, n, c in __import__(
                "dbt_assay.store", fromlist=["x"]).QUESTION_IDS_MOVED]
    finally:
        s.close()

    s = Store(p)
    try:
        assert s.renamed_question_ids == [], "the migration is not idempotent"
    finally:
        s.close()


def test_a_question_id_that_cannot_move_is_kept_and_named(tmp_path):
    """*** IT NEVER DROPS A ROW, AND AN `insert or replace` WOULD HAVE. ***

    The question id is in the primary key, so a store holding both the old id and the new one
    under one key cannot hold both after the rename. `update` raises there and `insert or replace`
    destroys one of the two answers silently -- the exact shape this tool checks other people's
    code for. The movable rows move, the collision stays put, and it is reported by name.
    """
    from dbt_assay.store import Store

    p = _planted_store(
        tmp_path,
        [("m::claim::a", "align", "claim.v2", "OLD_under_align"),
         ("m::claim::a", "claim", "claim.v2", "ALREADY_under_claim"),
         ("m::claim::b", "align", "claim.v2", "moves_fine")])

    s = Store(p)
    try:
        rows = sorted(s.con.execute(
            "select decision_key, question, answer from model_decisions").fetchall())
        assert rows == [("m::claim::a", "align", "OLD_under_align"),
                        ("m::claim::a", "claim", "ALREADY_under_claim"),
                        ("m::claim::b", "claim", "moves_fine")], rows
        assert s.unrenamable_question_ids == [("align", "claim", 1)], s.unrenamable_question_ids
        assert ("align", "claim", 1) in s.renamed_question_ids, s.renamed_question_ids
    finally:
        s.close()


def test_the_rename_does_not_match_a_single_underscore_wildcard(tmp_path):
    """`like 'claim__%'` matches `claimXY` too, because `_` is a wildcard in LIKE and the ids
    being moved end in a DOUBLE underscore. `starts_with` is the whole fix and this is what says
    so: a neighboring id that merely begins with the same letters must not move."""
    from dbt_assay.store import Store

    p = _planted_store(tmp_path, [("m::x", "claimXY", "v1", "untouched"),
                                  ("m::x", "claim__0", "v1", "moves"),
                                  # *** same_concept OWNS `align__N` AND KEEPS IT. ***
                                  # Only the BARE `align` was claim_alignment's. A prefix match
                                  # here would have swept up a whole other family's answers.
                                  ("m::x", "align__0", "v1", "same_concepts_own")])
    s = Store(p)
    try:
        got = sorted(r[0] for r in s.con.execute("select question from model_decisions").fetchall())
        assert got == ["align__0", "claimXY", "sentence__0"], got
    finally:
        s.close()


# ------------------------------------------------- 0.24.2: the claim refusal reaches the read path

def test_near_duplicate_claims_collapse_but_two_real_claims_do_not():
    """*** ONE SENTENCE, WRITTEN IN TWO PLACES, ASKED AND REPORTED TWICE. ***

    `int_water_diversion_history` came back contradicted at 0.95 AND 0.93, `stg_cdss_dams` at
    0.95 and 0.93 -- each pair one sentence a person had put in both a model header and a schema
    description. `claim_id` collapses byte-identical text, so the survivors differed by a
    backtick or a trailing full stop.

    The other half matters more: a normalizer aggressive enough to collapse two claims that
    genuinely differ loses one of them quietly, so this asserts what must NOT collapse.
    """
    from dbt_assay.claims import near_duplicate_key as k

    assert k("m", "The `amount_af` column is acre-feet.") == k("m", "the amount_af column is acre feet")
    assert k("m", "Residential permits are filtered out") == k("m", "Residential permits are filtered out.")
    # different claims stay different
    assert k("m", "residential permits are filtered out") != k("m", "commercial permits are filtered out")
    assert k("m", "one row per parcel") != k("m", "one row per parcel per year")
    # the same sentence about two models is two claims
    assert k("model.a", "one row per parcel") != k("model.b", "one row per parcel")


def test_a_stored_contradiction_the_call_site_would_refuse_is_not_a_finding(tmp_path):
    """*** THE REFUSAL RAN AT THE CALL SITE AND NOWHERE ELSE. ***

    0.23.0 taught `verify` not to ask the two claim shapes SQL cannot settle, which cut
    contradictions 389 -> 240 and removed both hand-read false positives. It changed no
    `prompt_version`, because the question did not change -- only which subjects are worth
    sending. So every answer given before it shipped is still the live answer, is not stale, and
    went on producing findings from the read path, which never consulted the refusal.

    One fact in two places, silent when they disagree. Exercised through the real function.
    """
    from dbt_assay.inventory import _claim_is_unanswerable

    class _Schema:
        def columns(self, _uid):
            return type("C", (), {"names": ["parcel_id", "acres"]})()

    class _Digest:
        def __init__(self):
            self.ok = True
            self.output_exprs = {"parcel_id": "p.parcel_id", "acres": "p.acres"}
            self.predicates_atomic: list = []
            self.group_by: list = []

    class _Project:
        def __init__(self):
            self.models = {"m": type("M", (), {"parents": [], "name": "m",
                                               "path": "m.sql"})()}

        def name_of(self, uid):
            return uid

    proj, digests, schema = _Project(), {"m": _Digest()}, _Schema()
    v = {"decision_key": "m::claim::c1"}

    # names an identifier that is nowhere in the model or its parents -> cannot be contradicted
    why = _claim_is_unanswerable(v, {"c1": "the ponds_covered flag is set upstream"},
                                 "m", proj, digests, schema)
    assert why and "absent" in why, why

    # a claim about ROWS, which SQL structure cannot settle in either direction
    why = _claim_is_unanswerable(v, {"c1": "the full record runs from 1886 to 2026"},
                                 "m", proj, digests, schema)
    assert why and "ROWS" in why, why

    # an ordinary claim naming a column that IS here stays askable, and stays a finding
    assert _claim_is_unanswerable(v, {"c1": "one row per parcel_id"},
                                  "m", proj, digests, schema) == ""


def test_an_unknown_claim_is_left_alone_rather_than_refused(tmp_path):
    """*** A GUARD THAT CANNOT SEE ITS SUBJECT MUST NOT REPORT A VERDICT ON IT. ***

    A decision key that resolves to no stored claim means the text is unavailable, not that the
    claim is unanswerable. Returning a refusal there would delete a finding on the strength of a
    missing row, which is the absence-reads-as-a-pass defect this file is mostly about.
    """
    from dbt_assay.inventory import _claim_is_unanswerable

    assert _claim_is_unanswerable({"decision_key": "m::claim::gone"}, {}, "m", None, {}, None) == ""
    assert _claim_is_unanswerable({}, {"c1": "anything"}, "m", None, {}, None) == ""


# ------------------------------------------- 0.24.2: hop_drops_most_rows, end to end on a control

def _control_project(tmp_path, sql: dict, parents: dict):
    """A dbt project on disk: manifest, compiled SQL, the real loader. Relations are qualified
    the way dbt compiles them, because the driving-edge refusal resolves `from_relations` through
    `schema.relation` and a bare name matches nothing there."""
    import json
    nodes, parent_map, child_map = {}, {}, {}
    for name, text in sql.items():
        uid = f"model.p.{name}"
        layer = "staging" if name.startswith("stg") else "intermediate"
        path = f"models/{layer}/{name}.sql"
        nodes[uid] = {"resource_type": "model", "name": name, "original_file_path": path,
                      "schema": "main", "description": "", "columns": {},
                      "config": {"materialized": "table", "meta": {}}}
        parent_map[uid] = parents.get(uid, [])
        child_map.setdefault(uid, [])
        f = tmp_path / "target" / "compiled" / "p" / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    for child, ps in parents.items():
        for p in ps:
            child_map.setdefault(p, []).append(child)
    (tmp_path / "target" / "manifest.json").write_text(json.dumps({
        "metadata": {"project_name": "p", "dbt_version": "1.11.0", "adapter_type": "duckdb"},
        "nodes": nodes, "sources": {}, "parent_map": parent_map, "child_map": child_map}))
    return tmp_path / "target"


def test_hop_drops_most_rows_fires_on_a_join_that_really_fails_to_match(tmp_path):
    """*** THE END-TO-END CONTROL. A CHECK THAT HAS NEVER SAID `no` HAS NOT BEEN VERIFIED. ***

    On the only warehouse this has run against, eight of nine driving edges kept EXACTLY 100% of
    their rows and the ninth was a fan-out, so it has never caught a real defect and
    `VERIFICATION.md` says so. It was verified against a planted candidate dict, which skips the
    two things most likely to be wrong: whether candidate selection reaches the hop at all, and
    whether the counts get back to it.

    So this drives the whole chain -- manifest, parser, driving-edge selection, counting, finding
    -- against a real DuckDB holding real rows, and asserts three outcomes that must differ:

      int_parcel_owner   100 of 1,000 driving rows survive an INNER join   FIRES
      int_parcel_zone  1,000 of 1,000 survive                              SILENT
      int_parcel_where    10 of 1,000, and the model has a WHERE           REFUSED

    The third is what stops the threshold taking the credit: it loses MORE than the first and
    produces nothing, because the loss is declared in the SQL.
    """
    import duckdb

    from dbt_assay import live, practices

    sql = {
        "stg_parcels": "select parcel_id, owner_id, acres from raw.parcels",
        "stg_owners": "select owner_id, owner_name from raw.owners",
        "stg_lookup": "select parcel_id, zone from raw.lookup",
        # the defect: a driving INNER join whose key matches a fraction of the driving rows
        "int_parcel_owner":
            "select p.parcel_id, p.acres, o.owner_name from main.stg_parcels p "
            "join main.stg_owners o on p.owner_id = o.owner_id",
        # the same shape, matching everything
        "int_parcel_zone":
            "select p.parcel_id, p.acres, l.zone from main.stg_parcels p "
            "join main.stg_lookup l on p.parcel_id = l.parcel_id",
        # loses more than either, and says so in the SQL
        "int_parcel_where":
            "select p.parcel_id, p.acres, o.owner_name from main.stg_parcels p "
            "join main.stg_owners o on p.owner_id = o.owner_id where p.acres > 500",
    }
    parents = {
        "model.p.int_parcel_owner": ["model.p.stg_parcels", "model.p.stg_owners"],
        "model.p.int_parcel_zone": ["model.p.stg_parcels", "model.p.stg_lookup"],
        "model.p.int_parcel_where": ["model.p.stg_parcels", "model.p.stg_owners"],
    }
    target = _control_project(tmp_path, sql, parents)
    st = live.read(target)

    # *** SELECTION, BEFORE ANY COUNT. ***
    # Only the DRIVING edge of a model that declares no narrowing. The `where` model is gone and
    # every joined-to parent is gone, which is the 0.21.1 refusal doing the work.
    cands = {(e.name, p) for e, p in practices.row_loss_candidates(st.entries)}
    assert cands == {("int_parcel_owner", "stg_parcels"),
                     ("int_parcel_zone", "stg_parcels")}, cands

    db = duckdb.connect(str(tmp_path / "w.duckdb"))
    db.execute("create schema if not exists main")
    db.execute("create table main.stg_parcels as "
               "select i as parcel_id, i % 50 as owner_id, i as acres from range(1000) t(i)")
    db.execute("create table main.stg_owners as "
               "select i as owner_id, 'o' as owner_name from range(50) t(i)")
    db.execute("create table main.stg_lookup as "
               "select i as parcel_id, 'z' as zone from range(1000) t(i)")
    db.execute("create table main.int_parcel_owner as select p.parcel_id, p.acres, o.owner_name "
               "from main.stg_parcels p join main.stg_owners o on p.owner_id = o.owner_id "
               "where o.owner_id < 5")
    db.execute("create table main.int_parcel_zone as select p.parcel_id, p.acres, l.zone "
               "from main.stg_parcels p join main.stg_lookup l on p.parcel_id = l.parcel_id")
    db.execute("create table main.int_parcel_where as select p.parcel_id from main.stg_parcels p "
               "join main.stg_owners o on p.owner_id = o.owner_id where p.acres > 990")

    class _Probe:
        @staticmethod
        def run_sql(q, _project_dir, _profiles_dir, _dbt_bin, limit=100, **_k):
            return Result(rows=[dict(zip([c[0] for c in db.description], r, strict=True))
                                for r in db.execute(q).fetchall()])

    try:
        n = practices.verify_row_loss(st.entries, st.project, _Probe, str(tmp_path), None,
                                      "dbt", st.schema)
        assert n == 2, f"counted {n} hops, expected the two candidates"
        loss = {e.name: e.row_loss for e in st.entries if e.row_loss}
        assert loss == {"int_parcel_owner": {"stg_parcels": (1000, 100)},
                        "int_parcel_zone": {"stg_parcels": (1000, 1000)}}, loss

        got = practices.hop_drops_most_rows(st.project, st.entries, 0.8)
        assert [f.subject_name for f in got] == ["int_parcel_owner"], \
            [(f.subject_name, f.summary) for f in got]
        assert "100" in got[0].summary and "1,000" in got[0].summary, got[0].summary
        # *** AN ABSENT MEASUREMENT IS NOT A PASS, SO THE REFUSED HOP IS ABSENT AND SILENT. ***
        assert "int_parcel_where" not in {f.subject_name for f in got}
    finally:
        db.close()


# ------------------------------------------------- 0.29.0: calibration, seeds, and the decision key

def test_a_noul_is_banded_by_its_ANSWER_because_it_has_no_confidence(tmp_path):
    """*** READING ONLY `confidence` CONCLUDED THE QUESTION WAS UNANSWERABLE. ***

    A `choice` stores distribution concentration in `confidence`. A `noul` stores NULL there ON
    PURPOSE, because its ANSWER is the probability -- the schema comment says so. A calibration
    report that bands by `confidence` therefore finds nulls on every noul and reports nothing,
    while the richest material in the store sits in the answer column: on the field store, 82
    verdicts with 26 disagreements in them.
    """
    from dbt_assay.store import Store
    from dbt_assay.subjects import calibration

    s = Store(str(tmp_path / "assay.duckdb"))
    try:
        rows = [("m::a", "key__x", "noul", "0.9", None, "agree"),
                ("m::b", "key__y", "noul", "0.1", None, "disagree"),
                ("m::c", "role__z", "choice", "identifier", 0.8, "agree")]
        for key, q, kind, ans, conf, _v in rows:
            s.con.execute(
                """insert into model_decisions (decision_key, question, kind, answer, confidence,
                       probabilities, state_hash, prompt_version, model_version, call_id, caller,
                       context, input_tokens, decided_at)
                   values (?,?,?,?,?,'{}','h','v1','m','c','t','x',1, current_timestamp)""",
                [key, q, kind, ans, conf])
        for key, q, _k, _a, _c, v in rows:
            s.adjudicate(key, q, "fam", "", v, source="human")
        got = {(r["band"], r["ruled"]) for r in calibration(s)}
        assert ("0.70+", 2) in got, f"the noul at 0.9 and the choice at 0.8 did not band: {got}"
        assert ("< 0.30", 1) in got, f"the noul at 0.1 was not banded by its answer: {got}"
    finally:
        s.close()


def test_calibration_never_sums_two_kinds_of_verdict(tmp_path):
    """`label` is the project's own declarations, measured wrong three times in four when read by
    hand. `human` is somebody who looked. Adding them produces a number that means neither."""
    from dbt_assay.store import Store
    from dbt_assay.subjects import calibration

    s = Store(str(tmp_path / "assay.duckdb"))
    try:
        for i, src in enumerate(("label", "human", "agent")):
            s.con.execute(
                """insert into model_decisions (decision_key, question, kind, answer, confidence,
                       probabilities, state_hash, prompt_version, model_version, call_id, caller,
                       context, input_tokens, decided_at)
                   values (?,'q','choice','a',0.9,'{}','h','v1','m','c','t','x',1,
                           current_timestamp)""", [f"m{i}"])
            s.adjudicate(f"m{i}", "q", "fam", "a", "agree", source=src)
        got = calibration(s)
        assert {r["source"] for r in got} == {"label", "human", "agent"}
        assert all(r["ruled"] == 1 for r in got), "sources were summed"
    finally:
        s.close()


def test_a_verdict_with_no_probability_is_counted_apart_not_as_a_zero(tmp_path):
    """*** A STRUCTURAL FINDING HAS NO PROBABILITY, AND THAT IS CORRECT RATHER THAN MISSING. ***

    97 of 105 agent rulings on the field store were on structural findings: a parser decided them,
    no question was asked, there is nothing to calibrate. They must not land in a band, and they
    must not vanish either -- a shrinking table reads as a confident judge.
    """
    from dbt_assay.store import Store
    from dbt_assay.subjects import calibration

    s = Store(str(tmp_path / "assay.duckdb"))
    try:
        s.con.execute(
            """insert into model_decisions (decision_key, question, kind, answer, confidence,
                   probabilities, state_hash, prompt_version, model_version, call_id, caller,
                   context, input_tokens, decided_at)
               values ('m','q','noul','not a number',NULL,'{}','h','v1','m','c','t','x',1,
                       current_timestamp)""")
        s.adjudicate("m", "q", "fam", "", "agree", source="human")
        got = calibration(s)
        assert [r["band"] for r in got] == ["no probability"], got
    finally:
        s.close()


def test_a_ruling_on_a_judged_finding_records_the_decision_it_ruled_on(tmp_path):
    """*** OF 105 AGENT RULINGS ON THE FIELD STORE, ZERO REACHED A DECISION BY ANY JOIN. ***

    `rule(finding=)` files under `<uid>::finding::<id>`; the confidence lives on a decision keyed
    by the subject the question was asked about. Without the link the verdict never reaches the
    number it ruled on. Empty for a structural finding, which is the honest answer, not a gap.
    """
    from dbt_assay.store import Store

    s = Store(str(tmp_path / "assay.duckdb"))
    try:
        s.adjudicate("m::finding::abc", "edge", "fam", "x", "agree",
                     source="agent", decision_key="model.p.m")
        s.adjudicate("m::finding::def", "arbitrary_pick", "fam", "", "agree", source="agent")
        got = dict(s.con.execute(
            "select subject, decision_key from adjudications").fetchall())
        assert got["m::finding::abc"] == "model.p.m", "a judged ruling lost its decision"
        assert (got["m::finding::def"] or "") == "", "a structural ruling invented one"
    finally:
        s.close()


def test_a_seed_nothing_reads_is_found_by_the_check_built_to_find_that(project_dir):
    """*** ASSAY'S OWN CHECK, POINTED AT ASSAY'S OWN OUTPUT, COULD NOT FIRE. ***

    `source_reaches_nothing` iterates `project.sources`. A seed is a node, so 80% of what
    `assay export` writes was loaded on every build, read by nothing, and invisible to it.
    """
    import json

    from dbt_assay.checks.sources import completeness_checks, seed_reaches_nothing
    from dbt_assay.manifest import Project

    man = json.loads((project_dir / "manifest.json").read_text())
    man["nodes"]["seed.p.unread"] = {
        "resource_type": "seed", "name": "unread", "package_name": "p",
        "original_file_path": "seeds/unread.csv", "config": {}, "meta": {}}
    man["nodes"]["seed.p.read"] = {
        "resource_type": "seed", "name": "read", "package_name": "p",
        "original_file_path": "seeds/read.csv", "config": {}, "meta": {}}
    man["nodes"]["seed.other.theirs"] = {
        "resource_type": "seed", "name": "theirs", "package_name": "somepackage",
        "original_file_path": "seeds/theirs.csv", "config": {}, "meta": {}}
    man["child_map"]["seed.p.read"] = ["model.p.stg_bad_notnull"]
    (project_dir / "manifest.json").write_text(json.dumps(man))

    got = {f.subject_name for f in seed_reaches_nothing(Project.load(project_dir))}
    assert "unread" in got, "a seed with no reader is not reported"
    assert "read" not in got, "a seed WITH a reader is reported"
    assert "theirs" not in got, "somebody else's package seed is reported as your problem"

    # *** AND IT MUST REACH THE REPORT, WHICH IT DID NOT. ***
    # `completeness` selected its members with `f.check.startswith("source_")`, a hand-written
    # membership rule standing in for the real list. The check fired seven times on the field
    # warehouse and printed as a zero.
    owned = completeness_checks()
    assert "seed_reaches_nothing" in owned
    assert "hop_drops_most_rows" in owned


def test_the_seed_declaration_comes_from_the_store_not_a_hand_list(tmp_path):
    """*** THE DECLARATION, THE STORE AND THE QUERY WERE THREE SPELLINGS OF ONE FACT. ***

    Reported from the field, about this tool. `edge_facts.dropped` is INTEGER in the store, the
    hand-written type map did not name it, so `assay.yml` declared it `varchar`, dbt honoured the
    declaration, and assay's own shipped query died on `sum(dropped)`. Any two of those three
    agree fine; all three is broken. Seven columns had drifted out of the map.

    It also produced a wrong number that looked right: `len()` over a VARCHAR reported a model
    dropping "65 columns" when 65 was the character count and the real answer is 794.
    """
    from dbt_assay import export
    from dbt_assay.store import Store

    s = Store(str(tmp_path / "assay.duckdb"))
    try:
        s.con.execute(
            "insert into edge_facts values ('r','p','c','pn','cn',10,7,3,'[]','[]')")
        out = export.to_seeds(s, tmp_path / "seeds")
        yml = export.schema_yml(s, out)
        for numeric in ("available: integer", "carried: integer", "dropped: integer"):
            assert numeric in yml, f"{numeric} is not declared, so a sum on it will fail"
        # and the columns that really ARE text stay text
        assert "joined_on: varchar" in yml and "dropped_cols: varchar" in yml
        assert "weight: double" in yml and "marts: integer" in yml
        # the derivation asks the store, so a column nobody listed still gets the right type
        assert export.seed_type(s, "edge_facts", "dropped") == "integer"
        assert export.seed_type(s, "edge_facts", "joined_on") == "varchar"
        assert export.seed_type(s, "nosuchtable", "x") == "varchar", "it must not raise"
    finally:
        s.close()


def test_every_shipped_example_query_runs_against_a_seeded_export(tmp_path):
    """*** A SHIPPED QUERY THAT FAILS, OR SILENTLY RETURNS NOTHING, IS THE DEFECT THIS TOOL IS
    ABOUT. *** Both happened: one died on `sum(VARCHAR)` and one returned zero rows because a
    decision key is `<uid>::<family>::<id>` and it matched the bare uid. So they are executed
    here, against tables built with the TYPES ASSAY ITSELF DECLARES, which is what dbt does.
    """
    import csv
    import re

    import duckdb

    from dbt_assay import export
    from dbt_assay.store import Store

    s = Store(str(tmp_path / "assay.duckdb"))
    try:
        s.con.execute("insert into runs (run_id, started_at, project, assay_version, models) "
                      "values ('r', current_timestamp, 'p', '0', 1)")
        s.con.execute("insert into edge_facts values ('r','model.p.a','model.p.b','a','b',"
                      "10,7,3,'[\"k\"]','[\"x\"]')")
        s.con.execute(
            """insert into findings (run_id, check_name, subject, subject_name, file, summary,
                   detail, base, weight, descendants, marts, evidence, finding_id)
               values ('r','c','model.p.b','b','f','s','d',2,3.0,1,4,'{}','fid')""")
        s.con.execute(
            """insert into model_decisions (decision_key, question, kind, answer, confidence,
                   probabilities, state_hash, prompt_version, model_version, call_id, caller,
                   context, input_tokens, decided_at)
               values ('model.p.b::edge::x','edge','choice','same_thing',0.4,'{}','h','v','m',
                       'c','t','x',1, current_timestamp)""")
        out = export.to_seeds(s, tmp_path / "seeds")
        types = {}
        for line in export.schema_yml(s, out).splitlines():
            if line.startswith("  - name: "):
                cur = line.split("name: ")[1].strip()
                types[cur] = {}
            elif ": " in line and line.startswith("        "):
                k, v = line.strip().split(": ", 1)
                types[cur][k] = v
    finally:
        s.close()

    db = duckdb.connect()
    for p in sorted((tmp_path / "seeds").glob("assay_*.csv")):
        with p.open() as fh:
            header = next(csv.reader(fh))
        cols = ", ".join(f"'{c}': '{types.get(p.stem, {}).get(c, 'varchar')}'" for c in header)
        db.execute(f"create table {p.stem} as select * from "
                   f"read_csv('{p}', header=true, columns={{{cols}}})")

    ran = 0
    for name, sql in [("defect_classes", export.EXAMPLE_SQL), *export.EXTRA_SQL.items()]:
        q = re.sub(r"\{\{ ref\('([a-z_]+)'\) \}\}", r"\1", sql)
        db.execute(q).fetchall()          # raises on a type error, which is the point
        ran += 1
    assert ran == 1 + len(export.EXTRA_SQL), "a shipped query was not executed"


def test_a_band_reports_its_interval_and_says_when_nothing_separates():
    """*** A BARE PERCENTAGE AT n=18 INVITES A CONCLUSION THE SAMPLE CANNOT CARRY. ***

    Reported from the field on the report's first day: 85 / 74 / 50 / 64 reads as a clean
    inversion, and every adjacent pair overlaps -- best and worst included. Saying so is the whole
    job of a feature whose purpose is to stop somebody gating on the wrong axis.
    """
    from dbt_assay.subjects import separated, wilson

    lo, hi = wilson(17, 20)
    assert 0.6 < lo < 0.7 and 0.93 < hi < 0.97, (lo, hi)
    # Wilson, not the normal approximation, because the normal one runs past 1.0 exactly here
    assert wilson(20, 20)[1] <= 1.0 and wilson(0, 20)[0] >= 0.0
    assert wilson(0, 0) == (None, None)

    field = [{"source": "label", "family": "f", "agree": a, "ruled": n,
              "lo": wilson(a, n)[0], "hi": wilson(a, n)[1]}
             for a, n in ((17, 20), (14, 19), (9, 18), (16, 25))]
    assert not separated(field), "the field bands were reported as separating and they do not"
    apart = [{"source": "label", "family": "f", "agree": a, "ruled": n,
              "lo": wilson(a, n)[0], "hi": wilson(a, n)[1]}
             for a, n in ((200, 200), (10, 200))]
    assert separated(apart), "two obviously different bands did not register as separating"


def test_a_fork_that_already_declares_its_origin_is_not_asked_to():
    """*** `banks` ASKED FOR A `forked_from` THREE LINES ABOVE THE ONE IT WAS ASKING FOR. *** (25.14)"""
    from dbt_assay.lint import override_drift

    shipped = {"x": _bank()}
    mine = _bank(prompt_version="x.mine.v1", _source="/mine/x.yml", forked_from="x.v1",
                 criteria={"a": {"what": "A", "examples": ["one"]},
                           "b": {"what": "DIFFERENT", "examples": ["two"]}})
    assert override_drift({"x": mine}, shipped) == []
    # And the moment the shipped one moves, the fork hears about it.
    moved = {"x": _bank(prompt_version="x.v2")}
    assert [i.rule for i in override_drift({"x": mine}, moved)] == ["override_is_behind"]
