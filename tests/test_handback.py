"""Handbacks on a server: verdicts only (S3), a handback folder (S4), and `assay serve` (S1, S2).

sunny-data runs assay on a box under Dagster, audit.yml comes from git there, and the form is
opened over Tailscale. A handback must reach the store without a terminal, and must never edit
the box's audit.yml.
"""
import json
import os
import time

from typer.testing import CliRunner

from dbt_assay import __version__, handback
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


# ------------------------------------------------------------------ the decisions file (D14)
# Ryan: "being able to track the changes made and by who is important ... agent should be able to
# use those too so unapplying or rolling back shit is easy"

FIXED = {"by": "ryan", "verdicts": PAYLOAD["verdicts"],
         "fixes": [{"fix": "fx1", "verdict": "approve", "title": "Add a test", "kind": "document",
                    "exclude": ["i2"]}],
         "config": PAYLOAD["config"]}


def _verdict(s, subject="model.p.orders"):
    row = s.con.execute("select verdict, decided_by, note from adjudications where subject = ? "
                        "and question = 'arbitrary_pick'", [subject]).fetchone()
    return row and tuple(row)


def test_a_save_is_kept_as_a_file_that_says_who_and_what_it_replaced(tmp_path):
    from dbt_assay import fixes
    from dbt_assay.store import Store
    s = Store(str(tmp_path / "s.duckdb"))
    # the same key a save writes: a structural check's verdict is versioned by the running assay
    s.adjudicate("model.p.orders", "arbitrary_pick", "arbitrary_pick", "", "disagree",
                 note="was wrong", who="dana", prompt_version=f"assay.{__version__}")
    kept, _got = handback.save(s, tmp_path / "hb", FIXED)
    assert kept.name.startswith("decisions-") and kept.name.endswith("-ryan.json")
    doc = json.loads(kept.read_text())
    assert doc["by"] == "ryan" and doc["fixes"] == FIXED["fixes"]
    assert doc["recorded"]["fixes_decided"]["approved"] == 1 and "undo" not in doc["recorded"]
    before = [u["before"] for u in doc["undo"] if u.get("subject") == "model.p.orders"]
    assert before and before[0]["verdict"] == "disagree" and before[0]["decided_by"] == "dana"
    assert fixes.statuses(s)["fx1"] == {"status": "approved", "note": "", "by": "ryan",
                                        "at": fixes.statuses(s)["fx1"]["at"], "excluded": ["i2"]}
    # withdrawing puts dana's verdict back, and the fix back to proposed
    out = handback.withdraw(s, doc, "ryan", kept.name)
    assert out["restored"] == len(doc["undo"]) and not out["skipped"], out
    assert _verdict(s) == ("disagree", "dana", "was wrong")
    assert _verdict(s, "model.p.orders::finding::abc123") is None, "a new row was not removed"
    assert fixes.statuses(s)["fx1"]["status"] == "proposed"
    assert "withdrawn: " + kept.name in fixes.statuses(s)["fx1"]["note"]
    s.close()


def test_withdrawing_leaves_what_somebody_decided_again_since(tmp_path):
    from dbt_assay.store import Store
    s = Store(str(tmp_path / "s.duckdb"))
    kept, _got = handback.save(s, tmp_path / "hb", FIXED)
    s.adjudicate("model.p.orders", "arbitrary_pick", "arbitrary_pick", "", "disagree",
                 note="changed my mind", who="ryan", prompt_version=f"assay.{__version__}")
    out = handback.withdraw(s, json.loads(kept.read_text()), "ryan")
    assert out["skipped"] == ["model.p.orders arbitrary_pick: ruled again since"], out
    assert _verdict(s) == ("disagree", "ryan", "changed my mind")
    s.close()


def test_a_file_that_never_recorded_cannot_be_withdrawn_and_is_not_recorded_twice(tmp_path,
                                                                                 project_dir):
    from dbt_assay.store import Store
    s = Store(str(tmp_path / "s.duckdb"))
    assert "error" in handback.withdraw(s, PAYLOAD, "ryan")
    kept, _got = handback.save(s, tmp_path / "hb", FIXED)
    s.close()
    r = CliRunner().invoke(app, ["review", "--load", str(kept), "--store",
                                 str(tmp_path / "s.duckdb")])
    assert r.exit_code == 1 and "not recorded twice" in " ".join(r.output.split()), r.output
    from dbt_assay.mcp_server import Backend
    be = Backend(str(project_dir), str(tmp_path / "s.duckdb"), config_path=str(tmp_path))
    assert "not recorded twice" in be.load_handback(str(kept))["error"]


