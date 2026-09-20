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


def test_the_canonical_compiled_copy_wins_and_a_conflict_is_recorded(project_dir, tmp_path):
    """Which compiled body you audit must not depend on directory sort order."""
    rel = "models/staging/stg_bad_notnull.sql"
    other = tmp_path / "target-run2" / "compiled" / "p" / rel
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("select id, coalesce(amount, other) as amount from raw.t")   # a DIFFERENT body

    p = Project.load(project_dir)
    m = p.models["model.p.stg_bad_notnull"]
    assert "coalesce(amount, 0)" in m.compiled            # the canonical copy, not the sibling
    assert m.compiled_conflicts                            # and the disagreement is visible
    assert p.coverage()["conflicting_copies"] == 1


def test_a_sibling_copy_is_used_only_when_the_canonical_one_is_absent(project_dir, tmp_path):
    rel = "models/staging/stg_ok_tilde.sql"
    (project_dir / "compiled" / "p" / rel).unlink()
    other = tmp_path / "target-run2" / "compiled" / "p" / rel
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("select id from raw.t where name ~ '.*X.*'")

    p = Project.load(project_dir)
    m = p.models["model.p.stg_ok_tilde"]
    assert m.readable and "run2" in (m.compiled_path or "")
    assert not m.compiled_conflicts
