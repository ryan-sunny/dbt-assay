"""Lean checks a certificate's proof; this checks its STATEMENT, by running the model.

*** A PROOF OF THE WRONG THEOREM READS PROVEN. *** `select objectid as county_id` made a
certificate assume "`county_id` unique in the source", a column the source does not have. Lean
proved it; nine sunny-data models carried one. Running each proven claim on inputs that meet its
premises found all nine, and would find the next.
"""
from test_prove import _load, build, needs_lean

from dbt_assay import claimcheck, conformance, inventory, prove, toolchain
from dbt_assay.store import Store

MODELS = {
    "stg_parent": "select id, name from raw.parent",
    "kids": "select id as kid, id as pid from raw.kids",
    "renamed": "select p.id as rid, p.name from main.stg_parent p",
    "looked_up": "select k.kid, p.name from main.kids k left join main.stg_parent p on p.id = k.pid",
}


def _cert(prop, claim, premises):
    return {"property": prop, "claim": claim, "premises": premises}


def test_a_claim_that_does_not_follow_from_its_premises_is_contradicted(tmp_path):
    target = build(tmp_path, MODELS)
    p, _d, sch = _load(target)
    sql = p.models["model.p.renamed"].compiled
    unique_id = {"id": "x", "relation": "model.p.stg_parent", "columns": ["id"],
                 "property": "unique"}
    got = claimcheck.check_model(p, sch, "model.p.renamed", sql, "duckdb", [
        _cert("right", {"kind": "unique", "cols": ["rid"]}, [unique_id]),
        # the wrong theorem: unique on `name` does not follow from `id` being unique
        _cert("wrong", {"kind": "unique", "cols": ["name"]}, [unique_id])])
    assert got["right"][0] == claimcheck.HOLDS, got
    assert got["wrong"][0] == claimcheck.CONTRADICTED, got
    sql = p.models["model.p.looked_up"].compiled
    join = {"kind": "join", "index": 1, "left": True}
    got = claimcheck.check_model(p, sch, "model.p.looked_up", sql, "duckdb", [
        _cert("held", join, [unique_id]), _cert("unmet", join, [])])
    assert got["held"][0] == claimcheck.HOLDS, got
    # with nothing making `id` unique the generated parent repeats it, and the left join
    # multiplies: a no-fanout certificate with no premise is wrong, and a run shows it
    assert got["unmet"][0] == claimcheck.CONTRADICTED, got


def test_a_grain_read_through_a_rename_rests_on_the_source_column(tmp_path):
    target = build(tmp_path, MODELS)
    p, d, sch = _load(target)
    entries = inventory.build(p, d, sch, store=None)
    from dbt_assay import ledger
    led = ledger.build(p, sch, entries, None, tests={}, observed={})
    g = next((o for o in prove.obligations(p, d, sch, entries, led)
              if o.name == "renamed" and o.prop == "grain"), None)
    assert g is not None, "the fixture derives rid as renamed's grain"
    assert g.claim == {"kind": "unique", "cols": ["rid"]}
    assert [(pp.prop, list(pp.columns)) for pp in g.premises] == [("unique", ["id"]),
                                                             ("not_null", ["id"])], g.premises
    assert 'Unique ["id"] (filterT p L)' in g.lean and "rid" not in g.lean, g.lean


def test_the_parser_follows_a_column_through_renames_and_casts_only():
    from dbt_assay.parse import trace_cte, trace_output
    assert trace_output("select objectid as county_id from raw.t", "county_id") == \
        [("t", "objectid")]
    assert trace_output("with c as (select * from raw.t) select c.id from c", "id") == \
        [("c", "id"), ("t", "id")]
    assert trace_output("select cast(n as varchar) as id from raw.t", "id") == [("t", "n")]
    assert trace_output("select upper(n) as id from raw.t", "id") == []
    assert trace_output("select cast(n as int) as id from raw.t", "id") == []
    assert trace_output("select b.id from raw.t a join raw.u b on b.k = a.k", "id") == []
    assert trace_output("select id from raw.t a join raw.u b on b.k = a.k", "id") == []
    assert trace_cte("with f as (select objectid as id from raw.t where z) select 1", "f",
                     "id") == [("t", "objectid")]


