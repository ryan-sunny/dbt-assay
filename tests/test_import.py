"""`assay import`: the verdicts in git, back in a fresh checkout's store."""
from typer.testing import CliRunner

from dbt_assay.cli import _record_one_verdict, app
from dbt_assay.store import Store

runner = CliRunner()


def test_an_export_round_trips_into_an_empty_store_and_gates_the_same(project_dir, tmp_path):
    a = str(tmp_path / "a.duckdb")
    assert runner.invoke(app, ["check", "-t", str(project_dir), "--store", a,
                               "--config", str(tmp_path)]).exit_code == 0
    s = Store(a)
    for i in range(3):
        _record_one_verdict(s, f"model.p.m{i}", "test_cannot_fail", "agree", "", "real", "me",
                            findings=[f"f{i}"])
    _record_one_verdict(s, "model.p.x", "arbitrary_pick", "accept", "", "on purpose", "me",
                        findings=["fx"], until="2999-01-01")
    before = {k: v for k, v in s.adjudication_counts("human").items()}
    s.close()
    seeds = tmp_path / "seeds"
    assert runner.invoke(app, ["export", str(seeds), "--store", a]).exit_code == 0

    b = str(tmp_path / "ci.duckdb")
    r = runner.invoke(app, ["import", str(seeds), "--store", b])
    assert r.exit_code == 0, r.output
    s = Store(b)
    assert s.adjudication_counts("human") == before
    assert "fx" in s.accepted(), "an accept keeps its reason and its date"
    base, rows = s.baseline_findings("p")
    assert base and rows, "the run and its findings came back, so --new-only has a baseline"
    s.close()

    r = runner.invoke(app, ["import", str(seeds), "--store", b])
    assert "0" in r.output and r.exit_code == 0
    s = Store(b)
    assert s.adjudication_counts("human") == before, "a second import adds nothing"
    s.close()


def test_a_store_row_is_never_overwritten_by_the_file(tmp_path):
    a = str(tmp_path / "a.duckdb")
    s = Store(a)
    _record_one_verdict(s, "model.p.m", "q", "agree", "", "from the file", "me")
    s.close()
    seeds = tmp_path / "seeds"
    runner.invoke(app, ["export", str(seeds), "--store", a])
    b = str(tmp_path / "b.duckdb")
    s = Store(b)
    _record_one_verdict(s, "model.p.m", "q", "disagree", "", "mine", "me")
    s.close()
    runner.invoke(app, ["import", str(seeds), "--store", b])
    s = Store(b)
    notes = {r[0] for r in s.con.execute(
        "select note from adjudications where subject = 'model.p.m'").fetchall()}
    s.close()
    assert "mine" in notes


def test_an_unknown_table_is_refused(tmp_path):
    (tmp_path / "seeds").mkdir()
    r = runner.invoke(app, ["import", str(tmp_path / "seeds"), "--store",
                            str(tmp_path / "s.duckdb"), "--tables", "nonsense"])
    assert r.exit_code == 2 and "not a table" in r.output
