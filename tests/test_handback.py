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
    # ...and says the one command that applies it from a checkout
    assert f"assay review --load {hb.name} --apply" in r.output, r.output
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


def test_a_card_ruled_finding_by_finding_records_each_and_no_single_answer(tmp_path):
    """Ryan: "what if some findings on a model are right and some aren't". A split card hands
    back one row per verdict; each finding keeps its own, and the pair reads `unclear`, saying
    how it split, so the check is counted as neither agreeing nor disagreeing on that model."""
    from dbt_assay.store import Store
    s = Store(str(tmp_path / "s.duckdb"))
    rows = [{"subject": "model.p.orders", "question": "column_has_no_description",
             "verdict": v, "note": "two are generated", "findings": f, "split": 3}
            for v, f in (("agree", ["a1"]), ("disagree", ["a2", "a3"]))]
    out = handback.record(s, {"by": "ryan", "verdicts": rows})
    assert out["recorded"] == 1 and out["split_cards"] == 1
    assert out["findings_agreed"] == 1 and out["findings_dismissed"] == 2
    got = dict(s.con.execute("select subject, verdict from adjudications").fetchall())
    assert got["model.p.orders::finding::a1"] == "agree"
    assert got["model.p.orders::finding::a2"] == got["model.p.orders::finding::a3"] == "disagree"
    assert got["model.p.orders"] == "unclear"
    note = s.con.execute("select note from adjudications where subject = 'model.p.orders'"
                         ).fetchone()[0]
    assert "1 agree, 2 disagree" in note and "two are generated" in note
    assert ("model.p.orders", "column_has_no_description") in s.ruled_pairs()
    s.close()
