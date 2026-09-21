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
