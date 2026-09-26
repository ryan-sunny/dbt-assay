"""Findings become fixes (leverage spec): each finding is attributed to the change that resolves
it, the change carries its files, a person approves it, and only then may an agent apply it."""
import json
from pathlib import Path
from types import SimpleNamespace

from dbt_assay import fixes
from dbt_assay.parse import digest
from dbt_assay.store import Store


def _m(name, path, parents, layer="marts", compiled="select 1"):
    return SimpleNamespace(name=name, path=path, parents=parents, layer=layer, compiled=compiled,
                           is_installed_package=False, columns={}, description="")


def _finding(check, subject, name, fid):
    return SimpleNamespace(check=check, subject=subject, subject_name=name, id=fid, summary="s",
                           evidence={}, exposures=[], marts=0, descendants=0, base=2)


def test_a_raw_source_read_by_two_models_is_one_staging_fix(tmp_path):
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "a.sql").write_text("select id from {{ source('raw', 'permits') }}\n")
    (tmp_path / "models" / "b.sql").write_text(
        "select id from {{source(\"raw\",\"permits\")}} join {{ ref('x') }} using (id)\n")
    src = SimpleNamespace(name="permits", source_name="raw", schema="raw", columns={})
    project = SimpleNamespace(
        models={"model.p.a": _m("a", "models/a.sql", ["source.p.raw.permits"]),
                "model.p.b": _m("b", "models/b.sql", ["source.p.raw.permits", "model.p.x"])},
        sources={"source.p.raw.permits": src}, raw={"nodes": {}})
    by = {("reads_raw_source_outside_staging", "model.p.a"): [_finding(
        "reads_raw_source_outside_staging", "model.p.a", "a", "f1")]}
    got = fixes._stage(project, by, tmp_path, {})
    assert len(got) == 1
    fx = got[0]
    assert fx.files["models/a.sql"] == "select id from {{ ref('stg_raw__permits') }}\n"
    assert "{{ ref('stg_raw__permits') }} join" in fx.files["models/b.sql"]
    assert fx.new_files == ["models/staging/raw/stg_raw__permits.sql"]
    assert "source('raw', 'permits')" in fx.files["models/staging/raw/stg_raw__permits.sql"]
    assert fx.findings == ["f1"] and fx.moves_logic


def test_an_existing_pass_through_staging_model_is_reused_and_a_cleaning_one_is_not(tmp_path):
    (tmp_path / "a.sql").write_text("select id from {{ source('raw', 'permits') }}\n")
    src = SimpleNamespace(name="permits", source_name="raw", schema="raw", columns={})
    clean = "with s as (select * from raw.permits) select id, try_cast(v as double) as v from s"
    plain = "select id, v from raw.permits"
    for sql, reused in ((plain, True), (clean, False)):
        project = SimpleNamespace(
            models={"model.p.a": _m("a", "a.sql", ["source.p.raw.permits"]),
                    "model.p.stg_permits": _m("stg_permits", "stg.sql", ["source.p.raw.permits"],
                                              layer="staging", compiled=sql)},
            sources={"source.p.raw.permits": src}, raw={"nodes": {}})
        fx = fixes._stage(project, {}, tmp_path,
                          {"model.p.stg_permits": digest(sql, "stg_permits")})[0]
        assert ("ref('stg_permits')" in fx.files["a.sql"]) is reused
        assert bool(fx.new_files) is not reused


def test_decisions_are_recorded_and_the_latest_wins(tmp_path):
    s = Store(str(tmp_path / "s.duckdb"))
    fixes.record(s, "abc", "deferred", note="after the release", by="ryan")
    fixes.record(s, "abc", "approved", by="ryan")
    assert fixes.statuses(s)["abc"]["status"] == "approved"
    s.close()


def test_an_agent_cannot_approve_a_fix_through_the_mcp_bridge():
    from dbt_assay import cli_tools
    got = cli_tools.run("fix", "abc --approve")
    assert "person's decision" in got["error"]


