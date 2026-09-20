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
