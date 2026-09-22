"""The assertion side of the test checks: something asserted that was never true upstream.

*** THIS IS THE ONE A REAL OUTAGE PRODUCED, AND THE OUTAGE MOVED WHILE WE WATCHED. ***
`not_null` on `int_water_well_parcel.parcel_id` failed on one row of 49,034. The model was then
rewritten to an INNER join, so the join half of this check is correctly silent on it today -- and
the column is STILL `min(parcel_id)` over a grouped CTE, which is NULL for a group whose parcels
all have a null id, and the INNER join keeps that row because it joins on the address key. The
parent still holds 5,876 nulls in 2.7M rows and the child holds 0 in 48,648: the test passes, and
is one unlucky group from not passing. That is the failure mode, and it is why this check reads
the aggregate rather than only the join.
"""
import json
from pathlib import Path

import pytest

from dbt_assay import inventory as inv_mod
from dbt_assay.checks import structural
from dbt_assay.infer import Schema, derive_columns
from dbt_assay.manifest import Project
from dbt_assay.parse import digest

SQL = {
    # min() over a group: NULL when every input row in the group is NULL. The field case.
    "int_agg_min": "select k, min(pid) as pid, count(pid) as n_pid from raw.t group by k",
    # count() over a group: 0, never NULL. The exception, and the common one.
    "int_agg_count": "select k, count(*) as n from raw.t group by k",
    # count() wearing a FILTER clause. Reads as `filter` unless the classifier descends.
    "int_agg_filter": "select k, count(*) filter (where pid > 1) as n from raw.t group by k",
}
TESTS = [
    ("nn_pid", "not_null_int_agg_min_pid", "not_null", "pid", "model.p.int_agg_min"),
    ("nn_npid", "not_null_int_agg_min_n_pid", "not_null", "n_pid", "model.p.int_agg_min"),
    ("nn_n", "not_null_int_agg_count_n", "not_null", "n", "model.p.int_agg_count"),
    ("nn_fn", "not_null_int_agg_filter_n", "not_null", "n", "model.p.int_agg_filter"),
]


@pytest.fixture
def agg_target(tmp_path: Path) -> Path:
    nodes, parent_map, child_map = {}, {}, {}
    for name, sql in SQL.items():
        uid, path = f"model.p.{name}", f"models/intermediate/{name}.sql"
        nodes[uid] = {"resource_type": "model", "name": name, "unique_id": uid,
                      "original_file_path": path, "compiled_path": f"target/compiled/p/{path}",
                      "config": {"materialized": "table"}, "columns": {}, "description": "",
                      "package_name": "p", "depends_on": {"nodes": []}}
        parent_map[uid], child_map[uid] = [], []
        f = tmp_path / "target" / "compiled" / "p" / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(sql)
    for uid, name, kind, col, model_uid in TESTS:
        nodes[f"test.p.{uid}"] = {
            "resource_type": "test", "name": name, "column_name": col,
            "attached_node": model_uid, "config": {"severity": "ERROR"}, "description": "",
            "test_metadata": {"name": kind, "kwargs": {"column_name": col}},
            "depends_on": {"nodes": [model_uid]}}
        parent_map[f"test.p.{uid}"] = [model_uid]
    (tmp_path / "target" / "manifest.json").write_text(json.dumps({
        "metadata": {"project_name": "p", "dbt_version": "1.11.0", "adapter_type": "duckdb"},
        "nodes": nodes, "sources": {}, "parent_map": parent_map, "child_map": child_map}))
    return tmp_path / "target"


def _run(target: Path):
    p = Project.load(target)
    d = {uid: digest(m.compiled, m.name) for uid, m in p.models.items() if m.readable}
    sch = Schema.load(p, target)
    derive_columns(p, d, sch)
    entries = inv_mod.build(p, d, sch, None, {})
    return {(f.subject_name, f.evidence["column"]): f
            for f in structural.test_outruns_its_source(p, d, sch, entries)}