def test_apply_is_refused_until_a_person_approved(tmp_path, monkeypatch):
    from dbt_assay.mcp_server import Backend
    fx = fixes.Fix("document", "model.p.a", "Document a", findings=["f1"],
                   files={"models/_a.yml": "version: 2\n"}, new_files=["models/_a.yml"])
    s = Store(str(tmp_path / "s.duckdb"))
    s.close()
    be = Backend(str(tmp_path), store_path=str(tmp_path / "s.duckdb"))
    monkeypatch.setattr(be, "_fixes", lambda: ([fx], [], Store(str(tmp_path / "s.duckdb"))))
    monkeypatch.setattr(be, "state", lambda: SimpleNamespace(
        project=SimpleNamespace(project_root=tmp_path)))
    assert "refused" in be.apply_plan_item(fx.id)
    st = Store(str(tmp_path / "s.duckdb"))
    fixes.record(st, fx.id, "approved", by="ryan")
    st.close()
    got = be.apply_plan_item(fx.id)
    assert got["wrote"] == ["models/_a.yml"] and (tmp_path / "models/_a.yml").exists()
    v = be.verify_plan_item(fx.id)
    assert v["verified"] and v["files_not_yet_as_the_fix_writes"] == []
    assert json.dumps(v)


def test_on_the_box_apply_returns_the_files_and_writes_nothing(tmp_path, monkeypatch):
    """D14: the box's MCP server (--verdicts-only) runs a project that comes from git, so the
    agent gets the files and the diff to write in its own branch, and the items the person left
    out of the approval come back as excluded."""
    from dbt_assay.mcp_server import Backend
    fx = fixes.Fix("document", "model.p.a", "Document a", findings=["f1"],
                   files={"models/_a.yml": "version: 2\n"}, new_files=["models/_a.yml"],
                   items=[{"id": "i1", "model": "a"}, {"id": "i2", "model": "a"}])
    s = Store(str(tmp_path / "s.duckdb"))
    fixes.record(s, fx.id, "approved", by="ryan", detail=json.dumps({"excluded": ["i2"]}))
    s.close()
    be = Backend(str(tmp_path), store_path=str(tmp_path / "s.duckdb"), verdicts_only=True)
    monkeypatch.setattr(be, "_fixes", lambda: ([fx], [], Store(str(tmp_path / "s.duckdb"))))
    monkeypatch.setattr(be, "state", lambda: SimpleNamespace(
        project=SimpleNamespace(project_root=tmp_path)))
    got = be.apply_plan_item(fx.id)
    assert got["wrote"] == [] and not (tmp_path / "models/_a.yml").exists()
    assert got["files"] == {"models/_a.yml": "version: 2\n"} and "+version: 2" in got["diff"]
    assert [i["id"] for i in got["items"]] == ["i1"]
    assert [i["id"] for i in got["excluded"]] == ["i2"]
    assert fixes.statuses(Store(str(tmp_path / "s.duckdb")))[fx.id]["status"] == "approved", \
        "a fix nobody wrote in a branch was recorded as applied"
    assert [i["id"] for i in be.plan_item(fx.id)["excluded"]] == ["i2"]
    assert [r["id"] for r in be.plan_items(status="approved")["fixes"]] == [fx.id]
    assert be.plan_items(status="rejected")["fixes"] == []


