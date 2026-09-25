"""dbt-project-evaluator's rows as assay's cards: one per (subject, fact), folded, configured.

(sunny-data, 2026-09-25) 1,033 evaluator rows on one project; a person rules on the fact, not the
row. Every test here runs the real reader against a DuckDB holding the evaluator's own tables.
"""
from pathlib import Path

import duckdb

from dbt_assay import evaluator as ev
from dbt_assay.checks.structural import Finding
from dbt_assay.manifest import Project
from dbt_assay.probe import Result


def _model(name, layer="marts", package="p"):
    return {"resource_type": "model", "name": name, "package_name": package,
            "original_file_path": f"models/{layer}/{name}.sql", "schema": "main",
            "description": "", "columns": {}, "config": {"materialized": "table", "meta": {}}}


def _project(tmp_path: Path) -> Project:
    nodes = {f"model.p.{n}": _model(n, layer) for n, layer in (
        ("stg_a", "staging"), ("stg_b", "staging"), ("int_x", "intermediate"),
        ("mart_one", "marts"), ("mart_two", "marts"), ("lonely", "marts"))}
    nodes["model.elementary.alerts_x"] = _model("alerts_x", "marts", package="elementary")
    sources = {f"source.p.raw.{t}": {"resource_type": "source", "name": t, "source_name": "raw",
                                     "schema": "raw", "description": "", "columns": {}}
               for t in ("orders", "lines", "extra", "unused")}
    disabled = {f"model.dbt_project_evaluator.{n}": [{
        "resource_type": "model", "name": n, "package_name": "dbt_project_evaluator",
        "schema": "main_evaluator", "database": "db", "alias": n,
        "relation_name": f'"db"."main_evaluator"."{n}"'}]
        for n in ("fct_direct_join_to_source", "fct_marts_or_intermediate_dependent_on_source",
                  "fct_multiple_sources_joined", "fct_source_fanout",
                  "fct_missing_primary_key_tests", "fct_model_naming_conventions",
                  "fct_model_directories", "int_all_graph_resources")}
    parent_map = {"model.p.mart_one": ["model.p.int_x"], "model.p.int_x": ["model.p.stg_a"],
                  "model.p.stg_a": [], "model.p.stg_b": [], "model.p.mart_two": [],
                  "model.p.lonely": [], "model.elementary.alerts_x": []}
    child_map = {"model.p.stg_a": ["model.p.int_x"], "model.p.int_x": ["model.p.mart_one"]}
    manifest = {"metadata": {"project_name": "p", "adapter_type": "duckdb"}, "nodes": nodes,
                "sources": sources, "disabled": disabled, "parent_map": parent_map,
                "child_map": child_map}
    (tmp_path / "target").mkdir(exist_ok=True)
    return Project(manifest, tmp_path / "target")


def _warehouse(naming_rows=(("mart_one", "marts"),)):
    c = duckdb.connect()
    c.execute("attach ':memory:' as db")
    c.execute("create schema db.main_evaluator")
    c.execute("use db")
    s = "main_evaluator"
    c.execute(f"""create table {s}.fct_direct_join_to_source as select * from (values
        ('raw.orders', 'source', 'mart_one', 'model', 1),
        ('int_x', 'model', 'mart_one', 'model', 1)) t(parent, parent_resource_type, child,
                                                      child_resource_type, distance)""")
    c.execute(f"""create table {s}.fct_marts_or_intermediate_dependent_on_source as select * from
        (values ('raw.orders', 'source', 'mart_one', 'marts'),
                ('raw.extra', 'source', 'mart_two', 'marts'),
                ('raw.orders', 'source', 'alerts_x', 'marts'))
        t(parent, parent_resource_type, child, child_model_type)""")
    c.execute(f"""create table {s}.fct_multiple_sources_joined as select * from (values
        ('mart_one', 'raw.lines, raw.orders')) t(child, source_parents)""")
    c.execute(f"""create table {s}.fct_source_fanout as select * from (values
        ('raw.orders', 'mart_one, stg_a'), ('raw.lines', 'stg_a, stg_b'))
        t(parent, model_children)""")
    c.execute(f"""create table {s}.fct_missing_primary_key_tests as select * from (values
        ('mart_two', 'model', 'marts', false, 0, 0), ('lonely', 'model', 'marts', false, 1, 0))
        t(resource_name, resource_type, model_type, is_primary_key_tested,
          number_of_tests_on_model, number_of_constraints_on_model)""")
    rows = ", ".join(f"('{n}', 'x_', '{t}', 'fct_, dim_')" for n, t in naming_rows)
    c.execute(f"""create table {s}.fct_model_naming_conventions as select * from (values {rows})
        t(resource_name, prefix, model_type, appropriate_prefixes)""")
    c.execute(f"""create table {s}.int_all_graph_resources as select * from (values
        ('stg_a', 'model', 'staging', 'p', false), ('stg_b', 'model', 'staging', 'p', false),
        ('int_x', 'model', 'intermediate', 'p', false), ('mart_one', 'model', 'marts', 'p', false),
        ('mart_two', 'model', 'marts', 'p', false), ('lonely', 'model', 'marts', 'p', false))
        t(resource_name, resource_type, model_type, package_name, is_excluded)""")
    return c


