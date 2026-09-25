"""*** 714 TESTS GREEN, RUFF CLEAN, A FRESH-VENV INSTALL VERIFIED -- AND THE ONE COMMAND THE
    RELEASE WAS ABOUT COULD NOT RUN AT ALL. ***

0.46.0 shipped with `elem.cadence` at one call site and `elem.build_cadence` at the other, because
a rename landed in one place and not the other. `assay volume` died on `AttributeError` the moment
it reached that line. Nothing caught it: the suite exercises the modules, `ruff` sees a name it
cannot resolve across a module boundary as nobody's business, and the release smoke test imported
the package and ran `assay version`.

Every one of those passed while the command was broken. So this file does the only thing that
would have failed: it INVOKES each command, and a command that cannot even be entered -- a missing
attribute, a signature that moved, a typo in a default -- fails here.

It does not check what they print. It checks that they RUN: no `AttributeError`, no `TypeError`,
no `NameError`, no exception the CLI did not choose to raise. A command refusing on purpose is a
pass, because refusing is a thing it decided to do.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from dbt_assay.cli import app

runner = CliRunner()

# The ways a command breaks by not being wired up. A typer.Exit is the command deciding
# something; these are the command failing to start.
BROKEN = (AttributeError, TypeError, NameError, ImportError, KeyError, IndexError)

# Commands that must not be invoked in a test: they serve forever, or they write outside the
# sandbox. Named rather than skipped by pattern, so a new command is covered by default.
NEVER_INVOKED = {
    "mcp": "runs a server until it is killed",
    "watch": "polls the filesystem until it is killed",
}


def _commands() -> list:
    return sorted((c.name or c.callback.__name__.removesuffix("_cmd").replace("_", "-"))
                  for c in app.registered_commands)


def test_every_command_can_be_entered(project_dir, tmp_path, monkeypatch):
    """*** THE GUARD THAT WOULD HAVE CAUGHT IT. ***

    Each command, invoked against a real fixture project and an empty store. A command that dies
    on a name that does not exist fails here, whatever it would have printed.

    From a folder of its own: a command writing its default output (`plan` writes
    assay_fixes.json) must not leave it in the repository.
    """
    monkeypatch.chdir(tmp_path)
    store = str(tmp_path / "s.duckdb")
    broke = []
    for name in _commands():
        if name in NEVER_INVOKED:
            continue
        res = runner.invoke(app, [name, "--target", str(project_dir), "--store", store],
                            catch_exceptions=True)
        exc = res.exception
        # SystemExit/typer.Exit is the command deciding something -- including deciding it needs
        # an argument it was not given. That is wiring that works.
        if exc is not None and isinstance(exc, BROKEN):
            broke.append(f"{name}: {type(exc).__name__}: {exc}")
    assert not broke, (
        "these commands cannot be entered at all, which no other test in this repo would "
        f"notice: {broke}")


def test_the_commands_that_are_never_invoked_are_named_with_a_reason():
    """A skip list that grows by accident is how a command stops being covered quietly."""
    have = set(_commands())
    gone = sorted(set(NEVER_INVOKED) - have)
    assert not gone, f"NEVER_INVOKED names commands that no longer exist: {gone}"
    assert all(len(v) > 15 for v in NEVER_INVOKED.values()), "a bare exemption is not a reason"


@pytest.mark.parametrize("flag", ["--help"])
def test_every_command_renders_its_help(flag):
    """`--help` builds every option, so a default that cannot be constructed fails here."""
    broke = []
    for name in _commands():
        res = runner.invoke(app, [name, flag], catch_exceptions=True)
        if res.exception is not None and isinstance(res.exception, BROKEN):
            broke.append(f"{name}: {type(res.exception).__name__}: {res.exception}")
        elif res.exit_code not in (0, 2):
            broke.append(f"{name}: exit {res.exit_code}")
    assert not broke, broke


def test_the_judged_commands_refuse_without_a_key_rather_than_crashing(project_dir, tmp_path,
                                                                      monkeypatch):
    """The judged tier is opt-in, and opting out must be a refusal with a sentence, never a
    traceback through assay's own internals."""
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    store = str(tmp_path / "s.duckdb")
    broke = []
    for name in ("infer", "columns", "claims", "verify", "traverse", "semantics", "volume"):
        if name not in _commands():
            continue
        res = runner.invoke(app, [name, "--target", str(project_dir), "--store", store,
                                  "--judge"] if name == "volume" else
                            [name, "--target", str(project_dir), "--store", store],
                            catch_exceptions=True)
        if res.exception is not None and isinstance(res.exception, BROKEN):
            broke.append(f"{name}: {type(res.exception).__name__}: {res.exception}")
    assert not broke, broke