def test_judged_roles_become_tests_only_when_counted_to_pass(tmp_path):
    """Graduation (assay-loops.md): a judged foreign key becomes a relationships test to the
    model whose key it is, a status flag an accepted_values test, and only after a count says
    each would pass. The test lands in the model's own yml."""
    import yaml

    from dbt_assay.inventory import ColumnEntry, Fact
    from dbt_assay.probe import Result, Statement

    def entry(uid, name, grain, cols):
        return SimpleNamespace(uid=uid, name=name, grain=Fact(grain, "derived"),
                               columns=[ColumnEntry(name=c, provenance=Fact("carried", "derived"),
                                                    role=Fact(r, "judged", 0.9) if r else None)
                                        for c, r in cols])
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "schema.yml").write_text(
        "version: 2\n\nmodels:\n- name: orders\n  columns:\n  - name: order_id\n")
    project = SimpleNamespace(
        dbt_version="1.11.0", tests=[],
        models={"model.p.orders": SimpleNamespace(name="orders", path="models/orders.sql",
                                                  parents=["model.p.customers"],
                                                  is_installed_package=False),
                "model.p.customers": SimpleNamespace(name="customers", path="models/c.sql",
                                                     parents=[], is_installed_package=False)},
        raw={"nodes": {"model.p.orders": {"patch_path": "p://models/schema.yml"}}})
    entries = [entry("model.p.orders", "orders", ["order_id"],
                     [("order_id", "identifier"), ("customer_id", "foreign_key"),
                      ("status", "status_flag"), ("channel", "status_flag")]),
               entry("model.p.customers", "customers", ["customer_id"],
                     [("customer_id", "identifier")])]
    cands = fixes.role_test_candidates(project, entries)
    assert sorted((c["kind"], c["column"]) for c in cands) == [
        ("accepted_values", "channel"), ("accepted_values", "status"),
        ("relationships", "customer_id")]

    def run_many(stmts, *a, **k):
        out = []
        for st in stmts:
            if "orphans" in st.sql:
                out.append(Result(rows=[{"orphans": 0}]))
            elif "channel" in st.sql:                       # too many values: not a category
                out.append(Result(rows=[{"v": str(i), "n": 1} for i in range(40)]))
            else:
                out.append(Result(rows=[{"v": "open", "n": 3}, {"v": "shipped", "n": 9}]))
        return out
    probe = SimpleNamespace(Statement=Statement, run_many=run_many)
    proven = fixes.prove_role_tests(cands, project, None, probe, ".", None, "dbt")
    assert sorted(p["column"] for p in proven) == ["customer_id", "status"]
    fx = fixes.graduate(project, proven, tmp_path)[0]
    d = yaml.safe_load(fx.files["models/schema.yml"])
    cols = {c["name"]: c for c in d["models"][0]["columns"]}
    assert cols["customer_id"]["data_tests"] == [
        {"relationships": {"arguments": {"to": "ref('customers')", "field": "customer_id"}}}]
    assert cols["status"]["data_tests"] == [
        {"accepted_values": {"arguments": {"values": ["open", "shipped"]}}}]


def test_the_date_is_pinned_with_the_projects_own_macro_and_prose_is_left_alone(tmp_path):
    (tmp_path / "m.sql").write_text(
        "select current_date - d as age, now() as at -- until now (see x)\n"
        "from t {{ var('x', 'current_date') }}\n")
    project = SimpleNamespace(
        raw={"macros": {"macro.p.run_date": {
            "name": "run_date", "package_name": "p",
            "macro_sql": "{% macro run_date() %}{% if var('run_date', none) %}x{% else %}"
                         "current_date{% endif %}{% endmacro %}"}}},
        project_name="p",
        models={"model.p.m": SimpleNamespace(name="m", path="m.sql")})
    f = _finding("output_depends_on_the_clock", "model.p.m", "m", "c1")
    fx = fixes._pin_the_date(project, [f], tmp_path)
    assert fx.files["m.sql"] == (
        "select {{ run_date() }} - d as age, cast({{ run_date() }} as timestamp) as at "
        "-- until now (see x)\nfrom t {{ var('x', 'current_date') }}\n")
    assert "macros/as_of.sql" not in fx.files and fx.findings == ["c1"]


