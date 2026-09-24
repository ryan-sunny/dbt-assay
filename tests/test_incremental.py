"""G-D: incremental models, read from the branch `dbt compile` never renders. Snowflake SQL."""
import hashlib
import json

import pytest

from dbt_assay import inventory, ledger, live
from dbt_assay.checks import incremental as inc
from dbt_assay.infer import Schema, derive_columns
from dbt_assay.manifest import Project
from dbt_assay.parse import digest

SRC = "raw.events"
HWM = "\n{% if is_incremental() %}\n  where event_at > (select max(event_at) from {{ this }})\n{% endif %}"
HWM_LB = ("\n{% if is_incremental() %}\n  where event_at > (select dateadd(day, -3, max(event_at))"
          " from {{ this }})\n{% endif %}")
DEDUPE = ("select id, event_at, amount, _loaded_at from " + SRC + " qualify row_number() over "
          "(partition by id order by _loaded_at desc, amount) = 1")
PLAIN = f"select id, event_at, amount, _loaded_at from {SRC}"

# name: (compiled sql, raw code, config)
MODELS = {
    "merge_no_key": (PLAIN, PLAIN + HWM_LB, {}),
    "merge_no_key_fixed": (DEDUPE, DEDUPE + HWM_LB, {"unique_key": "id"}),
    "key_unknown": (PLAIN, PLAIN + HWM_LB, {"unique_key": "id"}),
    "key_unknown_fixed": (DEDUPE, DEDUPE + HWM_LB, {"unique_key": "id"}),
    "no_lookback": (DEDUPE, DEDUPE + HWM, {"unique_key": "id"}),
    "no_lookback_fixed": (DEDUPE, DEDUPE + HWM_LB, {"unique_key": "id"}),
    "micro_zero": (PLAIN, PLAIN, {"incremental_strategy": "microbatch", "event_time": "event_at",
                                  "batch_size": "day", "lookback": 0}),
    "micro_fixed": (PLAIN, PLAIN, {"incremental_strategy": "microbatch", "event_time": "event_at",
                                   "batch_size": "day", "lookback": 3}),
    "no_arrival": ("select id, event_at, synced_on from " + SRC,
                   "select id, event_at, synced_on from " + SRC + HWM, {"unique_key": "id"}),
    "all_good": (DEDUPE, DEDUPE + HWM_LB, {"unique_key": "id",
                                           "on_schema_change": "append_new_columns"}),
}


@pytest.fixture
def snow(tmp_path):
    return build_snow(tmp_path)


def build_snow(tmp_path):
    """The fixture project, written in Snowflake SQL, as a function so a script can build it."""
    nodes = {}
    for name, (sql, raw, cfg) in MODELS.items():
        uid = f"model.w.{name}"
        path = f"models/marts/{name}.sql"
        nodes[uid] = {"resource_type": "model", "name": name, "original_file_path": path,
                      "schema": "analytics", "database": "PROD", "description": "",
                      "columns": {}, "raw_code": raw,
                      "config": {"materialized": "incremental", "meta": {}, **cfg},
                      "checksum": {"name": "sha256",
                                   "checksum": hashlib.sha256(sql.encode()).hexdigest()}}
        f = tmp_path / "target" / "compiled" / "w" / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(sql)
    (tmp_path / "target" / "manifest.json").write_text(json.dumps({
        "metadata": {"project_name": "w", "dbt_version": "1.9.0", "adapter_type": "snowflake"},
        "nodes": nodes, "sources": {}, "parent_map": {u: [] for u in nodes},
        "child_map": {u: [] for u in nodes}}))
    return tmp_path / "target"


def _load(target):
    p = Project.load(target)
    d = {uid: digest(m.compiled, m.name) for uid, m in p.models.items() if m.readable}
    sch = Schema.load(p, target)
    derive_columns(p, d, sch)
    return p, d, sch


def _fired(target, store=None):
    p, d, sch = _load(target)
    entries = inventory.build(p, d, sch, store=store)
    fs = live.all_findings(p, d, sch, entries, store=store)
    return {(f.check, f.subject_name) for f in fs if f.check.startswith(("incremental", "micro"))}


