from dbt_assay.manifest import Project


def test_reads_project_without_dbt_core(project_dir):
    p = Project.load(project_dir)
    assert p.project_name == "p"
    assert len(p.models) == len(
        [m for m in p.models if True]) and len(p.models) >= 8
    assert len(p.tests) == 5


def test_compiled_sql_is_found_on_disk(project_dir):
    p = Project.load(project_dir)
    cov = p.coverage()
    assert cov["readable"] == cov["models"]
    assert cov["from_disk"] == cov["models"]
    assert cov["unreadable"] == 0


def test_layer_comes_from_the_path(project_dir):
    p = Project.load(project_dir)
    assert p.models["model.p.stg_bad_notnull"].layer == "staging"
    assert p.models["model.p.int_bad_unique"].layer == "intermediate"


def test_graph_edges_and_blast_radius(project_dir):
    p = Project.load(project_dir)
    assert ("model.p.stg_bad_notnull", "model.p.int_bad_unique") in p.edges
    assert p.blast_radius("model.p.stg_bad_notnull")["descendants"] == 1
    assert p.blast_radius("model.p.int_bad_unique")["descendants"] == 0
    order = p.topological()
    assert order.index("model.p.stg_bad_notnull") < order.index("model.p.int_bad_unique")


def test_unreadable_models_are_counted_not_skipped(project_dir):
    """A model whose SQL cannot be read must never contribute to a passing result."""
    (project_dir / "compiled" / "p" / "models" / "staging" / "stg_bad_tilde.sql").unlink()
    p = Project.load(project_dir)
    assert p.coverage()["unreadable"] == 1