def _runner(con, batches):
    def run(sql, n):
        try:
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description]
            return Result(rows=[dict(zip(cols, r)) for r in cur.fetchmany(n)])
        except Exception as e:                                   # noqa: BLE001
            return Result(failed=True, why=str(e)[:200])

    def many(pairs, max_rows=None, max_statements=None):
        batches.append([s for s, _n in pairs])
        return [run(s, n) for s, n in pairs]
    run.many = many
    return run


def _read(tmp_path, **kw):
    batches = []
    p = _project(tmp_path)
    rep = ev.read(p, _runner(_warehouse(**kw), batches))
    return p, rep, batches


def test_the_tables_come_from_the_manifest_even_when_the_package_is_switched_off(tmp_path):
    p = _project(tmp_path)
    rels = ev.relations(p)
    assert rels["fct_source_fanout"] == '"db"."main_evaluator"."fct_source_fanout"'
    assert ev.relations(p, "elsewhere")["fct_source_fanout"] == "db.elsewhere.fct_source_fanout"
    assert ev.installed(p)


def test_an_unbuilt_table_is_named_and_never_sent(tmp_path):
    _p, rep, batches = _read(tmp_path)
    assert rep.unread == {"fct_model_directories": "not built"}
    assert len(batches) == 2                          # the listing, then every table at once
    assert not any("fct_model_directories" in sql for sql in batches[1])
    assert rep.total == 11 and rep.graph


def test_four_rules_about_one_fact_are_one_card_per_model(tmp_path):
    p, rep, _b = _read(tmp_path)
    cards, tally = ev.cards(p, rep)
    raw = {c.subject_name: c for c in cards if c.check == "reads_raw_source_outside_staging"}
    assert set(raw) == {"mart_one", "mart_two"}
    one = raw["mart_one"].evidence["evaluator"]
    assert one["rules"] == ["fct_direct_join_to_source", "fct_marts_or_intermediate_dependent_on_source",
                            "fct_multiple_sources_joined", "fct_source_fanout"]
    assert one["sources"] == ["raw.lines", "raw.orders"]
    # the model-parent row of direct_join and the source_fanout row both land on it
    assert one["rows"] == 5
    # raw.lines is read only by staging: its fanout row is a card of its own, per source
    left = [c for c in cards if c.check == "source_read_directly_by_many_models"]
    assert [c.subject_name for c in left] == ["raw.lines"]
    # every row is on a card, or on the config card; none dropped
    assert tally.unmatched == 0
    assert tally.rows == 11


def test_rows_about_a_package_are_one_config_card_naming_the_setting(tmp_path):
    p, rep, _b = _read(tmp_path)
    cards, _t = ev.cards(p, rep)
    cfg = [c for c in cards if c.check == "evaluator_config_does_not_fit"]
    assert len(cfg) == 1 and "exclude_packages" in cfg[0].detail
    assert cfg[0].evidence["packages"] == ["elementary"]
    assert not any(c.subject == "model.elementary.alerts_x" for c in cards if c is not cfg[0])


def test_a_convention_failing_most_of_a_layer_is_about_the_config(tmp_path):
    marts = [("mart_one", "marts"), ("mart_two", "marts"), ("lonely", "marts")]
    p, rep, _b = _read(tmp_path, naming_rows=marts)
    ev.MIN_LAYER, was = 2, ev.MIN_LAYER
    try:
        cards, _t = ev.cards(p, rep)
    finally:
        ev.MIN_LAYER = was
    cfg = [c for c in cards if c.check == "evaluator_config_does_not_fit"
           and c.evidence.get("fact") == "convention_mismatch"]
    assert len(cfg) == 1 and "marts_prefixes" in cfg[0].detail
    assert not [c for c in cards if c.check == "model_name_breaks_convention"]
    # one of three is not most: that stays a card for the model
    p, rep, _b = _read(tmp_path)
    cards, _t = ev.cards(p, rep)
    assert [c.subject_name for c in cards if c.check == "model_name_breaks_convention"] == \
        ["mart_one"]