def test_the_config_and_the_incremental_branch_are_read(snow):
    p, d, _ = _load(snow)
    got = inc.read(p, d)
    m = got["model.w.no_lookback"]
    assert m.strategy == "merge" and m.strategy_from == "adapter default"
    assert m.filter_column == "event_at" and not m.filter_lookback
    assert got["model.w.no_lookback_fixed"].filter_lookback
    assert got["model.w.micro_zero"].lookback == 0 and got["model.w.micro_zero"].batch_size == "day"


def test_each_check_fires_on_its_model_and_not_on_the_corrected_copy(snow):
    fired = _fired(snow)
    for check, bad in (("incremental_merge_without_key", "merge_no_key"),
                       ("incremental_key_not_unique", "key_unknown"),
                       ("incremental_filter_without_lookback", "no_lookback"),
                       ("microbatch_without_lookback", "micro_zero")):
        assert (check, bad) in fired, (check, sorted(fired))
        assert (check, bad + "_fixed") not in fired and (check, "micro_fixed") not in fired, check
    assert not {x for x in fired if x[1] == "all_good"}, fired


def test_a_merge_model_with_a_key_and_a_lookback_raises_nothing(snow):
    assert not {x for x in _fired(snow) if x[1] in ("all_good", "no_lookback_fixed")}


def test_the_lateness_premise_is_unchecked_with_an_arrival_column_and_unmeasured(snow):
    p, d, sch = _load(snow)
    entries = inventory.build(p, d, sch, store=None)
    live.all_findings(p, d, sch, entries)
    led = ledger.last()
    lat = [x for x in led.premises.values() if x.prop == "max_lateness"
           and x.name != "no_arrival"]
    assert lat and all(x.status == ledger.UNCHECKED for x in lat), [x.evidence for x in lat]
    assert "_loaded_at" in lat[0].evidence[0].detail


def test_measured_lateness_beyond_the_lookback_breaks_it(snow, tmp_path):
    from dbt_assay.store import Store
    s = Store(str(tmp_path / "s.duckdb"))
    p, d, sch = _load(snow)
    rel = (sch.relation.get("model.w.micro_fixed") or "").replace('"', "").lower()
    s.con.execute("insert into observed_lateness values (?, 'event_at', '_loaded_at', ?, 10, "
                  "now(), 'test')", [rel, 5 * 86400.0])
    fired = _fired(snow, s)
    assert ("microbatch_without_lookback", "micro_fixed") in fired


def test_schema_change_ignored_fires_when_columns_moved(snow, tmp_path):
    from dbt_assay.store import Store
    s = Store(str(tmp_path / "s.duckdb"))
    old = "select id, event_at from raw.events"
    s.con.execute("insert into compiled_sql values (?, 'key_unknown', ?, now() - interval 1 day)",
                  [hashlib.sha256(old.encode()).hexdigest(), old])
    fired = _fired(snow, s)
    assert ("incremental_schema_change_ignored", "key_unknown") in fired
    assert not any(c == "incremental_schema_change_ignored" and m == "all_good"
                   for c, m in fired)


def test_lateness_sql_is_in_the_projects_dialect():
    got = inc.lateness_sql("PROD.analytics.t", "event_at", "_loaded_at", "snowflake")
    assert "DATEDIFF" in got.upper() and "PROD.analytics.t" in got


def test_probe_lateness_measures_through_the_runner_and_the_premise_reads_it(snow, tmp_path):
    from dbt_assay.probe import Result
    from dbt_assay.store import Store
    s = Store(str(tmp_path / "s.duckdb"))
    p, d, sch = _load(snow)
    entries = inventory.build(p, d, sch, store=s)
    sent = []

    def run(sql):
        sent.append(sql)
        return Result(rows=[{"late": 7200.0, "n": 50}])
    rows = inc.measure(p, d, sch, entries, s, run)
    assert rows and all(r["measured"] for r in rows if r["model"] != "no_arrival") and sent
    assert next(r for r in rows if r["model"] == "no_arrival")["measured"] is False
    assert "DATEDIFF" in sent[0].upper()
    # no_lookback allows 0: 2 hours late breaks it
    live.all_findings(p, d, sch, entries, store=s)
    led = ledger.last()
    got = [x for x in led.premises.values() if x.prop == "max_lateness" and x.name == "no_lookback"]
    assert got and got[0].status == ledger.BROKEN and "2.0 hour" in ledger.why(got[0])


