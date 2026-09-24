"""`check --select` and `--new-only`: the two flags that let assay sit in an edit hook.

*** A TEN-MINUTE, WHOLE-PROJECT COMMAND CANNOT GATE ONE EDIT. ***
Every piece of assay's enforcement was skill prose asking an agent to remember, and the field
measured what that is worth. A hook stops the agent whether or not it read anything, and a hook
needs a command that answers about ONE model, against what was there before the edit.
"""
import json
from pathlib import Path

from typer.testing import CliRunner

from dbt_assay.cli import app
from dbt_assay.live import new_findings

runner = CliRunner()


def _compiled(target: Path, name: str) -> Path:
    return next((target / "compiled").rglob(f"{name}.sql"))


def test_select_reports_only_the_selected_model(project_dir, tmp_path):
    r = runner.invoke(app, ["check", "-t", str(project_dir), "--store", str(tmp_path / "s.duckdb"),
                            "--config", str(tmp_path), "--select", "stg_bad_tilde", "--json"])
    assert r.exit_code == 0, r.output
    doc = json.loads(r.output)
    assert doc["scope"] == ["stg_bad_tilde"]
    assert doc["findings"], "the fixture's `~ 'Denver'` is a finding on this model"
    assert {f["model"] for f in doc["findings"]} == {"stg_bad_tilde"}


def test_a_scoped_run_is_never_written_as_a_run(project_dir, tmp_path):
    """If it were, the next full run's `vs previous run` would call everything outside the
    selection resolved, and a hook's own run would become its baseline."""
    store = tmp_path / "s.duckdb"
    runner.invoke(app, ["check", "-t", str(project_dir), "--store", str(store),
                        "--config", str(tmp_path), "--select", "stg_bad_tilde"])
    import duckdb
    if store.exists():
        con = duckdb.connect(str(store))
        assert con.execute("select count(*) from runs").fetchone()[0] == 0
        con.close()


def test_a_selector_matching_nothing_is_an_error_not_a_clean_pass(project_dir, tmp_path):
    r = runner.invoke(app, ["check", "-t", str(project_dir), "--config", str(tmp_path),
                            "--store", str(tmp_path / "s.duckdb"), "--select", "no_such_model"])
    assert r.exit_code == 2
    assert "matches no model" in " ".join(r.output.split())


def test_new_only_without_a_baseline_refuses_rather_than_passing(project_dir, tmp_path):
    r = runner.invoke(app, ["check", "-t", str(project_dir), "--config", str(tmp_path),
                            "--store", str(tmp_path / "none.duckdb"), "--new-only"])
    assert r.exit_code == 2
    assert "baseline" in r.output