def test_a_card_assay_already_writes_carries_the_rule_and_keeps_its_id(tmp_path):
    p, rep, _b = _read(tmp_path)
    cards, tally = ev.cards(p, rep, grains={"lonely": ["id", "day"]})
    mine = Finding(check="grain_unresolved", subject="model.p.mart_two",
                   subject_name="mart_two", file="", summary="nobody knows what one row is",
                   detail="d", evidence={"k": 1})
    before = mine.id
    kept = ev.fold([mine], cards, tally)
    assert not any(c.check == "no_primary_key_test" and c.subject_name == "mart_two"
                   for c in kept)
    assert mine.id == before
    assert mine.evidence["evaluator"]["also_flagged_by"] == ["fct_missing_primary_key_tests"]
    assert "fct_missing_primary_key_tests" in mine.detail and tally.folded == 1
    lonely = next(c for c in kept if c.check == "no_primary_key_test")
    assert "(id, day)" in lonely.detail


def test_a_ruling_survives_the_evaluator_changing_its_rows(tmp_path):
    p, rep, _b = _read(tmp_path)
    cards, _t = ev.cards(p, rep)
    c = next(c for c in cards if c.subject_name == "mart_one"
             and c.check == "reads_raw_source_outside_staging")
    before = c.id
    c.evidence["evaluator"]["rules"].append("fct_something_new")
    c.evidence["evaluator"]["rows"] = 99
    assert c.id == before


def test_the_page_and_the_form_get_the_cards_from_the_last_verify_run(tmp_path):
    from dbt_assay import live
    from dbt_assay.store import Store
    p, rep, _b = _read(tmp_path)
    cards, tally = ev.cards(p, rep)
    mine = Finding(check="grain_unresolved", subject="model.p.mart_two",
                   subject_name="mart_two", file="", summary="nobody knows what one row is",
                   detail="d", evidence={"k": 1})
    kept = ev.fold([mine], cards, tally)
    st = Store(tmp_path / "s.duckdb")
    st.con.execute("insert into runs (run_id, started_at, project) values ('r1', now(), 'p')")
    st.write_findings("r1", [mine, *kept])
    offline = Finding(check="grain_unresolved", subject="model.p.mart_two",
                      subject_name="mart_two", file="", summary="nobody knows what one row is",
                      detail="d", evidence={"k": 1})
    got = live.with_stored_warehouse([offline], st, p)
    assert offline.evidence["evaluator"]["also_flagged_by"] == ["fct_missing_primary_key_tests"]
    assert {f.check for f in got} >= {"reads_raw_source_outside_staging",
                                     "evaluator_config_does_not_fit"}
    assert len(got) == 1 + len(kept)
    line = ev.surface_line(got)
    assert f"on {len(kept)} card(s) of its own and 1 on assay's own" in line
    st.close()


def test_monitoring_and_counted_findings_reach_the_page_and_the_form(tmp_path):
    """*** THE PAGE AND THE FORM NEVER SAW WHAT ONLY THE WAREHOUSE KNOWS. *** `check --verify`
    wrote monitoring findings and `hop_drops_most_rows`; the offline surfaces rebuilt findings
    without them. They come back from the latest full run, once, and only what offline lacks."""
    from dbt_assay import live
    from dbt_assay.store import Store
    p = _project(tmp_path)
    mon = Finding(check="volume_is_not_being_watched", subject="", subject_name="", file="",
                  summary="3 models with a mart downstream have no row-count monitor",
                  detail="d", base=2, evidence={"unwatched": 3})
    hop = Finding(check="hop_drops_most_rows", subject="model.p.mart_one",
                  subject_name="mart_one", file="", summary="keeps 10% of int_x",
                  detail="d", evidence={"parent": "int_x"})
    other = Finding(check="grain_unresolved", subject="model.p.lonely", subject_name="lonely",
                    file="", summary="s", detail="d")
    st = Store(tmp_path / "s.duckdb")
    st.con.execute("insert into runs (run_id, started_at, project) values ('r1', now(), 'p')")
    st.write_findings("r1", [mon, hop, other])
    got = live.with_stored_warehouse([], st, p)
    assert sorted(f.check for f in got) == ["hop_drops_most_rows", "volume_is_not_being_watched"]
    assert {f.id for f in got} == {mon.id, hop.id}
    # already produced offline: not twice
    again = live.with_stored_warehouse([hop], st, p)
    assert [f.check for f in again].count("hop_drops_most_rows") == 1
    st.close()
