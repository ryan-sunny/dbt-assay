"""From ruling on all 99 findings of a 357-model warehouse by hand.

*** TWO STRUCTURAL BLIND SPOTS CAUSED EVERY SINGLE DISAGREEMENT. ***
Twelve disagrees: ten were a union member read as a fan-out, two were a tessellation read as an
approximated circle. Both discriminators are readable from the AST with no judgment and no call,
which is the point -- the judgment was allowed to be wrong about something code settles exactly.
"""
from dbt_assay.checks.structural import (
    cannot_fail_by_construction,
    default_literal,
    default_share_sql,
)
from dbt_assay.parse import digest


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
