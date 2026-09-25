"""*** AN INSTALLED PACKAGE'S MODELS ARE NOT THIS PROJECT'S TO JUDGE. *** (sunny-data box) A warm
run re-sent 45 judged requests per family and 37 edges, one per dbt_project_evaluator model, the
day its build first succeeded: paid calls about code the project does not own."""
import json

from dbt_assay import subjects as subjects_mod
from dbt_assay.cli import _load


def test_a_package_model_is_never_a_judged_subject(project_dir):
    mf = project_dir / "manifest.json"
    m = json.loads(mf.read_text())
    m["nodes"]["model.p.int_bad_unique"]["package_name"] = "some_package"
    for n in m["nodes"].values():
        n.setdefault("package_name", "p")
    mf.write_text(json.dumps(m))
    project, digests, _f, schema, _s = _load(project_dir)
    assert project.models["model.p.int_bad_unique"].is_installed_package
    src = subjects_mod.SubjectSource(project, digests, schema, None)
    for kind in ("model", "column", "expression"):
        uids = {s.uid for s in subjects_mod.build(kind, src)}
        assert "model.p.int_bad_unique" not in uids, kind
    assert "model.p.stg_bad_notnull" in {s.uid for s in subjects_mod.build("model", src)}
