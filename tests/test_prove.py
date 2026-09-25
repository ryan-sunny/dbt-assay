"""L2: per-model certificates, checked by Lean, and the parse round trip."""
import hashlib
import json

import pytest

from dbt_assay import inventory, ledger, parsecheck, prove, toolchain
from dbt_assay.infer import Schema, derive_columns
from dbt_assay.manifest import Project
from dbt_assay.parse import digest
from dbt_assay.store import Store

MODELS = {
    "stg_parent": "select id, name from raw.parent",
    "covered": "select c.k, p.name from raw.kids c join main.stg_parent p on c.pid = p.id",
    "uncovered": "select c.k, p.name from raw.kids c join main.stg_parent p on c.pname = p.name",
    "grouped": "select k, count(*) as n from raw.kids group by k",
}


def build(tmp_path, models=None):
    nodes = {}
    models = models or MODELS
    for name, sql in models.items():
        uid = f"model.p.{name}"
        path = f"models/{name}.sql"
        nodes[uid] = {"resource_type": "model", "name": name, "original_file_path": path,
                      "schema": "main", "database": "", "description": "", "columns": {},
                      "config": {"materialized": "table", "meta": {}},
                      "checksum": {"name": "sha256",
                                   "checksum": hashlib.sha256(sql.encode()).hexdigest()}}
        f = tmp_path / "target" / "compiled" / "p" / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(sql)
    nodes["test.p.unique_parent_id"] = {
        "resource_type": "test", "name": "unique_stg_parent_id", "column_name": "id",
        "attached_node": "model.p.stg_parent", "config": {"severity": "ERROR"},
        "test_metadata": {"name": "unique", "kwargs": {"column_name": "id"}},
        "depends_on": {"nodes": ["model.p.stg_parent"]}}
    pm = {u: [] for u in nodes}
    kids = [u for u in nodes if u.startswith("model.") and u != "model.p.stg_parent"
            and "stg_parent" in (models.get(u.split(".")[-1]) or "")]
    for u in kids:
        pm[u] = ["model.p.stg_parent"]
    cm = {u: [] for u in nodes}
    cm["model.p.stg_parent"] = kids
    (tmp_path / "target" / "manifest.json").write_text(json.dumps({
        "metadata": {"project_name": "p", "adapter_type": "duckdb"}, "nodes": nodes,
        "sources": {}, "parent_map": pm, "child_map": cm}))
    return tmp_path / "target"


def _load(target):
    p = Project.load(target)
    d = {u: digest(m.compiled, m.name) for u, m in p.models.items() if m.readable}
    sch = Schema.load(p, target)
    derive_columns(p, d, sch)
    return p, d, sch


needs_lean = pytest.mark.skipif(toolchain.lake_for_build() is None,
                                reason="no Lean toolchain at the pinned version here")


def test_the_obligations_state_each_join_against_the_parents_key(tmp_path):
    target = build(tmp_path)
    p, d, sch = _load(target)
    entries = inventory.build(p, d, sch, store=None)
    led = ledger.build(p, sch, entries, None, tests={}, observed={})
    obs = {(o.name, o.prop): o for o in prove.obligations(p, d, sch, entries, led)}
    cov = obs[("covered", "no_fanout:stg_parent")]
    assert "inner_join_no_fanout" in cov.lean and 'Unique ["id"] R' in cov.lean
    assert not cov.missing
    unc = obs[("uncovered", "no_fanout:stg_parent")]
    assert "join on id too" in unc.missing
    assert obs[("grouped", "grain")].rule == "group_by_unique"


@needs_lean
def test_lean_proves_the_covered_join_and_refutes_the_uncovered_one(tmp_path):
    target = build(tmp_path)
    p, d, sch = _load(target)
    s = Store(str(tmp_path / "s.duckdb"))
    entries = inventory.build(p, d, sch, store=s)
    rep = prove.run(p, d, sch, entries, s, target, say=lambda *_: None)
    got = {(r["model_name"], r["property"]): r for r in rep["rows"]}
    assert got[("covered", "no_fanout:stg_parent")]["status"] == "proven"
    assert got[("grouped", "grain")]["status"] == "proven"
    bad = got[("uncovered", "no_fanout:stg_parent")]
    assert bad["status"] == "not_proven" and "is false" in bad["detail"], bad["detail"]
    assert (target / "assay" / "lean" / "Models" / "covered.lean").exists()
    # unchanged files are not proven again
    again = prove.run(p, d, sch, entries, s, target, say=lambda *_: None)
    assert again["checked_now"] == 0 and again["reused"] == rep["certificates"]


