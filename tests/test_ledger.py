"""The premise ledger: what the findings rest on, with its evidence and status."""
import json

from dbt_assay import inventory, ledger, live
from dbt_assay.infer import Schema, derive_columns
from dbt_assay.manifest import Project
from dbt_assay.parse import digest
from dbt_assay.store import Store


def _load(project_dir):
    p = Project.load(project_dir)
    d = {uid: digest(m.compiled, m.name) for uid, m in p.models.items() if m.readable}
    sch = Schema.load(p, project_dir)
    derive_columns(p, d, sch)
    return p, d, sch


def _unique_test(p, model):
    return next(t for t in p.tests if t.tests_model == model and t.kind == "unique")


def _run_results(project_dir, which, results):
    (project_dir / "run_results.json").write_text(json.dumps({
        "metadata": {"generated_at": "2026-09-20T10:00:00Z"}, "args": {"which": which},
        "results": [{"unique_id": u, "status": s} for u, s in results.items()]}))


def test_a_declared_key_whose_test_never_ran_is_unchecked(project_dir):
    p, d, sch = _load(project_dir)
    led = ledger.build(p, sch, [], None, tests={"test.other": ("pass", "2026-09-20")},
                       observed={})
    t = _unique_test(p, "model.p.int_bad_unique")
    prem = ledger.unique(led, "model.p.int_bad_unique", [t.column])
    assert prem.status == ledger.UNCHECKED
    assert any("never ran" in e.detail and t.name in e.detail for e in prem.evidence)


def test_with_no_results_read_at_all_it_says_so(project_dir):
    p, d, sch = _load(project_dir)
    led = ledger.build(p, sch, [], None, tests={}, observed={})
    t = _unique_test(p, "model.p.int_bad_unique")
    prem = ledger.unique(led, "model.p.int_bad_unique", [t.column])
    assert prem.status == ledger.UNCHECKED
    assert "no test results were read" in prem.evidence[0].detail


def test_a_passing_test_holds_and_a_failing_one_breaks(project_dir):
    p, d, sch = _load(project_dir)
    t = _unique_test(p, "model.p.int_bad_unique")
    for got, want in (("pass", ledger.HOLDING), ("fail", ledger.BROKEN),
                      ("warn", ledger.BROKEN), ("skipped", ledger.UNCHECKED)):
        led = ledger.build(p, sch, [], None, tests={t.unique_id: (got, "2026-09-20")},
                           observed={})
        assert ledger.unique(led, "model.p.int_bad_unique", [t.column]).status == want, got


def test_one_counted_duplicate_beats_a_passing_test(project_dir):
    """Measured false is enough. A passing test on a sample, or before today's load, is not."""
    from dbt_assay.probe import Observation
    p, d, sch = _load(project_dir)
    t = _unique_test(p, "model.p.int_bad_unique")
    rel = (sch.relation.get("model.p.int_bad_unique") or "").replace('"', "").lower()
    obs = {rel: {t.column: Observation(rel, t.column, 100, 100, 82, "has_duplicates",
                                       observed_at="2026-09-21 08:00:00")}}
    led = ledger.build(p, sch, [], None, tests={t.unique_id: ("pass", "2026-09-20")},
                       observed=obs)
    prem = ledger.unique(led, "model.p.int_bad_unique", [t.column])
    assert prem.status == ledger.BROKEN
    assert "18 duplicate" in ledger.why(prem)


def test_a_sampled_count_never_holds_a_premise(project_dir):
    from dbt_assay.probe import Observation
    p, d, sch = _load(project_dir)
    rel = (sch.relation.get("model.p.int_ok_unique") or "").replace('"', "").lower()
    obs = {rel: {"case_id": Observation(rel, "case_id", 100, 100, 100, "unique", sampled=True)}}
    led = ledger.build(p, sch, [], None, tests={}, observed=obs)
    assert ledger.unique(led, "model.p.int_ok_unique", ["case_id"]).status == ledger.UNKNOWN


def test_the_id_is_stable_and_ignores_column_order():
    a = ledger.Premise("model.p.x", "x", ("b", "a"))
    b = ledger.Premise("model.p.x", "x", ("a", "b"))
    assert a.id == b.id
    assert a.id != ledger.Premise("model.p.x", "x", ("a", "b"), "not_null").id


def test_a_declared_grain_on_a_never_run_test_is_not_firm(project_dir):
    """G-A: the value stands; `firm` goes, and the reason names the test."""
    p, d, sch = _load(project_dir)
    e = {x.uid: x for x in inventory.build(p, d, sch, store=None)}["model.p.int_bad_unique"]
    assert e.grain.source == "declared" and e.grain.value == ["section_id"]
    assert not e.grain.firm
    assert e.grain.premise["status"] == ledger.UNCHECKED
    assert not e.grain.resting_on, "an unrun test is not a judgment's unresolved column"