# --------------------------------------------------------------- past the front door

def _fake_warehouse(monkeypatch, rows_for):
    """Make `dbt show` answer, so a counted command runs its whole body.

    *** A SMOKE TEST THAT STOPS AT THE FRONT DOOR IS A SMOKE TEST THAT PASSES ON THE BUG. ***
    Written, run, and watched to FAIL first: the version of `test_every_command_can_be_entered`
    above passes with `elem.cadence` restored, because the fixture has no warehouse, `volume`
    reports `unreachable`, and every line after that -- including the broken one -- never runs.
    A guard nobody has watched fail is a guard nobody has tested, so this one was watched.
    """
    from dbt_assay import probe as probe_mod
    from dbt_assay.probe import Result

    def run_sql(sql, project_dir, profiles_dir=None, dbt_bin="dbt", limit=50, timeout=300,
                **_kw):
        got = rows_for(sql, limit)
        # A fixture that cannot express "this statement did not run" cannot test the readers
        # that now depend on the difference, so an empty answer here means a failure -- the same
        # thing `dbt show` reports when a relation is not there.
        return Result(rows=got) if got else Result(failed=True, why="no such relation")
    monkeypatch.setattr(probe_mod, "run_sql", run_sql)
    # A warehouse that answers answers `select 1` too.
    monkeypatch.setattr(probe_mod, "_reach", lambda *_a, **_k: None)


def _elementary_rows(sql: str, limit: int):
    low = sql.lower()
    if "assay_reachable" in low:
        return [{"assay_reachable": 1}]
    if low.strip().startswith("select count(*) as n from"):
        return [{"n": 4}]
    if "count(distinct test_unique_id)" in low:
        return [{"n": 3}]
    if "dbt_invocations" in low:
        return [{"assay_day": f"2026-09-{d:02d}"} for d in (1, 4, 7, 10, 13)]
    if "assay_day" in low:
        return [{"assay_day": f"2026-09-{d:02d}"} for d in (1, 4, 7, 10)]
    if "data_monitoring_metrics" in low:
        return [{"full_table_name": "DB.S.STG_BAD_NOTNULL", "bucket_end": "2026-09-13 00:00:00",
                 "metric_value": 50.0, "assay_rn": 1, "assay_buckets": 4},
                {"full_table_name": "DB.S.STG_BAD_NOTNULL", "bucket_end": "2026-09-10 00:00:00",
                 "metric_value": 100.0, "assay_rn": 2, "assay_buckets": 4}]
    if "elementary_test_results" in low:
        return [{"table_name": "STG_BAD_NOTNULL", "column_name": None,
                 "test_type": "anomaly_detection", "test_sub_type": "row_count",
                 "status": "fail", "detected_at": "2026-07-01 00:00:00"}]
    if "dbt_source_freshness_results" in low:
        return [{"created_at": "2026-07-08 13:08:14"}]
    return []


def test_volume_runs_its_whole_body_when_the_warehouse_answers(project_dir, tmp_path,
                                                               monkeypatch):
    """*** THIS IS THE TEST THAT WOULD HAVE CAUGHT 0.46.0. ***

    `assay volume` shipped calling `elem.cadence`, which a rename had turned into
    `elem.build_cadence` at the other call site. It died on `AttributeError` the moment it got
    past the reachability check -- so the one command the release was about could not run at all,
    while 714 tests, ruff and a fresh-venv install all passed.

    Reaching the warehouse is what makes the rest of the command execute, so the warehouse
    answers here.
    """
    _fake_warehouse(monkeypatch, _elementary_rows)
    store = str(tmp_path / "s.duckdb")
    res = runner.invoke(app, ["volume", "--target", str(project_dir), "--store", store,
                              "--elementary-schema", "elem"], catch_exceptions=True)
    if res.exception is not None and isinstance(res.exception, BROKEN):
        raise AssertionError(
            f"`assay volume` cannot run: {type(res.exception).__name__}: {res.exception}")
    assert res.exit_code == 0, res.output
    # *** IT GOT ALL THE WAY THROUGH, NOT JUST PAST THE DOOR. ***
    # The last line the command prints, so an early return cannot pass this.
    assert "relation" in res.output, "the readings table never rendered"
    assert "neither tool can answer alone" in res.output, (
        "the command exited before its final line, so most of it still did not run")


def test_volume_json_is_json_and_nothing_else(project_dir, tmp_path, monkeypatch):
    """`--json` piped to a file must parse. It did not once: the rich tables went to the same
    stream."""
    import json

    _fake_warehouse(monkeypatch, _elementary_rows)
    res = runner.invoke(app, ["volume", "--target", str(project_dir),
                              "--store", str(tmp_path / "s.duckdb"),
                              "--elementary-schema", "elem", "--json"], catch_exceptions=True)
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert "readings" in payload and "cadence" in payload and "monitoring" in payload