@needs_lean
def test_the_definitions_the_rules_are_proven_about_run_as_duckdb_does():
    toolchain.build_library(say=lambda *_: None)
    got = conformance.run_ops(40, seed=3)
    assert set(got) == {f"rule_op:{o}" for o in conformance.RULE_OPS}
    assert all(st == conformance.CONFORMS for st, _d in got.values()), got
    # and the comparison can fail: Lean's inner join against a LEFT join in DuckDB
    t = {"l": (["k", "k2", "v"], [[1, 1, 1], [9, 9, 9]]), "r": (["rk", "rk2", "w"], [[1, 1, 5]])}
    cols = ["k", "k2", "v", "rk", "rk2", "w"]
    lean = conformance.ops_rows(t, ["innerJoin", "l", "r", "k", "rk"], cols)
    eng = conformance.duckdb_rows(t, "select l.k, l.k2, l.v, r.rk, r.rk2, r.w from l "
                                     "left join r on l.k = r.rk")
    assert conformance.compare(lean, eng)[0] == conformance.DIFFERS


@needs_lean
def test_prove_runs_every_proven_claim_and_reads_contradicted_over_proven(tmp_path, monkeypatch):
    target = build(tmp_path, MODELS)
    p, d, sch = _load(target)
    s = Store(str(tmp_path / "s.duckdb"))
    entries = inventory.build(p, d, sch, store=s)
    rep = prove.run(p, d, sch, entries, s, target, say=lambda *_: None)
    ran = [r for r in rep["rows"] if r["status"] == "proven"]
    assert ran and all(r["run_check"]["status"] in ("holds", "unchecked") for r in ran), ran
    # a run that contradicts a proven claim wins over the proof
    r0 = ran[0]
    s.con.execute("update claim_checks set status = 'contradicted', detail = 'x' "
                  "where model = ? and property = ?", [r0["model"], r0["property"]])
    rows = prove.with_guarantees(prove.stored(s), None, p, s)
    r1 = next(r for r in rows if (r["model"], r["property"]) == (r0["model"], r0["property"]))
    assert r1["guarantee"] == r1["status"] == "contradicted" and r1["lean_checked"]


# --- sunny-data feedback M1-M3 ------------------------------------------------------------------

def test_a_model_nested_past_pythons_limit_is_still_run(tmp_path):
    """M3: the digest raised the recursion limit and the run check's own parse did not."""
    deep = "(" * 400 + "p.id" + ")" * 400
    target = build(tmp_path, {**MODELS, "deep": f"select {deep} as rid from main.stg_parent p"})
    p, _d, sch = _load(target)
    got = claimcheck.check_model(p, sch, "model.p.deep", p.models["model.p.deep"].compiled,
                                 "duckdb", [_cert("g", {"kind": "unique", "cols": ["rid"]},
                                                  [{"id": "x", "relation": "model.p.stg_parent",
                                                    "columns": ["id"], "property": "unique"}])])
    assert got["g"][0] == claimcheck.HOLDS, got


def test_a_certificate_whose_premise_is_broken_is_not_run_or_counted():
    """M1: the run check confirmed "no fan-out as long as account_no is unique" on inputs built
    to make it unique, while the real table repeats it; that was counted as held."""
    base = {"model": "m", "model_name": "m", "model_checksum": "c", "property": "no_fanout:p",
            "premises": [], "rule": "inner_join_no_fanout", "status": "proven", "detail": ""}
    for g in ("refuted", "lost"):
        rows = [dict(base)]
        # a broken premise: with_guarantees reads it from the ledger
        from types import SimpleNamespace
        broke = SimpleNamespace(status="broken", statement=lambda: "`k` unique in `p`",
                                evidence=[SimpleNamespace(kind="declared" if g == "lost"
                                                          else "observed")])
        rows[0]["premises"] = [{"id": "b", "relation": "p", "columns": ["k"],
                                "property": "unique"}]
        led = SimpleNamespace(premises={"b": broke})
        import dbt_assay.ledger as L
        orig = (L.why, L.label)
        L.why, L.label = (lambda _p: "counted"), (lambda _p: "broken")
        try:
            got = prove.with_guarantees(rows, led, None)[0]
        finally:
            L.why, L.label = orig
        assert got["guarantee"] == g and got["run_check"]["status"] == "premise_broken", got
        assert got["status"] != "proven"


@needs_lean
def test_random_n_runs_n_cases_for_each_rule_operation(tmp_path):
    """M2: `--random 300` ran 150 per operation."""
    toolchain.build_library(say=lambda *_: None)
    s = Store(str(tmp_path / "s.duckdb"))
    rep = conformance.run(s, n_random=12, say=lambda *_: None)
    assert "all 12 random cases agree" in rep["constructs"]["rule_op:pick"]["detail"], rep