def test_the_agent_gets_the_fixes_the_config_diff_and_where_to_commit(tmp_path, project_dir):
    """Through the box's MCP server: `decisions` lists the saves and gives one in full, and
    `withdraw_decisions` takes it back and marks the file."""
    from dbt_assay.mcp_server import Backend
    from dbt_assay.store import Store
    (tmp_path / "audit.yml").write_text("# ours\nvocab:\n  wdid:\n    means: ''\n")
    s = Store(str(tmp_path / "s.duckdb"))
    kept, _got = handback.save(s, tmp_path / "hb", FIXED)
    s.close()
    be = Backend(str(project_dir), str(tmp_path / "s.duckdb"), config_path=str(tmp_path),
                 handbacks=str(tmp_path / "hb"), verdicts_only=True)
    ls = be.decisions()
    assert [d["name"] for d in ls["decisions"]] == [kept.name]
    assert ls["decisions"][0]["fixes_approved"] == 1 and ls["decisions"][0]["recorded"]
    d = be.decisions(kept.name)
    assert d["approved"] == ["fx1"] and d["fixes"][0]["exclude"] == ["i2"]
    assert d["commit_as"] == f"assay_decisions/{kept.name}"
    assert "+    means: a structure id" in d["config"]["diff"], d["config"]
    assert "-    means: ''" in d["config"]["diff"] and not d["config"]["not_placed"]
    assert d["config"]["diff"].startswith("--- audit.yml"), d["config"]["diff"]
    assert (tmp_path / "audit.yml").read_text().startswith("# ours"), "the diff wrote audit.yml"
    assert "by" in be.withdraw_decisions(kept.name)["error"]
    assert "no decisions file" in be.withdraw_decisions("nope.json", by="ryan")["error"]
    out = be.withdraw_decisions(kept.name, by="ryan")
    assert out["restored"] >= 3 and not out["skipped"], out
    assert json.loads(kept.read_text())["withdrawn"]["by"] == "ryan"
    assert be.decisions()["decisions"][0]["withdrawn"]
    assert "withdrawn already" in be.withdraw_decisions(kept.name, by="ryan")["error"]


def test_the_cli_lists_shows_and_withdraws(tmp_path):
    from dbt_assay.store import Store
    (tmp_path / "audit.yml").write_text("vocab: {}\n")
    store = str(tmp_path / "s.duckdb")
    s = Store(store)
    kept, _got = handback.save(s, tmp_path / "hb", FIXED)
    s.close()
    base = ["--handbacks", str(tmp_path / "hb"), "--store", store, "--config", str(tmp_path)]
    r = CliRunner().invoke(app, ["decisions", *base])
    assert r.exit_code == 0 and kept.name in r.output and "1 fix(es) approved" in r.output
    r = CliRunner().invoke(app, ["decisions", kept.name, *base])
    assert r.exit_code == 0 and "assay_decisions/" + kept.name in r.output, r.output
    r = CliRunner().invoke(app, ["decisions", kept.name, "--withdraw", *base])
    assert r.exit_code == 2 and "--by" in r.output
    r = CliRunner().invoke(app, ["decisions", kept.name, "--withdraw", "--by", "ryan", *base])
    assert r.exit_code == 0 and "withdrew" in r.output, r.output


def test_a_load_is_kept_as_a_decisions_file_when_there_is_a_folder(tmp_path, project_dir):
    from dbt_assay.mcp_server import Backend
    store = tmp_path / "s.duckdb"
    CliRunner().invoke(app, ["check", "--target", str(project_dir), "--store", str(store)])
    src = tmp_path / "decisions-2026-09-26-1432-ryan.json"
    src.write_text(json.dumps(FIXED))
    be = Backend(str(project_dir), str(store), config_path=str(tmp_path),
                 handbacks=str(tmp_path / "kept"))
    out = be.load_handback(str(src))
    assert out["recorded"] == 1 and "undo" not in out
    assert out["commit_as"] == f"assay_decisions/{out['decisions_file']}"
    assert handback.is_saved(json.loads((tmp_path / "kept" / out["decisions_file"]).read_text()))
