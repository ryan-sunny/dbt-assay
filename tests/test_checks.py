"""Every check gets a positive control AND a negative one.

A guard is not trusted because it stayed quiet. It is trusted because it was shown to bite on a
thing broken on purpose, and to stay quiet on the correct-but-similar thing beside it.
"""
import pytest

from dbt_assay.checks import run_all
from dbt_assay.manifest import Project
from dbt_assay.parse import digest


@pytest.fixture
def findings(project_dir):
    p = Project.load(project_dir)
    d = {uid: digest(m.compiled, m.name) for uid, m in p.models.items() if m.readable}
    return p, run_all(p, d)


def _for(findings, check, model):
    return [f for f in findings if f.check == check and f.subject_name == model]


def test_not_null_on_coalesce_with_literal_fires(findings):
    _, fs = findings
    assert _for(fs, "test_cannot_fail", "stg_bad_notnull")


def test_not_null_on_coalesce_with_column_does_not_fire(findings):
    _, fs = findings
    assert not _for(fs, "test_cannot_fail", "stg_ok_notnull")


def test_unique_on_sole_group_by_key_fires(findings):
    _, fs = findings
    assert _for(fs, "test_cannot_fail", "int_bad_unique")


def test_unique_with_a_compound_group_by_does_not_fire(findings):
    _, fs = findings
    assert not _for(fs, "test_cannot_fail", "int_ok_unique")


def test_accepted_values_covering_every_case_branch_fires(findings):
    _, fs = findings
    assert _for(fs, "test_cannot_fail", "stg_bad_accepted")


def test_ranking_by_st_distance_fires(findings):
    _, fs = findings
    hit = _for(fs, "ranks_by_degrees", "int_bad_degrees")
    assert hit and hit[0].base == 3


def test_st_distance_in_a_projection_only_does_not_fire(findings):
    """The false positive that four hand-written regexes could not avoid."""
    _, fs = findings
    assert not _for(fs, "ranks_by_degrees", "int_ok_degrees")


def test_duckdb_full_match_against_a_bare_pattern_fires(findings):
    _, fs = findings
    assert _for(fs, "duckdb_full_match", "stg_bad_tilde")


def test_duckdb_full_match_with_wildcards_does_not_fire(findings):
    _, fs = findings
    assert not _for(fs, "duckdb_full_match", "stg_ok_tilde")


def test_severity_is_lifted_by_reach(findings):
    _, fs = findings
    leaf = [f for f in fs if f.descendants == 0]
    assert all(f.weight == f.base for f in leaf)


def test_a_distance_measured_after_reprojection_is_not_flagged():
    """Correct code: transform to a meter-based CRS, THEN measure. A guard that bans the function
    outright catches the one model that already did the right thing."""
    from dbt_assay.parse import digest as dg
    bad = dg("select row_number() over (order by ST_Distance(a.geom, b.pt)) rn from t a join u b on true")
    assert bad.windows[0].order_roots == ["ST_DISTANCE"]
    assert bad.windows[0].order_reprojected == [False]
    good = dg("select row_number() over (order by ST_Distance("
              "ST_Transform(ST_Point(s.lon, s.lat), 'EPSG:4326', 'EPSG:5070', true), b.pt)) rn "
              "from t s join u b on true")
    assert good.windows[0].order_roots == ["ST_DISTANCE"]
    assert good.windows[0].order_reprojected == [True]


def test_a_test_assay_could_not_look_at_is_reported_not_counted_as_a_pass(project_dir):
    """On a real public package every one of 38 tests sat on a column assay could not resolve --
    the models end in `select *` -- and the check reported "no structural findings", which looks
    exactly like a clean bill of health."""
    from dbt_assay.checks import unevaluable_tests
    from dbt_assay.manifest import Project
    from dbt_assay.parse import digest as dg

    p = Project.load(project_dir)
    d = {uid: dg(m.compiled, m.name) for uid, m in p.models.items() if m.readable}
    assert unevaluable_tests(p, d) == []          # this fixture resolves every column

    # a model whose output is unknown makes its tests unevaluable, and says which reason
    d["model.p.stg_bad_notnull"].output_columns = ["*"]
    d["model.p.stg_bad_notnull"].output_roots = {}
    blind = unevaluable_tests(p, d)
    assert blind and all(len(x) == 4 for x in blind)
    assert any("select *" in why for _m, _t, _c, why in blind)


