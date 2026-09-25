"""Findings become fixes (leverage spec): each finding is attributed to the change that resolves
it, the change carries its files, a person approves it, and only then may an agent apply it."""
import json
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