@needs_lean
def test_a_broken_premise_loses_the_guarantee_without_lean(tmp_path, monkeypatch):
    target = build(tmp_path)
    p, d, sch = _load(target)
    s = Store(str(tmp_path / "s.duckdb"))
    entries = inventory.build(p, d, sch, store=s)
    prove.run(p, d, sch, entries, s, target, say=lambda *_: None)
    rel = (sch.relation.get("model.p.stg_parent") or "").replace('"', "").lower()
    s.con.execute("insert into observed_keys (relation, column_name, row_count, non_null, "
                  "distinct_ct, status, detail, observed_at, via, minimality, sampled, "
                  "sample_pct) values (?, 'id', 10, 10, 7, 'has_duplicates', '', now(), 't', "
                  "'', false, 0)", [rel])
    monkeypatch.setattr(prove, "check_files", lambda *a, **k: pytest.fail("Lean ran"))
    from dbt_assay import live
    rep = live.proofs_report(p, d, sch, entries, s, model="covered")
    (r,) = [x for x in rep["proofs"] if x["property"] == "no_fanout:stg_parent"]
    assert r["guarantee"] == "lost" and "3 duplicate" in r["lost_because"]


def test_nothing_is_proven_when_lean_did_not_check_the_file(tmp_path):
    f = tmp_path / "M.lean"
    f.write_text("import Assay\n\ntheorem m__a : True := trivial\n")
    o = prove.Obligation("m", "m", "", "a", "s")
    prove._mark(f, [o], "no such file or directory (error code: 2)", 1)
    assert o.status == prove.NOT_PROVEN
    o2 = prove.Obligation("m", "m", "", "a", "s")
    prove._mark(f, [o2], "M.lean:1:0: error: unknown module prefix 'Assay'", 1)
    assert o2.status == prove.NOT_PROVEN


def test_the_parse_round_trip_agrees_on_a_plain_model(tmp_path):
    target = build(tmp_path)
    p, d, sch = _load(target)
    st, detail, n = parsecheck.check_duckdb(p, sch, MODELS["grouped"], "duckdb")
    assert st in (ledger.HOLDING, ledger.UNCHECKED), detail


def test_a_parse_that_changes_the_result_is_broken(tmp_path, monkeypatch):
    target = build(tmp_path)
    p, d, sch = _load(target)
    sql = "select id, name from main.stg_parent where id > 1"
    good = parsecheck.check_duckdb(p, sch, sql, "duckdb")
    assert good[0] == ledger.HOLDING, good
    monkeypatch.setattr(parsecheck, "printed", lambda s, dl: s.replace("> 1", ">= 1"))
    bad = parsecheck.check_duckdb(p, sch, sql, "duckdb")
    assert bad[0] == ledger.BROKEN and "differ" in bad[1]


def test_an_order_dependent_aggregate_is_not_evidence(tmp_path, monkeypatch):
    assert parsecheck.order_dependent("select string_agg(name, ',') from t", "duckdb")
    assert not parsecheck.order_dependent(
        "select string_agg(name, ',' order by name) from t", "duckdb")


def test_a_udf_is_named_as_the_reason(tmp_path):
    msg = parsecheck._why_not_run(
        "Catalog Error: Scalar Function with name canon_addr_key does not exist!")
    assert "canon_addr_key" in msg and "outside its SQL" in msg


def test_the_parse_premise_reads_only_the_current_file(tmp_path):
    target = build(tmp_path)
    p, d, sch = _load(target)
    s = Store(str(tmp_path / "s.duckdb"))
    parsecheck.run(p, d, sch, s, say=lambda *_: None)
    led = ledger.build(p, sch, [], s, tests={}, observed={})
    pf = ledger.parse_faithful(led, "model.p.covered", s)
    assert pf.status in (ledger.HOLDING, ledger.UNCHECKED) and pf.evidence
    p.models["model.p.covered"].checksum = "changed"
    led = ledger.build(p, sch, [], s, tests={}, observed={})
    assert "earlier version" in ledger.parse_faithful(led, "model.p.covered", s).evidence[0].detail


@needs_lean
def test_an_agent_proof_is_checked_and_kept_and_a_wrong_one_gets_leans_error(tmp_path):
    from dbt_assay import proofwork
    target = build(tmp_path)
    p, d, sch = _load(target)
    s = Store(str(tmp_path / "s.duckdb"))
    entries = inventory.build(p, d, sch, store=s)
    g = proofwork.goal(p, d, sch, entries, s, "covered", "no_fanout:stg_parent")
    assert g["goal"].endswith("sorry") and g["premises"][0]["hypothesis"].startswith("p_")
    assert any(x["name"] == "inner_join_no_fanout" for x in g["lemmas"])
    hyp = g["premises"][0]["hypothesis"]
    ok = proofwork.check(p, d, sch, entries, s, "covered", "no_fanout:stg_parent",
                         f"inner_join_no_fanout (us := [\"id\"]) (by decide) {hyp}")
    assert ok["status"] == "proven", ok
    rows = [r for r in prove.stored(s) if r["model_name"] == "covered"]
    assert rows[0]["written_by"] == "agent"
    wrong = proofwork.check(p, d, sch, entries, s, "covered", "no_fanout:stg_parent", "rfl")
    assert wrong["status"] == "not_proven" and "error" in wrong["lean"]


