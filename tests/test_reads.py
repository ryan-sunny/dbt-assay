"""`assay read`: the judged reading `review --reads` takes, which nothing used to write."""
import json

import pytest
from typer.testing import CliRunner

from dbt_assay import cli, reads
from dbt_assay.cli import app
from dbt_assay.store import Store

runner = CliRunner()


def test_a_reading_uses_the_forms_own_verdicts_and_a_selected_reason():
    r = reads.reading({"answer": "misreads_the_sql", "confidence": 0.4,
                       "probabilities": {"misreads_the_sql": 0.6, "correct": 0.3,
                                         "cannot_tell": 0.1}})
    assert r["verdict"] == "disagree"
    crit = reads.bank()["criteria"]["misreads_the_sql"]["what"]
    assert " ".join(crit.split()) in r["why"], "the why is the option's own words"
    assert "0.60" in r["why"] and "correct at 0.30" in r["why"]
    assert set(reads.VERDICT_OF) == set(reads.bank()["criteria"]), "every option maps"


@pytest.fixture
def fake_jev(monkeypatch):
    """No key, no spend: `decide` answers `correct` for everything and counts its calls."""
    calls = []

    class C:
        available, calls, input_tokens, spent_usd, model = True, 0, 0, 0.0, "fake"

        def __init__(self, **_kw):
            pass

    def decide(_store, _client, rec, questions, **_kw):
        calls.append(rec.key)
        return {k: {"kind": "choice", "answer": "correct", "confidence": 0.9,
                    "probabilities": {"correct": 0.9, "cannot_tell": 0.1}} for k in questions}
    monkeypatch.setattr(cli, "Client", C)
    monkeypatch.setattr(cli, "decide", decide)
    return calls


def _args(project_dir, tmp_path, out):
    return ["read", "--out", str(out), "-t", str(project_dir),
            "--store", str(tmp_path / "s.duckdb"), "--config", str(tmp_path)]


def test_dry_run_prices_and_writes_nothing(project_dir, tmp_path, fake_jev):
    out = tmp_path / "reads.json"
    r = runner.invoke(app, [*_args(project_dir, tmp_path, out), "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "card(s) to read" in r.output
    assert not out.exists() and not fake_jev


def test_it_writes_a_file_records_no_verdict_and_never_pays_twice(project_dir, tmp_path,
                                                                    fake_jev):
    out = tmp_path / "reads.json"
    r = runner.invoke(app, _args(project_dir, tmp_path, out))
    assert r.exit_code == 0, r.output
    doc = json.loads(out.read_text())
    assert doc and all(v["verdict"] == "agree" for v in doc.values())
    assert all("::" in k for k in doc), "keyed the way `review --reads` looks them up"
    s = Store(str(tmp_path / "s.duckdb"))
    assert s.con.execute("select count(*) from adjudications").fetchone()[0] == 0
    s.close()

    n = len(fake_jev)
    r = runner.invoke(app, _args(project_dir, tmp_path, out))
    assert r.exit_code == 0 and len(fake_jev) == n, "a card already read is not read again"


def test_the_file_lands_on_the_form(project_dir, tmp_path, fake_jev):
    out = tmp_path / "reads.json"
    runner.invoke(app, _args(project_dir, tmp_path, out))
    form = tmp_path / "form.html"
    r = runner.invoke(app, ["review", "--emit", str(form), "--target", str(project_dir),
                            "--store", str(tmp_path / "s.duckdb"), "--reads", str(out)])
    assert r.exit_code == 0, r.output
    assert "What the findings state is true" in form.read_text()


def test_a_reading_aid_does_not_move_a_finding_id():
    from dbt_assay.checks.structural import Finding
    a = Finding("models_disagree_about_a_column", "m", "m", "", "s", "",
                evidence={"column": "x", "one_of_each": ["1"]})
    b = Finding("models_disagree_about_a_column", "m", "m", "", "s", "",
                evidence={"column": "x", "one_of_each": ["2", "3"]})
    assert a.id == b.id