def test_what_could_break_is_one_fix_per_model_and_the_rest_one_per_check():
    project = SimpleNamespace(models={"model.p.a": SimpleNamespace(name="a", path="a.sql")})
    fs = []
    for i, ans in enumerate(("joins_would_fan_out_or_drop", "an_aggregate_would_be_wrong")):
        f = _finding("what_would_break_silently", "model.p.a", "a", f"w{i}")
        f.evidence = {"answer": ans, "context": f"a.col{i}"}
        fs.append(f)
    got = fixes._test_what_could_break(project, fs)
    # each judged test is its own decision, named with its column and why
    assert len(got) == 1 and got[0].findings == ["w0", "w1"] and got[0].decisions == 2
    assert [(i["column"], i["test"]) for i in got[0].items] == [
        ("col0", "unique, or relationships to the model it joins"),
        ("col1", "a range or a reconciliation test")]
    assert "col0: unique, or relationships" in got[0].how


def test_stage_fixes_that_edit_the_same_model_are_one_fix(tmp_path):
    """Two fixes each carrying their own full text of one model cannot both be applied: sources
    read raw by the same model are staged in one fix, and a model reading both is resolved."""
    (tmp_path / "a.sql").write_text("select * from {{ source('raw', 'p') }} join "
                                    "{{ source('raw', 'q') }} using (id)\n")
    (tmp_path / "b.sql").write_text("select * from {{ source('raw', 'q') }}\n")
    (tmp_path / "c.sql").write_text("select * from {{ source('raw', 'r') }}\n")
    srcs = {f"source.p.raw.{n}": SimpleNamespace(name=n, source_name="raw", schema="raw",
                                                 columns={}) for n in "pqr"}
    project = SimpleNamespace(
        models={"model.p.a": _m("a", "a.sql", ["source.p.raw.p", "source.p.raw.q"]),
                "model.p.b": _m("b", "b.sql", ["source.p.raw.q"]),
                "model.p.c": _m("c", "c.sql", ["source.p.raw.r"])},
        sources=srcs, raw={"nodes": {}})
    by = {("reads_raw_source_outside_staging", "model.p.a"): [_finding(
        "reads_raw_source_outside_staging", "model.p.a", "a", "fa")]}
    got = sorted(fixes._stage(project, by, tmp_path, {}), key=lambda f: len(f.models))
    assert len(got) == 2
    one, merged = got
    assert one.models == ["c"]
    assert merged.models == ["a", "b"] and merged.decisions == 1
    assert merged.files["a.sql"] == ("select * from {{ ref('stg_raw__p') }} join "
                                     "{{ ref('stg_raw__q') }} using (id)\n")
    assert merged.files["b.sql"] == "select * from {{ ref('stg_raw__q') }}\n"
    assert sorted(merged.new_files) == ["models/staging/raw/stg_raw__p.sql",
                                        "models/staging/raw/stg_raw__q.sql"]
    assert merged.findings == ["fa"]            # a reads neither raw once both are repointed


def test_every_model_a_schema_file_documents_is_one_fix_in_one_text(tmp_path):
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "_s.yml").write_text(
        "version: 2\n\nmodels:\n  - name: a\n  - name: b\n")
    project = SimpleNamespace(
        models={"model.p.a": _m("a", "models/a.sql", []), "model.p.b": _m("b", "models/b.sql", [])},
        raw={"nodes": {u: {"patch_path": "p://models/_s.yml"} for u in ("model.p.a", "model.p.b")}})
    by = {}
    for u, n in (("model.p.a", "a"), ("model.p.b", "b")):
        f = _finding("column_has_no_description", u, n, "d" + n)
        f.evidence = {"missing": ["id"]}
        by[("column_has_no_description", u)] = [f]
    import dbt_assay.fixes as fm
    orig = fm._draft
    fm._draft = lambda *a: "The row id."
    try:
        got = fixes._document(project, [], {}, None, by, tmp_path)
    finally:
        fm._draft = orig
    assert len(got) == 1
    fx = got[0]
    assert fx.findings == ["da", "db"] and fx.decisions == 1 and list(fx.files) == ["models/_s.yml"]
    assert fx.files["models/_s.yml"].count("description: The row id.") == 2