def test_a_proof_with_sorry_or_an_axiom_is_refused_before_lean(tmp_path):
    from dbt_assay import proofwork
    target = build(tmp_path)
    p, d, sch = _load(target)
    entries = inventory.build(p, d, sch, store=None)
    for bad in ("by sorry", "by native_decide", "by\n  set_option maxHeartbeats 0 in exact x"):
        got = proofwork.check(p, d, sch, entries, None, "covered", "no_fanout:stg_parent", bad)
        assert got["status"] == "refused", (bad, got)
    got = proofwork.check(p, d, sch, entries, None, "covered", "no_fanout:stg_parent", "x",
                          helpers="axiom cheat : False")
    assert got["status"] == "refused"


def test_export_carries_premises_proofs_and_conformance(tmp_path):
    from dbt_assay import export
    assert {"premises", "premise_uses", "proofs", "conformance"} <= set(export.TABLES)
    s = Store(str(tmp_path / "s.duckdb"))
    s.con.execute("insert into proofs (model, model_name, property, written_by, status, source) "
                  "values ('m', 'm', 'grain', 'agent', 'proven', 'theorem x : True := trivial')")
    got = {e.table: e.rows for e in export.to_seeds(s, tmp_path / "seeds")}
    assert got.get("proofs") == 1
    from typer.testing import CliRunner

    from dbt_assay.cli import app
    s.close()
    r = CliRunner().invoke(app, ["prove", "--store", str(tmp_path / "s.duckdb"),
                                 "--export-proofs", str(tmp_path / "out")])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "out" / "m__grain.lean").read_text().startswith("theorem x")


# --- L1 (sunny-data feedback): what a join actually reads --------------------------------------

def test_the_parser_knows_what_a_join_target_is_unique_on():
    from dbt_assay.parse import digest
    sql = ("with per as (select k, max(v) as m from raw.t group by 1), "
           "latest as (select k, v from (select k, v, row_number() over (partition by k order by v) "
           "as rn from raw.t) where rn = 1), "
           "flt as (select k, v from raw.u where v > 0), "
           "ds as (select distinct k from raw.w) "
           "select a.k from raw.a a "
           "left join per p on p.k = a.k left join latest l on l.k = a.k "
           "left join flt f on f.k = a.k left join ds on ds.k = a.k "
           "left join raw.geo g on st_contains(g.geom, a.pt) "
           "left join (select k from raw.x group by k) x on x.k = a.k")
    js = {(j.target_cte or j.target_alias): j for j in digest(sql, "m").joins}
    assert js["per"].target_unique == ["k"] and js["per"].target_unique_by == "group_by"
    assert js["latest"].target_unique == ["k"] and js["latest"].target_unique_by == "row_number"
    assert js["flt"].target_unique is None and js["flt"].target_filter_of == "u"
    assert js["ds"].target_unique == ["k"]
    assert not js["g"].equi
    assert js["x"].target_unique == ["k"] and js["x"].equi_keys == ["k"]


def test_a_model_reading_a_grouped_cte_starts_from_its_grain():
    from dbt_assay.parse import digest
    d = digest("with e as (select k, min(x) as x from raw.t group by k) "
               "select e.k, e.x from e join raw.u u on u.k = e.k", "m")
    assert d.from_unique[0] == ["k"] and d.from_sources == ["t"]


GROUPED = {
    "stg_parent": "select id, name from raw.parent",
    "grouped_join": ("select a.id from raw.a a left join (select id, count(*) as n from "
                     "main.stg_parent group by id) g on g.id = a.id"),
    "spatial": ("select a.id from raw.a a join main.stg_parent p "
                "on st_contains(p.name, a.pt)"),
}


@needs_lean
def test_a_join_onto_a_grouped_subquery_is_proven_with_no_premise(tmp_path, monkeypatch):
    target = build(tmp_path, GROUPED)
    p, d, sch = _load(target)
    s = Store(str(tmp_path / "s.duckdb"))
    # the parent's key is counted duplicated: it must not matter to the grouped join
    rel = (sch.relation.get("model.p.stg_parent") or "").replace('"', "").lower()
    s.con.execute("insert into observed_keys (relation, column_name, row_count, non_null, "
                  "distinct_ct, status, detail, observed_at, via, minimality, sampled, "
                  "sample_pct) values (?, 'id', 10, 10, 7, 'has_duplicates', '', now(), 't', "
                  "'', false, 0)", [rel])
    entries = inventory.build(p, d, sch, store=s)
    rep = prove.run(p, d, sch, entries, s, target, say=lambda *_: None)
    got = {(r["model_name"], r["property"]): r for r in rep["rows"]}
    g = got[("grouped_join", "no_fanout:g")]
    assert g["status"] == "proven" and g["premises"] == [] and g["guarantee"] == "holding"
    sp = got[("spatial", "no_fanout:stg_parent")]
    assert sp["status"] == "not_attempted" and "not key equality" in sp["missing"]