def test_a_null_preserving_aggregate_is_reported(agg_target):
    """`min()` over an all-NULL group is NULL, and the GROUP BY still emits the row."""
    got = _run(agg_target)
    assert ("int_agg_min", "pid") in got, sorted(got)
    assert "min()" in got[("int_agg_min", "pid")].summary


def test_count_is_the_exception_and_is_not_reported(agg_target):
    """`count(x)` over a group is 0, never NULL.

    On the field warehouse this is 16 of 23 aggregated `not_null` tests. A check that could not
    tell the aggregates apart would report all 23, and a list of 23 where 16 are correct is the
    shape that gets a check switched off.
    """
    got = _run(agg_target)
    assert ("int_agg_count", "n") not in got, got.get(("int_agg_count", "n"))
    assert ("int_agg_min", "n_pid") not in got, "count(pid) is 0 for an all-null group, not NULL"


def test_a_filter_clause_does_not_hide_which_aggregate_it_is(agg_target):
    """`count(*) FILTER (WHERE ...)` must read as a count, not as an unresolved `filter`.

    Before the classifier descended, two of the field warehouse's aggregated `not_null` tests read
    `filter`, which says an aggregate happened and hides which. Both were counts.
    """
    got = _run(agg_target)
    assert ("int_agg_filter", "n") not in got, "a FILTERed count is still a count"


def test_the_never_null_list_is_the_exception_not_the_rule(agg_target):
    """An aggregate nobody has thought about has to read as null-preserving.

    That is the safe direction for a check whose claim is "this assertion may not hold": a new
    aggregate defaults to being reported and is argued down, rather than defaulting to silence.
    """
    src = Path(structural.__file__).read_text()
    assert "NEVER_NULL = {" in src
    body = src.split("NEVER_NULL = {", 1)[1].split("}", 1)[0]
    assert "count" in body
    for agg in ("min", "max", "sum", "avg", "group_concat", "logical_or"):
        assert agg not in body, f"{agg} returns NULL for an all-NULL group; it is not an exception"


# --------------------------------------- an aggregate whose INPUT cannot be null is not a finding

AGG_SQL = {
    # the input carries a not_null test on its parent -> bool_or can never return NULL
    "stg_flags": "select k, is_sfha from raw.t",
    "int_guarded": "select k, bool_or(is_sfha) as intersects from {{ref('stg_flags')}} group by k",
    # a coalesce with a LITERAL tail cannot produce NULL, so nor can the aggregate
    "int_defaulted": "select k, string_agg(coalesce(label, 'unknown'), ', ') as labels "
                     "from raw.t group by k",
    # ...but a concatenation tail CAN be null, and must not be treated as a literal
    "int_piped": "select k, string_agg(coalesce(label, 'code ' || other), ', ') as labels "
                 "from raw.t group by k",
    # no test on the input, no default -> the finding stands
    "int_open": "select k, min(letter_date) as first_seen from raw.t group by k",
}
AGG_TESTS = [
    ("nn_flag", "not_null_stg_flags_is_sfha", "not_null", "is_sfha", "model.p.stg_flags"),
    ("nn_i", "not_null_int_guarded_intersects", "not_null", "intersects", "model.p.int_guarded"),
    ("nn_d", "not_null_int_defaulted_labels", "not_null", "labels", "model.p.int_defaulted"),
    ("nn_p", "not_null_int_piped_labels", "not_null", "labels", "model.p.int_piped"),
    ("nn_o", "not_null_int_open_first_seen", "not_null", "first_seen", "model.p.int_open"),
]


