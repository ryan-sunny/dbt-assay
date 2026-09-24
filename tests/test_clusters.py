"""Areas rather than findings: code builds the clusters for free; a judgment reads only the rest.

*** NO CLUSTER IS EVER RULED ON AS A UNIT. *** (25.23, 25.24)
"""
from __future__ import annotations

import json
from pathlib import Path

from fakejev import install
from typer.testing import CliRunner

from dbt_assay import clusters
from dbt_assay.cli import _load, app

runner = CliRunner()

SQL = {
    "stg_a": "select address from raw.a where trim(address) <> ''",
    "stg_b": "select street from raw.b where trim(street) <> ''",
    "int_c": "select owner from raw.c where trim(owner) <> '' and not owner is null",
    "stg_d": "select * from raw.d where sale_date >= current_date - interval '120' day",
    "stg_e": "select * from raw.e where sale_date >= current_date - interval '120' day",
    "stg_f": "select * from raw.f where sale_date >= current_date - interval '120' day",
    "int_g": ("-- the most recent permit per building (last 540d)\n"
              "select * from raw.g where issued_date >= current_date - interval '540' day"),
}


def _project(tmp_path: Path, macro_for=()) -> Path:
    nodes, parents, children = {}, {}, {}
    for name, sql in SQL.items():
        path = f"models/{name}.sql"
        uid = f"model.p.{name}"
        f = tmp_path / "target" / "compiled" / "p" / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(sql)
        (tmp_path / "models").mkdir(exist_ok=True)
        (tmp_path / path).write_text(sql)
        nodes[uid] = {"unique_id": uid, "name": name, "resource_type": "model",
                      "package_name": "p", "original_file_path": path, "path": path,
                      "description": "", "columns": {}, "config": {"materialized": "view"},
                      "depends_on": {"nodes": [], "macros": (["macro.p.recent"]
                                                             if name in macro_for else [])},
                      "raw_code": sql, "checksum": {"name": "sha256", "checksum": name}}
        parents[uid], children[uid] = [], []
    (tmp_path / "macros").mkdir(exist_ok=True)
    (tmp_path / "macros" / "recent.sql").write_text(
        "{% macro recent() %}where sale_date >= current_date - interval '120' day{% endmacro %}\n")
    manifest = {"metadata": {"project_name": "p", "dbt_version": "1.11.0",
                             "adapter_type": "duckdb"},
                "nodes": nodes, "sources": {}, "parent_map": parents, "child_map": children,
                "macros": {"macro.p.recent": {"name": "recent", "package_name": "p",
                                              "original_file_path": "macros/recent.sql"}}}
    (tmp_path / "target" / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path / "target"


def test_a_shape_masks_columns_and_drops_comments():
    a = clusters.shapes("TRIM(c.county) <> ''")[0]
    b = clusters.shapes("trim(address) /* guard */ <> ''")[0]
    assert a == b == "TRIM(<col>) <> ''"


def test_a_null_check_is_a_habit_not_a_rule(tmp_path):
    project, digests, *_ = _load(_project(tmp_path), None)
    shapes = {c.shape for c in clusters.predicate_clusters(project, digests)}
    assert "TRIM(<col>) <> ''" in shapes
    assert not any("IS NULL" in s for s in shapes)


def test_a_rule_a_macro_already_writes_once_is_never_asked(tmp_path):
    from dbt_assay import subjects
    project, digests, _f, schema, _s = _load(
        _project(tmp_path, macro_for=("stg_d", "stg_e", "stg_f")), None)
    pcs = {c.shape: c for c in clusters.predicate_clusters(project, digests)}
    written = next(c for s, c in pcs.items() if "120" in s)
    assert written.macro == "recent" and written.macro_at.startswith("macros/recent.sql:")
    asked = subjects.build("predicate_cluster", subjects.SubjectSource(project, digests, schema))
    assert all("120" not in s.state["filter_shape"] for s in asked)


def test_the_odd_one_out_carries_the_line_that_explains_it(tmp_path):
    project, digests, *_ = _load(_project(tmp_path), None)
    odd = clusters.odd_ones_out(project, digests)
    assert [o.member.model for o in odd] == ["int_g"]
    o = odd[0]
    assert "540" in o.difference and "120" in o.difference
    said = clusters.what_it_says(project, o.member.uid, o.added)
    assert any("last 540d" in line for line in said)


def test_word_for_word_claims_are_settled_by_code_and_near_ones_are_pairs():
    class _S:
        def claims(self, **_k):
            row = lambda i, subj, text: {"claim_id": i, "subject": subj,
                                         "subject_name": subj.split(".")[-1], "text": text}
            return [row("1", "model.p.a", "Nothing finer than a decade exists -- it rests on counts."),
                    row("2", "model.p.b", "Nothing finer than a decade exists: it rests on counts."),
                    row("3", "model.p.c", "Every parcel sits in its surveyed section by a point."),
                    row("4", "model.p.d", "Every parcel sits in its surveyed section, by a centroid.")]
    pairs, settled = clusters.claim_pairs(_S())
    assert [sorted(r["claim_id"] for r in g) for g in settled] == [["1", "2"]]
    assert [(a["claim_id"], b["claim_id"]) for _j, a, b in pairs] == [("3", "4")]
    assert clusters.components([("x", "y"), ("y", "z"), ("p", "q")]) == [{"x", "y", "z"},
                                                                          {"p", "q"}]


def _pick(answers):
    def pick(qid, q):
        for k, v in answers.items():
            if qid.startswith(k):
                return v
        return next(iter(q.get("criteria") or {"x": 0}))
    return pick


def test_a_cluster_read_as_one_rule_is_a_finding_on_each_member(tmp_path, monkeypatch):
    target = _project(tmp_path)
    install(monkeypatch, confidence=0.9, pick=_pick({"orule": "one_rule_repeated",
                                                     "odd": "deliberate_exception"}))
    store = tmp_path / "s.duckdb"
    r = runner.invoke(app, ["clusters", "-t", str(target), "--store", str(store),
                            "--config", str(tmp_path), "--judge"])
    assert r.exit_code == 0, r.output + repr(r.exception)
    from dbt_assay import live
    from dbt_assay.store import Store
    project, digests, _f, schema, _s = _load(target, None)
    s = Store(str(store))
    try:
        fs = [f for f in live.all_findings(project, digests, schema, store=s)
              if f.check == "one_rule_or_a_coincidence"]
    finally:
        s.close()
    members = sorted(f.subject_name for f in fs if "TRIM" in f.summary)
    assert members == ["int_c", "stg_a", "stg_b"], "one finding per member, never one per cluster"
    assert len({f.id for f in fs}) == len(fs)
    from dbt_assay import groups
    gs = groups.build(project, fs)
    assert any(set(g.models) >= {"stg_a", "stg_b", "int_c"} for g in gs), \
        "and the plan sees them as one edit"
    doc = json.loads(runner.invoke(app, ["clusters", "-t", str(target), "--store", str(store),
                                         "--config", str(tmp_path), "--json"]).stdout)
    trim = next(c for c in doc["predicate_clusters"] if "TRIM" in c["shape"])
    assert trim["one_rule"]["answer"] == "one_rule_repeated" and "fix_belongs" in trim


def test_an_answer_below_the_floor_is_not_a_finding(tmp_path, monkeypatch):
    target = _project(tmp_path)
    install(monkeypatch, confidence=0.3, pick=_pick({"orule": "one_rule_repeated"}))
    store = tmp_path / "s.duckdb"
    runner.invoke(app, ["clusters", "-t", str(target), "--store", str(store),
                        "--config", str(tmp_path), "--judge"])
    from dbt_assay.store import Store
    project, digests, *_ = _load(target, None)
    s = Store(str(store))
    try:
        assert clusters.findings(project, digests, s) == []
    finally:
        s.close()


def test_dry_run_prices_every_family_and_writes_no_store(tmp_path):
    target = _project(tmp_path)
    store = tmp_path / "never.duckdb"
    r = runner.invoke(app, ["clusters", "-t", str(target), "--store", str(store),
                            "--config", str(tmp_path), "--judge", "--dry-run"])
    assert r.exit_code == 0, r.output
    out = " ".join(r.output.split())
    for fam in clusters.FAMILIES[:3]:
        assert fam in out
    assert "call(s) to make" in out and not store.exists()