def test_the_plan_ranks_by_findings_resolved_per_decision():
    lo = fixes.Fix("make_it_pass", "t", "one failing test", findings=["x"])
    lo.tier = "customer-facing"
    hi = fixes.Fix("document", "f", "a file", findings=[f"d{i}" for i in range(50)])
    hi.tier = "the rest"
    wide = fixes.Fix("review", "c", "a check", findings=[f"r{i}" for i in range(60)], decisions=120)
    assert [f.key for f in fixes.rank([lo, wide, hi])] == ["f", "t", "c"]


def test_the_open_findings_split_four_ways_and_a_note_is_never_a_proposal():
    """(Ryan: "im not deciding on 1600 cards"; "2,202 notes are not 2,202 problems") Every open
    finding is in exactly one place: a change clears it, a proposal says what to do about what a
    count settled, a person decides it, or it is a note."""
    def f(check, fid, base=3, subject="model.p.a"):
        x = _finding(check, subject, "a", fid)
        x.base = base
        return x
    fs = [f("test_declared_but_never_run", "t1", base=1),         # a change clears it
          f("values_lost_at_hop", "v1"),                          # a count settled it, queued: a proposal
          f("values_lost_at_hop", "v2", subject="model.p.b"),     # the same, annotated
          f("code_contradicts_a_claim", "c1"),                    # a judgment call, queued
          f("code_contradicts_a_claim", "c2", subject="model.p.b")]  # a judgment call, a note
    acts = {"t1": ("annotate", ""), "v1": ("queue", ""), "v2": ("annotate", ""),
            "c1": ("queue", ""), "c2": ("annotate", "")}
    project = SimpleNamespace(models={}, sources={}, exposures={}, raw={"nodes": {}},
                              project_root=".", tests=[])
    fx = fixes.build(project, fs, acts=acts, led=SimpleNamespace(uses=[], premises={}),
                     root=Path("."))
    sp = fixes.split(fs, fx, acts)
    assert sp == {"fix": {"t1"}, "settled": {"v1"}, "decide": {"c1"}, "notes": {"v2", "c2"}}
    review = [x for x in fx if x.kind == "review"]
    assert [x.findings for x in review] == [["v1"]]              # the queued count, not the note
    assert sum(len(v) for v in sp.values()) == len(fs)


def test_a_batch_kind_is_one_card_and_a_change_that_clears_nothing_is_dropped(tmp_path):
    (tmp_path / "m").mkdir()
    for n in ("a", "b"):
        (tmp_path / "m" / f"_{n}.yml").write_text(f"version: 2\n\nmodels:\n  - name: {n}\n")
    project = SimpleNamespace(
        models={"model.p.a": _m("a", "m/a.sql", []), "model.p.b": _m("b", "m/b.sql", [])},
        sources={}, exposures={}, project_root=str(tmp_path), tests=[],
        raw={"nodes": {"model.p.a": {"patch_path": "p://m/_a.yml"},
                       "model.p.b": {"patch_path": "p://m/_b.yml"}}})
    fs = []
    for u, n in (("model.p.a", "a"), ("model.p.b", "b")):
        x = _finding("column_has_no_description", u, n, "d" + n)
        x.evidence = {"missing": ["id"], "missing_total": 1}
        fs.append(x)
    import dbt_assay.fixes as fm
    orig = fm._draft
    fm._draft = lambda *a: "The row id."
    try:
        fx = fixes.build(project, fs, led=SimpleNamespace(uses=[], premises={}), root=tmp_path)
    finally:
        fm._draft = orig
    doc = [x for x in fx if x.kind == "document"]
    assert len(doc) == 1 and doc[0].decisions == 1
    assert sorted(doc[0].files) == ["m/_a.yml", "m/_b.yml"] and sorted(doc[0].findings) == ["da", "db"]
    assert doc[0].title == "Document 2 column(s) in 2 model(s)" and len(doc[0].pieces) == 2
    assert all(x.findings for x in fx)                           # nothing that clears nothing