@pytest.fixture
def agg_guard_target(tmp_path: Path) -> Path:
    nodes, parent_map, child_map = {}, {}, {}
    for name, sql in AGG_SQL.items():
        uid, path = f"model.p.{name}", f"models/intermediate/{name}.sql"
        nodes[uid] = {"resource_type": "model", "name": name, "unique_id": uid,
                      "original_file_path": path, "compiled_path": f"target/compiled/p/{path}",
                      "config": {"materialized": "table"}, "columns": {}, "description": "",
                      "package_name": "p", "depends_on": {"nodes": []}}
        parent_map[uid], child_map[uid] = [], []
        f = tmp_path / "target" / "compiled" / "p" / path
        f.parent.mkdir(parents=True, exist_ok=True)
        # the compiled form: the ref is already resolved
        f.write_text(sql.replace("{{ref('stg_flags')}}", "main.stg_flags"))
    parent_map["model.p.int_guarded"] = ["model.p.stg_flags"]
    child_map["model.p.stg_flags"] = ["model.p.int_guarded"]
    for uid, name, kind, col, model_uid in AGG_TESTS:
        nodes[f"test.p.{uid}"] = {
            "resource_type": "test", "name": name, "column_name": col,
            "attached_node": model_uid, "config": {"severity": "ERROR"}, "description": "",
            "test_metadata": {"name": kind, "kwargs": {"column_name": col}},
            "depends_on": {"nodes": [model_uid]}}
        parent_map[f"test.p.{uid}"] = [model_uid]
    (tmp_path / "target" / "manifest.json").write_text(json.dumps({
        "metadata": {"project_name": "p", "dbt_version": "1.11.0", "adapter_type": "duckdb"},
        "nodes": nodes, "sources": {}, "parent_map": parent_map, "child_map": child_map}))
    return tmp_path / "target"


def test_an_input_with_a_not_null_test_cannot_make_the_aggregate_null(agg_guard_target):
    """*** RULED ON ALL SEVEN OF THIS CHECK'S OWN FINDINGS, AND THREE WERE WRONG. ***

    `bool_or(is_sfha)` where `stg_fema_flood_zones.is_sfha` carries a `not_null` test: the input
    is never NULL, so no group is ever entirely NULL, so the aggregate never is. Two of the seven
    were exactly that, against two that are right and whose inputs carry no such test and do hold
    nulls -- `letter_date` 1,300 of 17,193, `parcel_id` 5,876 of 2,732,101.

    The declaration is in the manifest. It costs nothing to read and it is the difference between
    a check that is right five times in five and one that is right five times in seven.
    """
    got = _run(agg_guard_target)
    assert ("int_guarded", "intersects") not in got, \
        "the aggregated column is declared not_null upstream; the aggregate cannot be NULL"
    assert ("int_open", "first_seen") in got, "a genuinely unguarded aggregate stopped firing"


def test_a_coalesce_with_a_literal_tail_cannot_make_the_aggregate_null(agg_guard_target):
    """The same fact `test_cannot_fail` reads from the other side."""
    got = _run(agg_guard_target)
    assert ("int_defaulted", "labels") not in got


def test_a_concatenation_tail_is_not_a_literal_tail(agg_guard_target):
    """*** `'code ' || other` IS NULL WHEN `other` IS. ***

    Found on the real warehouse: `listagg(coalesce(use_label, 'Unrecognised code ' || use_code))`
    looks defaulted and is not. Treating a DPipe as a literal would have suppressed a finding on
    the grounds that a NULL cannot happen, which is the exact direction this check must not get
    wrong.
    """
    got = _run(agg_guard_target)
    assert ("int_piped", "labels") in got, "a concatenated tail was treated as a literal default"


def test_an_aggregate_read_out_of_a_cte_is_not_assumed_safe(agg_guard_target):
    """Where the output expression is a bare column, there is no argument to examine.

    No evidence is not evidence of safety, which is the safe direction for a check whose claim is
    "this assertion may not hold".
    """
    from dbt_assay.checks.structural import _aggregate_input_cannot_be_null
    p = Project.load(agg_guard_target)
    d = {uid: digest(m.compiled, m.name) for uid, m in p.models.items() if m.readable}
    assert not _aggregate_input_cannot_be_null("model.p.nonexistent", "x", p, d)