def test_a_build_s_results_make_it_firm_and_a_compile_s_do_not(project_dir):
    p, d, sch = _load(project_dir)
    t = _unique_test(p, "model.p.int_bad_unique")
    _run_results(project_dir, "compile", {t.unique_id: "success"})
    e = {x.uid: x for x in inventory.build(p, d, sch, store=None)}["model.p.int_bad_unique"]
    assert not e.grain.firm, "a compile's `success` is not a test result"
    _run_results(project_dir, "build", {t.unique_id: "pass"})
    e = {x.uid: x for x in inventory.build(p, d, sch, store=None)}["model.p.int_bad_unique"]
    assert e.grain.firm


def test_test_status_keeps_the_last_result(tmp_path):
    s = Store(str(tmp_path / "s.duckdb"))
    ledger.record_test_status(s, {"test.a": ("pass", "2026-09-01 00:00:00")}, "elementary")
    ledger.record_test_status(s, {"test.a": ("fail", "2026-09-03 00:00:00")}, "elementary")
    ledger.record_test_status(s, {"test.a": ("pass", "2026-09-02 00:00:00")}, "run_results")
    assert ledger.test_status(s)["test.a"][0] == "fail"


def test_since_carries_across_runs_while_the_status_holds(tmp_path, project_dir):
    p, d, sch = _load(project_dir)
    s = Store(str(tmp_path / "s.duckdb"))
    t = _unique_test(p, "model.p.int_bad_unique")

    def run(rid, at, status):
        s.con.execute("insert into runs (run_id, started_at, project) values (?, ?, 'p')",
                      [rid, at])
        led = ledger.build(p, sch, [], None, tests={t.unique_id: (status, at)}, observed={})
        led.use(ledger.unique(led, "model.p.int_bad_unique", [t.column]), "grain",
                "model.p.int_bad_unique", "model.p.int_bad_unique")
        ledger.write(s, rid, led)
        return s.con.execute("select status, since from premises where run_id = ?",
                             [rid]).fetchone()

    assert run("r1", "2026-09-01 00:00:00", "pass") == ("holding", _ts("2026-09-01"))
    assert run("r2", "2026-09-02 00:00:00", "pass")[1] == _ts("2026-09-01")
    got = run("r3", "2026-09-03 00:00:00", "fail")
    assert got == ("broken", _ts("2026-09-03"))
    moved = ledger.changes(s, "r3")
    assert len(moved) == 1 and moved[0][3:5] == ("holding", "broken")
    line = ledger.change_lines(moved)[0]
    assert "broke" in line and "`section_id` unique in `int_bad_unique`" in line
    assert ledger.change_lines([("x", '["a"]', "unique", "unchecked", "holding", "[]")] * 3) == [
        "[green]3 now holding[/] [dim](were unchecked)[/]"]
    assert s.con.execute("select model, dependent_kind from premise_uses where run_id='r3'"
                         ).fetchone() == ("model.p.int_bad_unique", "grain")


def _ts(day):
    from datetime import datetime
    return datetime.fromisoformat(day + " 00:00:00")


def test_all_findings_leaves_its_ledger_for_the_caller(project_dir):
    p, d, sch = _load(project_dir)
    entries = inventory.build(p, d, sch, store=None)
    live.all_findings(p, d, sch, entries)
    led = ledger.last()
    assert led is not None and ledger.active() is None
    assert any(u.kind == "grain" for u in led.uses)


def test_the_command_mcp_and_the_page_read_the_same_premises(project_dir, tmp_path):
    """`assay premises --json`, MCP `premises()` and the page's rows are one list."""
    from typer.testing import CliRunner

    from dbt_assay import explore
    from dbt_assay.cli import app
    from dbt_assay.mcp_server import Backend
    store = str(tmp_path / "s.duckdb")
    CliRunner().invoke(app, ["check", "--target", str(project_dir), "--store", store])
    r = CliRunner().invoke(app, ["premises", "--target", str(project_dir), "--store", store,
                                 "--json"])
    assert r.exit_code == 0, r.output
    cli = json.loads(r.output)
    assert cli["premises"] and not cli["tests_read"] and "no test results" in cli["note"]
    be = Backend(str(project_dir), store, str(tmp_path))
    mcp = be.premises()
    assert [p["id"] for p in mcp["premises"]] == [p["id"] for p in cli["premises"]]
    one = be.premises(model="int_bad_unique")
    assert one["premises"] and all(
        p["relation"] == "model.p.int_bad_unique"
        or any(u["model"] == "model.p.int_bad_unique" for u in p["uses"])
        for p in one["premises"])
    assert be.premises(model="nope").get("error")
    p, d, sch = _load(project_dir)
    entries = inventory.build(p, d, sch, store=None)
    live.all_findings(p, d, sch, entries)
    page = explore._premises(ledger.last(), None, [])["premises"]
    assert [x["id"] for x in page] == [x["id"] for x in cli["premises"]]
    r = CliRunner().invoke(app, ["premises", "--target", str(project_dir), "--store", store,
                                 "--status", "unchecked"])
    assert r.exit_code == 0 and ("never ran" in r.output or "no results read" in r.output), r.output
    r = CliRunner().invoke(app, ["premises", "--target", str(project_dir), "--status", "nah"])
    assert r.exit_code == 2
