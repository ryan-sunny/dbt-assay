"""*** A CAPABILITY CHECK THAT CAN BE WRONG NEEDS A WAY TO SEE WHAT IT DECIDED. ***

assay read only `os.environ`, so a key in a `.env` was invisible and every judged command reported
the tier as off. It was wrong for weeks and nothing in the tool could show it.
"""
import pytest
from typer.testing import CliRunner

from dbt_assay.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    for name in ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    from dbt_assay import jev
    monkeypatch.setattr(jev, "_FROM_ENV", None)
    monkeypatch.setattr(jev, "_DOTENV_PATH", None)


def test_it_says_there_is_no_key_when_there_is_none(tmp_path):
    r = runner.invoke(app, ["config", "--config", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "none" in r.output
    assert "auto" in r.output


def test_a_key_in_a_dotenv_is_a_key(tmp_path):
    (tmp_path / ".env").write_text("TYPESAFE_API_KEY=sk-from-the-file\n")
    r = runner.invoke(app, ["config", "--config", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "found" in r.output
    assert ".env" in r.output
    assert "sk-from-the-file" not in r.output          # never print the key itself


def test_an_exported_variable_beats_the_file(tmp_path, monkeypatch):
    """A stale file must not win over what someone just typed into their shell."""
    (tmp_path / ".env").write_text("TYPESAFE_API_KEY=sk-from-the-file\n")
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-from-the-shell")
    from dbt_assay import jev
    jev._FROM_ENV = None
    jev._DOTENV_PATH = None
    _n, _spec, key = jev.resolve_provider("typesafe")
    assert key == "sk-from-the-shell"


def test_a_comment_or_a_blank_line_does_not_break_the_file(tmp_path):
    (tmp_path / ".env").write_text("# a comment\n\nTYPESAFE_API_KEY=sk-ok\n")
    r = runner.invoke(app, ["config", "--config", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "found" in r.output
