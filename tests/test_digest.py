"""`assay digest`: only what moved since the previous full run, and `{}` when nothing did
(assay-loops.md gap 3)."""
import json

from typer.testing import CliRunner

from dbt_assay import digest
from dbt_assay.cli import app
from dbt_assay.store import Store


def _run(s, rid, at, rows):
    s.con.execute("insert into runs (run_id, started_at, project) values (?, ?, 'p')", [rid, at])
    for fid, check, subj, summ, exp in rows:
        s.con.execute("insert into findings (run_id, check_name, subject, subject_name, summary, "
                      "evidence, finding_id, exposures) values (?,?,?,?,?,'{}',?,?)",
                      [rid, check, subj, subj.split('.')[-1], summ, fid, json.dumps(exp)])


def test_quiet_when_nothing_moved_and_news_first_when_it_did(tmp_path):
    s = Store(str(tmp_path / "s.duckdb"))
    s.con.execute("select 1 from findings limit 0")
    base = [("a", "arbitrary_pick", "model.p.m", "x", [])]
    _run(s, "r1", "2026-09-24 06:00:00", base)
    _run(s, "r2", "2026-09-25 06:00:00", base)
    assert digest.build(s) == {}
    _run(s, "r3", "2026-09-26 06:00:00", base + [
        ("b", "test_is_failing", "model.p.m", "`t` fails", []),
        ("c", "guarantee_lost", "model.p.paid", "one row per id: stopped", ["Water report"]),
        ("d", "column_has_no_description", "model.p.m", "cols", [])])
    d = digest.build(s)
    assert d["findings"] == {"new": 3, "resolved": 0, "open": 4}
    assert [e["kind"] for e in d["events"]] == ["guarantee_lost", "test_is_failing"]
    assert "reaches Water report" in digest.lines(d)[1]
    s.close()


def test_a_reworded_summary_is_not_news(tmp_path):
    s = Store(str(tmp_path / "s.duckdb"))
    s.con.execute("select 1 from findings limit 0")
    _run(s, "r1", "2026-09-24 06:00:00", [("a", "test_is_failing", "model.p.m", "16 ways", [])])
    _run(s, "r2", "2026-09-25 06:00:00", [("a2", "test_is_failing", "model.p.m", "17 ways", [])])
    assert digest.build(s) == {}
    s.close()
    r = CliRunner().invoke(app, ["digest", "--store", str(tmp_path / "s.duckdb"), "--json"])
    assert r.exit_code == 0 and json.loads(r.output) == {}


def test_the_trend_is_one_row_per_full_run_oldest_first(tmp_path):
    s = Store(str(tmp_path / "s.duckdb"))
    s.con.execute("select 1 from findings limit 0")
    _run(s, "r1", "2026-09-24 06:00:00", [("a", "test_is_failing", "model.p.m", "x", []),
                                          ("b", "arbitrary_pick", "model.p.m", "y", [])])
    _run(s, "r2", "2026-09-25 06:00:00", [("b", "arbitrary_pick", "model.p.m", "y", [])])
    got = digest.trend(s)
    assert [(t["run"], t["open"], t["harm"]) for t in got] == [("r1", 2, 1), ("r2", 1, 0)]
    s.close()
