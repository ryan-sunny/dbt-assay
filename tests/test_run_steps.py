"""`assay run`: several steps in one process (sunny-data's daily was eleven invocations, each
paying its own start). Every step runs, each prints a line, the shared options reach the steps
that take them, and one failure fails the run without stopping the steps after it."""
import json

from typer.testing import CliRunner

from dbt_assay.cli import app


def test_steps_run_in_order_and_each_says_how_it_went(tmp_path, project_dir):
    store = tmp_path / "s.duckdb"
    page = tmp_path / "p.html"
    out = tmp_path / "cost.json"
    r = CliRunner().invoke(app, ["run", "check", f"page {page}", f"cost --json >{out}",
                                 "--target", str(project_dir), "--store", str(store)], env={"COLUMNS": "2000"})
    lines = [x for x in r.output.splitlines() if x.startswith("[")]
    assert [x.split(":")[0] for x in lines if "starting" not in x] == \
        ["[1/3] check", "[2/3] page", "[3/3] cost"], r.output
    run = json.loads(next(x for x in r.output.splitlines() if x.startswith("RUN "))[4:])
    assert [s["ok"] for s in run["steps"]][1:] == [True, True], run
    assert page.exists() and page.stat().st_size > 20_000
    assert "calls" in json.loads(out.read_text())         # `>path` took that step's stdout
    # the shared --target and --store reached the steps that take them, and not `cost`'s target
    assert f"--target {project_dir}" in r.output and f"--store {store}" in r.output


def test_a_failed_step_fails_the_run_and_the_next_one_still_runs(tmp_path, project_dir):
    store = tmp_path / "s.duckdb"
    r = CliRunner().invoke(app, ["run", "nosuchstep", "check", "--target", str(project_dir),
                                 "--store", str(store)], env={"COLUMNS": "2000"})
    run = json.loads(next(x for x in r.output.splitlines() if x.startswith("RUN "))[4:])
    assert run["failed"][0] == "nosuchstep" and len(run["steps"]) == 2
    assert r.exit_code == 1
