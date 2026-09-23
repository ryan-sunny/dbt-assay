"""`contract(model)` carries the model's health, not only its anatomy."""
import json

from typer.testing import CliRunner

from dbt_assay.cli import app
from dbt_assay.mcp_server import Backend
from dbt_assay.store import Store

runner = CliRunner()


def test_a_contract_says_what_is_open_what_is_decided_and_what_was_counted(project_dir,
                                                                           tmp_path):
    store = str(tmp_path / "s.duckdb")
    r = runner.invoke(app, ["check", "-t", str(project_dir), "--store", store,
                            "--config", str(tmp_path), "--json"])
    fs = [f for f in json.loads(r.output)["findings"] if f["model"] == "stg_bad_tilde"]
    assert fs, "the fixture has a finding on this model"
    runner.invoke(app, ["review", "--store", store, "-t", str(project_dir), "--finding",
                        fs[0]["finding"], "--verdict", "agree", "--note", "real", "--by", "me"])

    be = Backend(str(project_dir), store_path=store, config_path=str(tmp_path))
    h = be.contract("stg_bad_tilde")["health"]
    mine = next(f for f in h["open_findings"] if f["finding"] == fs[0]["finding"])
    assert mine["ruled_by"]["verdict"] == "agree" and mine["ruled_by"]["as"] == "human"
    assert "1 a person agreed with" in h["summary"]

    runner.invoke(app, ["review", "--store", store, "-t", str(project_dir), "--finding",
                        fs[0]["finding"], "--verdict", "accept", "--note", "on purpose"])
    be = Backend(str(project_dir), store_path=store, config_path=str(tmp_path))
    h = be.contract("stg_bad_tilde")["health"]
    assert fs[0]["finding"] not in {f["finding"] for f in h["open_findings"]}
    assert any("accepted" in w["why"] for w in h["waived_or_accepted"])


def test_an_uncounted_grain_says_so(project_dir, tmp_path):
    be = Backend(str(project_dir), store_path=str(tmp_path / "none.duckdb"),
                 config_path=str(tmp_path))
    h = be.contract("int_bad_unique")["health"]
    assert h["grain_measured"] is None and "not been counted" in h["grain_note"]


def test_a_counted_grain_is_reported_with_its_date(project_dir, tmp_path):
    from dbt_assay.probe import Observation, write
    store = str(tmp_path / "s.duckdb")
    s = Store(store)
    rel = Backend(str(project_dir)).state().schema.relation["model.p.int_bad_unique"]
    write(s, [Observation(rel.replace('"', ""), "section_id", 10, 10, 10, "unique", "counted")])
    s.close()
    be = Backend(str(project_dir), store_path=store, config_path=str(tmp_path))
    h = be.contract("int_bad_unique")["health"]
    assert h["grain_measured"][0]["status"] == "unique" and h["grain_measured"][0]["rows"] == 10


def test_the_cli_contract_is_the_tools_contract(project_dir, tmp_path):
    r = runner.invoke(app, ["inventory", "-t", str(project_dir), "--model", "int_bad_unique",
                            "--json", "--store", str(tmp_path / "none.duckdb"),
                            "--config", str(tmp_path)])
    assert r.exit_code == 0, r.output
    doc = json.loads(r.output)
    want = Backend(str(project_dir), config_path=str(tmp_path)).contract("int_bad_unique")
    assert doc == json.loads(json.dumps(want, default=str))
    assert "health" in doc
