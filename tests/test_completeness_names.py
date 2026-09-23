"""`completeness --verify` names the empty models it counts.

Reported from the field (25.11): "models that are EMPTY 3", and `practices --keys-only` named two
of them. A reader who can find two of three has no way to close the gap but a hand query.
"""
from __future__ import annotations

from typer.testing import CliRunner

from dbt_assay import practices
from dbt_assay.cli import app


def test_the_empty_models_are_named(project_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(practices, "verify_row_loss", lambda *a, **k: 0)
    monkeypatch.setattr(practices, "primary_key_patches", lambda *a, **k: [])
    monkeypatch.setattr(practices, "verify_grains", lambda *a, **k: {
        "int_azcc_owners": (0, 0), "stg_pm_properties": (0, 0), "int_third_one": (0, 0),
        "full_model": (10, 10)})
    r = CliRunner(env={"COLUMNS": "200"}).invoke(
        app, ["completeness", "-t", str(project_dir), "--verify", "--project-dir",
              str(project_dir.parent), "--dbt", "true", "--store", str(tmp_path / "s.duckdb")])
    out = " ".join(r.output.split())
    assert "models that are EMPTY 3" in out, r.output
    for name in ("int_azcc_owners", "stg_pm_properties", "int_third_one"):
        assert name in out
    assert "full_model" not in out