def test_new_only_catches_what_the_edit_introduced_and_nothing_else(project_dir, tmp_path):
    store = str(tmp_path / "s.duckdb")
    base = ["-t", str(project_dir), "--store", store, "--config", str(tmp_path)]
    assert runner.invoke(app, ["check", *base]).exit_code == 0          # the baseline

    # Unchanged: the existing `stg_bad_tilde` finding is old, so nothing is new.
    r = runner.invoke(app, ["check", *base, "--select", "stg_bad_tilde", "--new-only", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["findings"] == []

    # The edit: a pattern with no metacharacters under `~` in a model that was clean.
    _compiled(project_dir, "stg_ok_tilde").write_text("select id from raw.t where name ~ 'Boulder'")
    r = runner.invoke(app, ["check", *base, "--select", "stg_ok_tilde", "--new-only", "--json"])
    assert r.exit_code == 1, r.output
    doc = json.loads(r.output)
    assert doc["baseline"]["run_id"]
    assert [f["model"] for f in doc["findings"]] == ["stg_ok_tilde"]

    # And blocking once does not make it old: the scoped run wrote nothing.
    r = runner.invoke(app, ["check", *base, "--select", "stg_ok_tilde", "--new-only", "--json"])
    assert r.exit_code == 1


class _F:
    def __init__(self, fid, check, subject):
        self.id, self.check, self.subject = fid, check, subject


def test_a_reworded_finding_is_matched_by_count_not_called_new():
    base = [("a", "test_cannot_fail", "m"), ("b", "test_cannot_fail", "m")]
    now = [_F("a", "test_cannot_fail", "m"), _F("b2", "test_cannot_fail", "m")]
    assert new_findings(now, base) == []
    third = _F("c", "test_cannot_fail", "m")
    assert new_findings([*now, third], base) == [third]
    other = _F("d", "arbitrary_pick", "m")
    assert new_findings([*now, other], base) == [other]


def _runs(store):
    import duckdb
    c = duckdb.connect(str(store), read_only=True)
    try:
        return c.execute("select run_id, scope from runs order by started_at").fetchall()
    finally:
        c.close()


def test_a_check_scoped_run_is_history_and_never_the_baseline(project_dir, tmp_path):
    """*** `--check` WROTE A PARTIAL RUN AND THE LOOP NUMBER SAID "63 ARE GONE". ***

    Reported from the field (25.3). `--select` already refused to write; `--check`, one flag
    over, wrote 21 of 536 findings as a run, and the next diff and the loop line read from it.
    """
    from dbt_assay.store import Store
    store = tmp_path / "s.duckdb"
    args = ["check", "-t", str(project_dir), "--store", str(store), "--config", str(tmp_path)]
    runner.invoke(app, args)
    full = _runs(store)[-1][0]
    r = runner.invoke(app, [*args, "--check", "arbitrary_pick"])
    assert "scoped to --check arbitrary_pick" in r.output
    assert "vs previous run" not in r.output and "are gone" not in r.output
    _scoped_id, scope = _runs(store)[-1]
    assert scope == "check:arbitrary_pick"
    s = Store(str(store))
    try:
        assert s.latest_run("p") == full
        assert s.baseline_findings("p")[0]["run_id"] == full
    finally:
        s.close()
    # The next full run compares with the previous FULL run, so nothing moved.
    r = runner.invoke(app, args)
    assert "vs previous run: 0 new, 0 resolved" in " ".join(r.output.split()), r.output


def _fake_runs(store_path, shapes):
    """Runs with findings of the given (n, checks) shapes, one minute apart."""
    from datetime import datetime, timedelta, timezone

    from dbt_assay.store import Store
    s = Store(str(store_path))
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for i, (n, checks) in enumerate(shapes):
        rid = f"r{i}"
        s.con.execute("insert into runs (run_id, started_at, project) values (?, ?, 'p')",
                      [rid, t0 + timedelta(minutes=i)])
        for k in range(n):
            s.con.execute("insert into findings (run_id, check_name, subject, summary) "
                          "values (?, ?, ?, ?)", [rid, checks[k % len(checks)], f"m{k}", f"s{k}"])
    s.close()


def test_a_partial_run_written_before_scope_existed_is_marked_once(tmp_path):
    from dbt_assay.store import Store
    _fake_runs(tmp_path / "a.duckdb",
               [(30, ["a", "b", "c"]), (4, ["b"]), (30, ["a", "b", "c"])])
    s = Store(str(tmp_path / "a.duckdb"))
    try:
        assert s.latest_run("p") == "r2"
        rows = dict(s.con.execute("select run_id, scope from runs").fetchall())
        assert rows == {"r0": None, "r1": "check:b (inferred)", "r2": None}
        assert s.previous_run("p", "r2") == "r0"
    finally:
        s.close()


def test_a_project_with_one_kind_of_finding_is_never_marked(tmp_path):
    """Full runs of one check look like the partial shape; the neighbours tell them apart."""
    from dbt_assay.store import Store
    _fake_runs(tmp_path / "b.duckdb", [(30, ["a"]), (4, ["a"]), (30, ["a"])])
    s = Store(str(tmp_path / "b.duckdb"))
    try:
        assert s.scoped_runs_marked == 0
    finally:
        s.close()


def test_the_monitoring_list_is_what_the_module_emits():
    import re
    from pathlib import Path

    from dbt_assay import elementary
    src = Path(elementary.__file__).read_text()
    assert set(re.findall(r'check="([a-z_]+)"', src)) == set(elementary.MONITORING_CHECKS)


def test_a_check_a_run_did_not_evaluate_is_neither_resolved_nor_new(tmp_path):
    """Reported from the field: a run without --verify called 5 monitoring findings resolved."""
    from datetime import datetime, timedelta, timezone

    from dbt_assay.store import Store
    s = Store(str(tmp_path / "s.duckdb"))
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for i, (rid, unchecked, checks) in enumerate((
            ("verified", None, ["volume_is_not_being_watched", "arbitrary_pick"]),
            ("plain", '["volume_is_not_being_watched"]', ["arbitrary_pick"]),
            ("verified2", None, ["volume_is_not_being_watched", "arbitrary_pick"]))):
        s.con.execute("insert into runs (run_id, started_at, project, unchecked) "
                      "values (?, ?, 'p', ?)", [rid, t0 + timedelta(minutes=i), unchecked])
        for c in checks:
            s.con.execute("insert into findings (run_id, check_name, subject, summary) "
                          "values (?, ?, 'm', 's')", [rid, c])
    try:
        assert s.diff("verified", "plain") == {"new": [], "gone": [], "same": 1, "reworded": 0}
        assert s.diff("plain", "verified2") == {"new": [], "gone": [], "same": 1, "reworded": 0}
        assert s.diff("verified", "verified2")["same"] == 2
    finally:
        s.close()


def test_a_run_before_the_column_that_held_no_monitoring_finding_is_marked(tmp_path):
    from dbt_assay.store import Store
    _fake_runs(tmp_path / "m.duckdb",
               [(3, ["volume_is_not_being_watched", "a"]), (3, ["a"])])
    s = Store(str(tmp_path / "m.duckdb"))
    try:
        assert s.unchecked("r1") >= {"volume_is_not_being_watched"}
        assert s.unchecked("r0") == set()
    finally:
        s.close()
