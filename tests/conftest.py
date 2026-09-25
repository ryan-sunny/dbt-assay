"""A synthetic dbt project on disk, so the tests exercise the real loader rather than a mock."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True, scope="session")
def _digest_cache_in_a_temp_folder(tmp_path_factory):
    """The parse cache and dbt's target folder are exercised by every test that uses them, in
    folders of the run's own, never the person's ~/.cache. (ASSAY_CACHE is not used: it also moves
    the Lean toolchain.)"""
    from dbt_assay import digestcache, probe
    where = tmp_path_factory.mktemp("digests")
    was = digestcache.cache_dir
    digestcache.cache_dir = lambda: where
    probe.SHOW_ROOT = str(tmp_path_factory.mktemp("dbt-target"))
    yield
    digestcache.cache_dir = was
    probe.SHOW_ROOT = None

# One real description, and one boilerplate applied to three models so the repetition rule has
# something to catch. A project where every description is unique cannot test it.
_BOILER = "Staging model: light cleanup of one raw source toward the common lead schema."
DESCRIPTIONS = {
    "stg_bad_notnull": "One row per id, with amount never null because it is coalesced to zero.",
    "stg_ok_notnull": _BOILER,
    "stg_bad_accepted": _BOILER,
    "stg_bad_tilde": _BOILER,
}


def _model(uid: str, name: str, path: str, **kw) -> dict:
    return {"resource_type": "model", "name": name, "original_file_path": path,
            "schema": kw.get("schema", "main"), "description": kw.get("description", ""),
            "columns": kw.get("columns", {}), "config": {"materialized": "view", "meta": {}}}


def _test(uid: str, name: str, kind: str, column: str, model_uid: str, **kwargs) -> dict:
    return {"resource_type": "test", "name": name, "column_name": column,
            "attached_node": model_uid, "config": {"severity": "ERROR"}, "description": "",
            "test_metadata": {"name": kind, "kwargs": {"column_name": column, **kwargs}},
            "depends_on": {"nodes": [model_uid]}}


SQL = {
    # a not_null test on this cannot fail: COALESCE with a literal tail
    "stg_bad_notnull": "select id, coalesce(amount, 0) as amount from raw.t",
    # ...and this one can: the tail is another column
    "stg_ok_notnull": "select id, coalesce(amount, backup_amount) as amount from raw.t",
    # unique on the sole group by key cannot fail
    "int_bad_unique": "select section_id, count(*) as n from raw.t group by section_id",
    # two group by keys: unique on one of them CAN fail
    "int_ok_unique": "select section_id, case_id, count(*) as n from raw.t group by section_id, case_id",
    # accepted_values covering every CASE branch cannot fail
    "stg_bad_accepted":
        "select id, case when k = 'a' then 'A' when k = 'b' then 'B' else 'C' end as cls from raw.t",
    # ranking by ST_Distance (degrees) -- the defect
    "int_bad_degrees":
        "select id, row_number() over (partition by id order by ST_Distance(a.geom, b.pt)) as rn "
        "from raw.a a join raw.b b on true",
    # ST_Distance only in a PROJECTION, ranked by a meter measure -- must NOT fire
    "int_ok_degrees":
        "select id, ST_Distance(a.geom, b.pt) as deg, "
        "row_number() over (partition by id order by ST_Distance_Sphere(a.geom, b.pt)) as rn "
        "from raw.a a join raw.b b on true",
    # DuckDB `~` against a pattern with no metacharacters: a full match that finds nothing
    "stg_bad_tilde": "select id from raw.t where name ~ 'Denver'",
    "stg_ok_tilde": "select id from raw.t where name ~ '.*Denver.*'",
    # *** TWO MODELS NAMING THE SAME CONCEPT DIFFERENTLY. ***
    # Without a pair like this the fixture produces zero align candidates at the real threshold,
    # so `align`'s state builder could not be exercised by anything -- and a builder no test
    # rebuilds is a builder nothing proves can be rebuilt.
    "stg_customer_a": "select customer_id, customer_name, order_total from raw.a",
    "stg_customer_b": "select cust_id, cust_name, order_amount from raw.b",
}

TESTS = [
    ("t1", "not_null_stg_bad_notnull_amount", "not_null", "amount", "model.p.stg_bad_notnull", {}),
    ("t2", "not_null_stg_ok_notnull_amount", "not_null", "amount", "model.p.stg_ok_notnull", {}),
    ("t3", "unique_int_bad_unique_section_id", "unique", "section_id", "model.p.int_bad_unique", {}),
    ("t4", "unique_int_ok_unique_section_id", "unique", "section_id", "model.p.int_ok_unique", {}),
    ("t5", "accepted_values_stg_bad_accepted_cls", "accepted_values", "cls",
     "model.p.stg_bad_accepted", {"values": ["A", "B", "C"]}),
]

LAYER = {"stg": "staging", "int": "intermediate"}


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    nodes, parent_map, child_map = {}, {}, {}
    for name, sql in SQL.items():
        layer = LAYER.get(name.split("_")[0], "marts")
        path = f"models/{layer}/{name}.sql"
        uid = f"model.p.{name}"
        nodes[uid] = _model(uid, name, path, description=DESCRIPTIONS.get(name, ""))
        # dbt records a sha256 of every model's source file, and `assay stale` reads it. A fixture
        # without one cannot exercise the one thing that makes staleness free to detect.
        nodes[uid]["checksum"] = {"name": "sha256",
                                  "checksum": hashlib.sha256(sql.encode()).hexdigest()}
        parent_map[uid], child_map[uid] = [], []
        f = tmp_path / "target" / "compiled" / "p" / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(sql)

    # one real edge so graph code is exercised
    parent_map["model.p.int_bad_unique"] = ["model.p.stg_bad_notnull"]
    child_map["model.p.stg_bad_notnull"] = ["model.p.int_bad_unique"]

    for uid, name, kind, col, model_uid, kw in TESTS:
        nodes[f"test.p.{uid}"] = _test(uid, name, kind, col, model_uid, **kw)
        parent_map[f"test.p.{uid}"] = [model_uid]

    manifest = {
        # The `~` fixtures are DuckDB semantics, so the fixture says so rather than relying on a
        # default. A manifest that names no adapter is its own test, in test_dialect.py.
        "metadata": {"project_name": "p", "dbt_version": "1.11.0", "adapter_type": "duckdb"},
        "nodes": nodes, "sources": {}, "parent_map": parent_map, "child_map": child_map,
    }
    (tmp_path / "target" / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path / "target"


def require_chromium():
    """Skip, saying why, where playwright is installed and its browser is not (the release
    workflow). The same rule as test_the_page_runs_in_a_browser's fixture, for a test elsewhere
    that opens the page: 0.52.0's publish failed on two that launched chromium directly."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            pw.chromium.launch().close()
    except Exception as e:                                       # noqa: BLE001
        pytest.skip(f"chromium is not installed here: `uv run playwright install chromium` "
                    f"({str(e)[:120]})")
