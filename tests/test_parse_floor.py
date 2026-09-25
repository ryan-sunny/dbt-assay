"""RC 06c18da on the box: an incompatible sqlglot failed all 358 parses, every step reported
success, and `ask` spent $0.10 on nothing. Below half the readable models parsing, a command stops
before it counts or sends anything; `scan` and `onboard`, which exist to report it, still run. And a
failure reading the extra counted shapes never costs a model its parse."""
from typer.testing import CliRunner

from dbt_assay import parse
from dbt_assay.cli import app


def _break_every_parse(monkeypatch):
    real = parse.digest

    def broken(sql, name="", dialect="duckdb"):
        d = real(sql, name, dialect)
        d.ok, d.error = False, "AttributeError: module has no attribute 'X'"
        return d
    monkeypatch.setattr("dbt_assay.cli.digest", broken)
    monkeypatch.setenv("ASSAY_NO_DIGEST_CACHE", "1")


def test_a_command_stops_when_almost_nothing_parsed(project_dir, tmp_path, monkeypatch):
    _break_every_parse(monkeypatch)
    r = CliRunner().invoke(app, ["check", "-t", str(project_dir), "--store",
                                 str(tmp_path / "s.duckdb")])
    assert r.exit_code == 3, r.output
    r = CliRunner().invoke(app, ["ask", "-t", str(project_dir), "--store",
                                 str(tmp_path / "s.duckdb"), "--dry-run"])
    assert r.exit_code == 3, r.output
    # the commands that report the failure still run
    r = CliRunner().invoke(app, ["scan", "-t", str(project_dir)])
    assert r.exit_code != 3, r.output
    monkeypatch.setenv("ASSAY_ALLOW_LOW_PARSE", "1")
    r = CliRunner().invoke(app, ["check", "-t", str(project_dir), "--store",
                                 str(tmp_path / "s.duckdb")])
    assert r.exit_code != 3


def test_a_failure_in_the_counted_shapes_never_costs_the_parse(monkeypatch):
    from dbt_assay import sqlpatterns

    def boom(tree, dialect="duckdb"):
        raise AttributeError("module 'sqlglot.expressions' has no attribute 'Localtimestamp'")
    monkeypatch.setattr(sqlpatterns, "facts", boom)
    d = parse.digest("select now() as t, id from x", "m")
    assert d.ok and d.patterns == []