def test_an_unparsed_model_makes_its_tests_unevaluable_too(project_dir):
    from dbt_assay.checks import unevaluable_tests
    from dbt_assay.manifest import Project
    from dbt_assay.parse import digest as dg

    p = Project.load(project_dir)
    d = {uid: dg(m.compiled, m.name) for uid, m in p.models.items() if m.readable}
    d["model.p.stg_bad_notnull"].ok = False
    assert any("could not be parsed" in why for _m, _t, _c, why in unevaluable_tests(p, d))


def test_taking_the_first_element_of_a_multivalued_field_is_detected():
    """sqlglot names SPLIT_PART's arguments rather than positioning them, and normalizes `arr[1]`
    to `arr[0]`. Reading positional args and looking for a literal 1 found nothing at all."""
    from dbt_assay.parse import digest as dg
    assert dg("select split_part(cases, ',', 1) as c from t").first_element_picks
    assert dg("select arr[1] as a from t").first_element_picks
    assert dg("select split(s, ',')[1] as s from t").first_element_picks
    # a deliberate, defensible index is not a first-match pick
    assert not dg("select split_part(x, ',', 2) as ok from t").first_element_picks


def test_parsing_a_structured_string_is_not_picking_from_a_list():
    """All 8 findings on a real project were deliberate parses: the street out of an address, the
    prefix of a license number, the first word of a status. The column's own name is what
    separates a structured string from a list of equivalent values."""
    from dbt_assay.checks.structural import _LISTY
    for parse in ("SPLIT_PART(street_address, ',', 1)", "SPLIT_PART(license_number, '-', 1)",
                  "SPLIT_PART(township, ' ', 1)", "SPLIT_PART(buyer_vertical, ' - ', 1)"):
        assert not _LISTY.search(parse), parse
    for pick in ("SPLIT_PART(associated_case_numbers, ',', 1)", "owner_names[1]",
                 "SPLIT_PART(contact_ids, ';', 1)", "SPLIT_PART(all_codes, ',', 1)"):
        assert _LISTY.search(pick), pick


def test_arbitrary_pick_evidence_carries_the_partition_as_written_without_moving_the_id():
    """*** THE EVIDENCE NAMED A PARTITION THE WINDOW DOES NOT USE. *** (25.9)

    `partition by matched_name`, with `matched_name` projected as `owner_key`: the resolved key is
    right for the finding's identity and wrong for a reader looking for the window.
    """
    from dbt_assay.checks.structural import Finding
    sql = ("select matched_name as owner_key, scraped_at, "
           "row_number() over (partition by matched_name order by scraped_at desc) as rn "
           "from raw.owners")
    d = digest(sql, "int_owners", "duckdb")
    w = d.windows[0]
    assert w.partition_by == ["matched_name"]
    ev = {"partition_by": w.partition_columns, "order_by": w.order_sql[:3]}
    before = Finding(check="arbitrary_pick", subject="model.p.int_owners", subject_name="int_owners",
                     file="", summary="s", detail="", evidence=ev)
    after = Finding(check="arbitrary_pick", subject="model.p.int_owners", subject_name="int_owners",
                    file="", summary="s", detail="",
                    evidence={**ev, "partition_as_written": w.partition_by})
    assert before.id == after.id, "the rulings on these findings must not move"


def test_feedback_n5_a_tie_the_output_cannot_see_is_not_an_arbitrary_pick():
    """When every column kept is a partition or ORDER BY key, a remaining tie is identical in all
    it keeps, so which row survives changes nothing. A star or any other column is not a proof."""
    from dbt_assay import parse
    base = ("with o as (select id_business, officer_name, trank, title from p), "
            "b as (select id_business, officer_name, title as officer_title from o "
            "qualify row_number() over (partition by id_business order by {}) = 1) "
            "select * from b")
    assert [w.picks_only_keys for w in parse.digest(base.format("trank, officer_name")).windows] \
        == [False]
    assert [w.picks_only_keys for w in parse.digest(
        base.format("trank, officer_name, title")).windows] == [True]
    star = ("select * from (select *, row_number() over (partition by k order by t) rn from x) "
            "where rn = 1")
    assert [w.picks_only_keys for w in parse.digest(star).windows] == [False]
    import inspect

    from dbt_assay.checks import structural
    assert 'getattr(w, "picks_only_keys", False)' in inspect.getsource(structural.arbitrary_pick)
