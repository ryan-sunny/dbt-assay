import pytest

from dbt_assay.manifest import Project
from dbt_assay.selector import SelectorError, resolve, validate


def test_syntax_assay_cannot_honour_is_an_error_not_a_silent_match_all():
    """A selector that is silently ignored scopes nothing while looking as though it did."""
    for bad in ("fqn:a.b", "source:raw.x", "exposure:dash", "test_type:generic"):
        with pytest.raises(SelectorError):
            validate(bad)


def test_the_supported_forms_validate():
    validate("path:models/water tag:finance config.materialized:table my_model +a b+")


def test_none_means_everything_and_empty_means_nothing(project_dir):
    p = Project.load(project_dir)
    assert resolve(p, None) is None
    assert resolve(p, "no_such_model") == set()


def test_a_path_prefix_selects_a_subtree(project_dir):
    p = Project.load(project_dir)
    got = resolve(p, "path:models/staging")
    assert got
    assert all(p.models[u].path.startswith("models/staging") for u in got)


def test_a_bare_name_selects_exactly_that_model(project_dir):
    p = Project.load(project_dir)
    assert resolve(p, "int_bad_unique") == {"model.p.int_bad_unique"}


def test_the_graph_operators_walk_the_dag(project_dir):
    p = Project.load(project_dir)
    down = resolve(p, "stg_bad_notnull+")
    assert "model.p.int_bad_unique" in down
    up = resolve(p, "+int_bad_unique")
    assert "model.p.stg_bad_notnull" in up


def test_a_union_is_space_separated(project_dir):
    p = Project.load(project_dir)
    both = resolve(p, "int_bad_unique int_ok_unique")
    assert both == {"model.p.int_bad_unique", "model.p.int_ok_unique"}


def test_a_selector_in_audit_yml_is_validated_at_load_time(project_dir):
    from dbt_assay.config import Config
    with pytest.raises(SelectorError):
        Config.from_dict({"questions": {"q": {"when": {"select": "fqn:a.b"}}}})
