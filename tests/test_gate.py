"""`assay gate`: one verdict for a change (assay-loops.md gap 4). Each part passes, fails, or says
it could not look; a part that could not look is never a pass."""
from types import SimpleNamespace

from typer.testing import CliRunner

from dbt_assay import gate
from dbt_assay.cli import app


def _f(check, subject="model.p.a", ev=None):
    return SimpleNamespace(check=check, subject=subject, subject_name=subject.split(".")[-1],
                           summary="s", evidence=ev or {})


def _eval(**kw):
    base = {"new": [], "actions": {}, "premises_now": {}, "premises_before": {"x": ("holding", "")},
            "contract_changes": [], "allowed": set(), "test_results": {"t": ("pass", "")}}
    return gate.evaluate(**{**base, **kw})


def test_a_clean_change_passes_and_says_what_it_looked_at():
    got = _eval()
    assert got["verdict"] == "pass" and got["line"].startswith("PASS: 6 of 6")


def test_each_part_fails_on_its_own_evidence():
    assert _eval(new=[_f("arbitrary_pick")],
                 actions={("arbitrary_pick", "model.p.a"): "fail"})["verdict"] == "fail"
    assert _eval(new=[_f("arbitrary_pick")],
                 actions={("arbitrary_pick", "model.p.a"): "queue"})["verdict"] == "pass"
    assert _eval(new=[_f("test_is_failing")])["verdict"] == "fail"
    assert _eval(new=[_f("model_refs_nothing", ev={"evaluator": "fct_x"})])["verdict"] == "fail"
    assert _eval(premises_now={"x": ("broken", "`id` unique in `a`")})["verdict"] == "fail"
    assert _eval(premises_now={"x": ("broken", "")},
                 premises_before={"x": ("broken", "")})["verdict"] == "pass"   # not NEWLY broken
    ch = [SimpleNamespace(model="orders", detail="grain changed")]
    assert _eval(contract_changes=ch)["verdict"] == "fail"
    assert _eval(contract_changes=ch, allowed={"orders"})["verdict"] == "pass"
    assert _eval(test_results={"test.p.t": ("fail", "")})["verdict"] == "fail"


def test_a_part_that_could_not_look_is_skipped_never_passed():
    got = _eval(new=None, contract_changes=None, test_results={}, premises_before={})
    skipped = [p["part"] for p in got["parts"] if p["status"] == "skipped"]
    assert len(skipped) == 6 and "skipped" in got["line"]
    assert "gate: PASS" in gate.markdown(got)


def test_gate_runs_against_the_last_full_check(tmp_path, project_dir):
    store = str(tmp_path / "s.duckdb")
    CliRunner().invoke(app, ["check", "-t", str(project_dir), "--store", store])
    r = CliRunner().invoke(app, ["gate", "-t", str(project_dir), "--store", store, "--json"])
    assert r.exit_code == 0, r.output
    import json
    got = json.loads(r.output)
    parts = {p["part"]: p["status"] for p in got["parts"]}
    assert parts["findings above policy"] == "pass" and parts["contracts"] == "skipped"
    # against itself as the baseline target, nothing changed meaning
    r = CliRunner().invoke(app, ["gate", "-t", str(project_dir), "-b", str(project_dir),
                                 "--store", store, "--json"])
    assert json.loads(r.output)["parts"][4]["status"] == "pass", r.output
