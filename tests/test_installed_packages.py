"""An installed package's models are counted by name, never as a gap in the user's audit.

*** "30 MODELS ASSAY COULD NOT READ -- NOT AUDITED, AND NOT A PASS", AND ALL 30 WERE ELEMENTARY'S.
Reported from the field (25.5). The line is the tool's most reassuring-sounding one, and a reader
takes it for thirty of their own models sitting outside the audit.
"""
from __future__ import annotations

import json

from typer.testing import CliRunner

from dbt_assay.cli import _load, app


def _add_package_model(target) -> None:
    m = json.loads((target / "manifest.json").read_text())
    uid = "model.elementary.dbt_run_results"
    m["nodes"][uid] = {"unique_id": uid, "name": "dbt_run_results", "resource_type": "model",
                       "package_name": "elementary", "path": "edr/dbt_run_results.sql",
                       "original_file_path": "models/edr/dbt_run_results.sql",
                       "description": "", "columns": {}, "config": {"materialized": "table"},
                       "depends_on": {"nodes": [], "macros": []}, "raw_code": ""}
    m["parent_map"][uid], m["child_map"][uid] = [], []
    (target / "manifest.json").write_text(json.dumps(m))


def test_a_package_model_is_not_one_of_yours_that_went_unread(project_dir):
    _add_package_model(project_dir)
    project, *_ = _load(project_dir, None)
    cov = project.coverage()
    assert cov["unreadable"] == 0
    assert cov["installed_unreadable"] == 1 and cov["installed_packages"] == {"elementary": 1}


def test_completeness_names_the_package_and_counts_none_of_yours(project_dir, tmp_path):
    _add_package_model(project_dir)
    r = CliRunner().invoke(app, ["completeness", "-t", str(project_dir), "--json",
                                 "--store", str(tmp_path / "s.duckdb")])
    doc = json.loads(r.stdout)
    assert doc["assay_can_read"]["not_audited"] == 0, doc["assay_can_read"]
    assert doc["assay_can_read"]["installed_packages_not_counted"] == {"elementary": 1}


def test_a_package_model_is_read_from_its_own_packages_compiled_directory(project_dir):
    """*** THE SQL WAS ONE DIRECTORY OVER. *** dbt compiles a package's models under
    `target/compiled/<package>/`; looking only under the root project's name left 30 of them
    unread on the field warehouse."""
    _add_package_model(project_dir)
    f = project_dir / "compiled" / "elementary" / "models" / "edr" / "dbt_run_results.sql"
    f.parent.mkdir(parents=True)
    f.write_text("select 1 as run_id")
    project, *_ = _load(project_dir, None)
    m = project.models["model.elementary.dbt_run_results"]
    assert m.readable and m.compiled_from == "disk"
    assert project.coverage()["installed_unreadable"] == 0
