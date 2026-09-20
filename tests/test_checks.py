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