def test_arrival_is_asked_only_where_no_name_says_it(snow):
    from dbt_assay import subject_kinds
    p, d, sch = _load(snow)
    subs = subject_kinds.arrival_candidates(p, d, sch)
    assert [x.key for x in subs] == ["model.w.no_arrival::arrival::synced_on"]
    assert subs[0].state["event_column"] == "event_at"


def test_no_arrival_column_leaves_lateness_unknown_and_the_finding_says_so(snow):
    p, d, sch = _load(snow)
    entries = inventory.build(p, d, sch, store=None)
    fs = live.all_findings(p, d, sch, entries)
    f = next(x for x in fs if x.check == "incremental_filter_without_lookback"
             and x.subject_name == "no_arrival")
    assert f.evidence["premise_status"] == "unknown"
    assert "no column says when a row arrived" in f.detail


def test_a_judged_arrival_column_is_used(snow):
    p, d, sch = _load(snow)
    entries = inventory.build(p, d, sch, store=None)
    e = next(x for x in entries if x.name == "no_arrival")
    e.judged = {"arrv": {"answer": "arrival_time", "probabilities": {"arrival_time": 0.9},
                         "context": "no_arrival.synced_on"}}
    i = inc.read(p, d)["model.w.no_arrival"]
    assert inc.arrival_column(i, e, sch) == ("synced_on", "the arrival_time_column judgment, 0.90")


def test_the_model_pane_shows_the_incremental_section_with_its_flags(snow, tmp_path):
    from playwright.sync_api import sync_playwright
    from typer.testing import CliRunner

    from dbt_assay.cli import app
    store = tmp_path / "s.duckdb"
    CliRunner().invoke(app, ["check", "--target", str(snow), "--store", str(store)])
    out = tmp_path / "p.html"
    r = CliRunner().invoke(app, ["page", str(out), "--target", str(snow), "--store", str(store)])
    assert r.exit_code == 0, r.output
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        try:
            errors = []
            for w in (1500, 420):
                page = b.new_page(viewport={"width": w, "height": 900})
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(out.as_uri() + "#models")
                page.wait_for_timeout(300)
                page.evaluate("() => GO.models('no_lookback')")
                text = page.locator(".detail").first.inner_text()
                assert "incremental" in text.lower() and "no lookback" in text
                assert "incremental filter without lookback" in text
                assert "adapter default" in text
                assert page.evaluate("() => document.documentElement.scrollWidth") <= w
                page.close()
            assert not errors, errors
        finally:
            b.close()


def test_a_branch_that_opens_with_a_comment_is_still_read():
    block = ("-- idempotent within a day\n"
             "where current_date not in (select d from {{ this }})\n"
             "   or true -- note")
    col, sql, lb, read_ = inc.parse_filter(block, "duckdb")
    assert read_ and col == ""
    col, sql, lb, read_ = inc.parse_filter(
        "-- the mark\n/* high-water */ and loaded > (select max(loaded) from {{ this }})",
        "snowflake")
    assert read_ and col == "loaded" and not lb and "{{ this }}" in sql


@pytest.mark.skipif(__import__("dbt_assay.toolchain", fromlist=["x"]).lake_for_build() is None,
                    reason="no Lean toolchain at the pinned version here")
def test_a_row_by_row_merge_model_is_proven_equal_to_its_full_refresh(snow, tmp_path):
    from dbt_assay import prove
    from dbt_assay.store import Store
    s = Store(str(tmp_path / "s.duckdb"))
    p, d, sch = _load(snow)
    entries = inventory.build(p, d, sch, store=s)
    rep = prove.run(p, d, sch, entries, s, snow, say=lambda *_: None)
    got = {(r["model_name"], r["property"]): r for r in rep["rows"]}
    ok = got[("key_unknown", "incremental")]
    assert ok["status"] == "proven" and ok["rule"] == "incremental_equals_full_refresh"
    props = {x["property"] for x in ok["premises"]}
    assert props == {"max_lateness", "unique", "not_null"}
    # a dedupe inside the model is not row by row: said, not attempted
    assert got[("all_good", "incremental")]["status"] == "not_attempted"
    assert "row by row" in got[("all_good", "incremental")]["missing"]
