"""Handbacks on a server: verdicts only (S3), a handback folder (S4), and `assay serve` (S1, S2).

sunny-data runs assay on a box under Dagster, audit.yml comes from git there, and the form is
opened over Tailscale. A handback must reach the store without a terminal, and must never edit
the box's audit.yml.
"""
import json
import os
import time

from typer.testing import CliRunner

from dbt_assay import handback
from dbt_assay.cli import app
from dbt_assay.config import Config

PAYLOAD = {"by": "ryan", "verdicts": [
    {"subject": "model.p.orders", "question": "arbitrary_pick", "verdict": "agree",
     "note": "real", "model": "orders", "findings": ["abc123"]}],
    "config": [{"path": ["vocab", "wdid", "means"], "value": "a structure id"}]}


def test_s3_verdicts_only_records_and_leaves_audit_yml_byte_identical(tmp_path, project_dir):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    yml = cfg / "audit.yml"
    yml.write_text("# the box's audit.yml, from git\nvocab: {}\n")
    before = yml.read_bytes()
    hb = tmp_path / "handback.json"
    hb.write_text(json.dumps(PAYLOAD))
    store = tmp_path / "s.duckdb"
    r = CliRunner().invoke(app, ["check", "--target", str(project_dir), "--store", str(store)])
    assert r.exit_code in (0, 1), r.output
    r = CliRunner().invoke(app, ["review", "--load", str(hb), "--verdicts-only", "--apply",
                                 "--store", str(store), "--config", str(cfg)])
    assert r.exit_code == 0, r.output
    assert yml.read_bytes() == before, "a verdicts-only load edited audit.yml"
    assert "refused 1 config edit" in r.output and "vocab.wdid.means" in r.output
    assert "recorded 1 verdict" in r.output


def test_s4_the_handback_folder_comes_from_audit_yml_and_the_newest_wins(tmp_path):
    (tmp_path / "audit.yml").write_text("review:\n  handbacks: warehouse/assay/handbacks\n")
    cfg = Config.load(tmp_path)
    where = handback.folder(cfg)
    assert where == tmp_path / "warehouse/assay/handbacks", where
    assert handback.folder(cfg, "/elsewhere") == handback.Path("/elsewhere")
    where.mkdir(parents=True)
    old, new = where / "handback-1.json", where / "handback-2.json"
    old.write_text("{}")
    new.write_text("{}")
    t = time.time()
    os.utime(old, (t - 60, t - 60))
    os.utime(new, (t, t))
    assert handback.newest(where) == new
    assert handback.folder(Config()) == handback.DOWNLOADS


def test_the_mcp_tool_loads_from_the_folder_and_refuses_config(tmp_path, project_dir):
    from dbt_assay.mcp_server import Backend
    store = tmp_path / "s.duckdb"
    CliRunner().invoke(app, ["check", "--target", str(project_dir), "--store", str(store)])
    folder = tmp_path / "hb"
    folder.mkdir()
    (folder / "handback-x.json").write_text(json.dumps(PAYLOAD))
    be = Backend(str(project_dir), str(store), config_path=str(tmp_path), handbacks=str(folder))
    out = be.load_handback(verdicts_only=True)
    assert out.get("recorded") == 1, out
    assert out["file"].endswith("handback-x.json")
    assert out["config_refused"] == ["vocab.wdid.means: a structure id"], out