def test_check_verify_runs_the_monitoring_checks(project_dir, tmp_path, monkeypatch):
    """The other place the Elementary reader is called from. Two call sites is how one of them
    got left behind in the first place."""
    _fake_warehouse(monkeypatch, _elementary_rows)
    res = runner.invoke(app, ["check", "--target", str(project_dir),
                              "--store", str(tmp_path / "s.duckdb"), "--verify"],
                        catch_exceptions=True)
    if res.exception is not None and isinstance(res.exception, BROKEN):
        raise AssertionError(
            f"`assay check --verify` cannot run: {type(res.exception).__name__}: "
            f"{res.exception}")
    assert res.exit_code in (0, 1), res.output


def test_version_is_an_option_as_well_as_a_command():
    """Both spellings are natural and one of them errored (25.8)."""
    from typer.testing import CliRunner

    from dbt_assay import __version__
    from dbt_assay.cli import app
    for args in (["--version"], ["version"]):
        r = CliRunner().invoke(app, args)
        assert r.exit_code == 0 and __version__ in r.output, (args, r.output)


def test_disagreements_accepts_the_target_flag_every_neighbour_takes(tmp_path):
    from typer.testing import CliRunner

    from dbt_assay.cli import app
    r = CliRunner().invoke(app, ["disagreements", "-t", "target", "--store",
                                 str(tmp_path / "s.duckdb"), "--json"])
    assert "No such option" not in r.output, r.output


def test_volume_json_says_it_could_not_reach_the_warehouse_and_why(project_dir, tmp_path,
                                                                  monkeypatch):
    """*** EXIT 1 AND AN EMPTY STDOUT. ***

    Reported from the field: `volume --json` printed nothing and exited 1 when it could not reach
    the warehouse, while the same command without `--json` explained itself. Every line went
    through `say`, which `--json` silences. A machine now gets JSON saying it was unreachable and
    dbt's own error, and the person at the terminal gets the explanation on stderr.
    """
    import json

    from dbt_assay import probe as probe_mod
    from dbt_assay.probe import DBT_OUTPUT, Result
    dbt_log = ("\x1b[0m05:09:32  Running with dbt=1.11.12\n\x1b[0m05:09:32  Encountered an error:\n"
               "Runtime Error\n  Could not find profile named 'sunny_data'")
    why = "could not reach the warehouse: `dbt show` failed." + DBT_OUTPUT + dbt_log

    def run_sql(sql, project_dir, profiles_dir=None, dbt_bin="dbt", limit=50, timeout=300, **_kw):
        return Result(failed=True, why=why)
    monkeypatch.setattr(probe_mod, "run_sql", run_sql)
    monkeypatch.setattr(probe_mod, "run_many", lambda stmts, *a, **k:
                        [Result(failed=True, why=why) for _ in stmts])

    res = runner.invoke(app, ["volume", "--target", str(project_dir),
                              "--store", str(tmp_path / "s.duckdb"),
                              "--elementary-schema", "elem", "--json"], catch_exceptions=True)
    assert res.exit_code == 1, res.output
    payload = json.loads(res.stdout)
    assert payload["reachable"] is False
    assert payload["error"] == "dbt said: Runtime Error Could not find profile named 'sunny_data'"
    assert "Could not find profile" in res.stderr, "the person at the terminal got no reason"

    # and without --json the reason is printed once, not once per relation, in dbt's words
    res = runner.invoke(app, ["volume", "--target", str(project_dir),
                              "--store", str(tmp_path / "s.duckdb"),
                              "--elementary-schema", "elem"], catch_exceptions=True)
    assert res.exit_code == 1
    assert res.output.count("Could not find profile named") == 1, res.output


def test_volume_judge_json_keeps_stdout_for_the_document(project_dir, tmp_path, monkeypatch):
    """sunny-data feedback V1: `volume --judge --json > v.json` must parse. The judging progress
    and each bank's summary printed after the document, on stdout."""
    import json

    _fake_warehouse(monkeypatch, _elementary_rows)
    res = runner.invoke(app, ["volume", "--target", str(project_dir),
                              "--store", str(tmp_path / "s.duckdb"),
                              "--elementary-schema", "elem", "--json", "--judge", "--dry-run"],
                        catch_exceptions=True)
    assert res.exception is None or not isinstance(res.exception, BROKEN), res.exception
    payload = json.loads(res.stdout)
    assert "readings" in payload and "monitoring" in payload
    assert "no claims to check a movement against" in res.stderr, res.stderr