def test_triage_leads_with_what_is_broken_now_and_counts_notes_once():
    from dbt_assay import priority
    fs = [{"id": "a", "check": "test_is_failing", "tier": "customer-facing"},
          {"id": "b", "check": "code_contradicts_a_claim", "tier": "the rest"},
          {"id": "c", "check": "column_has_no_description", "tier": "the rest"}]
    fs.append({"id": "d", "check": "values_lost_at_hop", "tier": "customer-facing"})
    fs.append({"id": "e", "check": "arbitrary_pick", "tier": "the rest",
               "evidence": {"why_it_is_back": {"premise": "k"}}})
    t = priority.triage(fs, {"a", "b", "d", "e"})
    # a count still to be read against the data is worth a look; a broken premise is broken
    assert t == {"queued": 4, "broken": 2, "broken_paid": 1, "look": 2, "look_paid": 1, "notes": 1}


def test_run_the_tests_names_what_makes_them_run_and_lists_each_test():
    """(Ryan: "a condensed table of 223") and "the selector includes them" named nothing. A tag
    the job leaves out gets its selector; a model none of whose tests ever ran is named; a test on
    a model whose other tests run is newer or excluded. And a mechanical change is not "judged"."""
    nodes = {
        "test.p.a": {"resource_type": "test", "name": "vol_a", "tags": ["elementary-tests"],
                     "attached_node": "model.p.a", "depends_on": {"nodes": ["model.p.a"]}},
        "test.p.b1": {"resource_type": "test", "name": "nn_b", "tags": [],
                      "attached_node": "model.p.b", "depends_on": {"nodes": ["model.p.b"]}},
        "test.p.c1": {"resource_type": "test", "name": "nn_c", "tags": [],
                      "attached_node": "model.p.c", "depends_on": {"nodes": ["model.p.c"]}},
        "test.p.c2": {"resource_type": "test", "name": "uq_c", "tags": [],     # this one ran
                      "attached_node": "model.p.c", "depends_on": {"nodes": ["model.p.c"]}},
        "test.p.r": {"resource_type": "test", "name": "rel_b_c", "tags": [],  # on b, refers to c
                     "attached_node": "model.p.b",
                     "depends_on": {"nodes": ["model.p.c", "model.p.b"]}},
    }
    project = SimpleNamespace(models={u: _m(u[-1], f"{u[-1]}.sql", []) for u in
                                      ("model.p.a", "model.p.b", "model.p.c")},
                              sources={}, exposures={}, raw={"nodes": nodes}, project_root=".",
                              tests=[])
    fs = []
    for i, (test, model) in enumerate((("vol_a", "a"), ("nn_b", "b"), ("nn_c", "c"),
                                       ("rel_b_c", "b"))):
        f = _finding("test_never_ran_is_a_gap_or_a_leftover", f"model.p.{model}", model, f"t{i}")
        f.evidence = {"context": test, "answer": "a_coverage_gap", "probability": 0.99}
        fs.append(f)
    fx = next(x for x in fixes.build(project, fs, led=SimpleNamespace(uses=[], premises={}),
                                     root=Path("."))
              if x.kind == "run_the_tests")
    why = {r[1]: r[2] for r in fx.table["rows"]}
    assert why == {"vol_a": "the job leaves out tag:elementary-tests",
                   "nn_b": "no test on this model has ever run",
                   "rel_b_c": "no test on this model has ever run",
                   "nn_c": "newer than the last run, or excluded"}, why
    assert fx.table["cols"] == ["model", "test", "why it never ran"]
    assert "dbt test --select tag:elementary-tests" in fx.how and "(b)" in fx.how
    assert not any(w.startswith("judged at") for w in fx.why), fx.why
