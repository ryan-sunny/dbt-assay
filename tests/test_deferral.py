"""A deferral nobody is told about is a check that stopped looking.

*** MEASURED ON THE FIELD WAREHOUSE. ***
`source_freshness_undeclared` goes silent when dbt-project-evaluator is INSTALLED, on the argument
that printing the same finding twice is worse than printing it once. The argument is right. The
test was wrong: `installed()` reads the MANIFEST, and a package being in the manifest is not its
models being BUILT.

The evaluator was installed, `fct_sources_without_freshness` was NOT built -- 4 of its tables
existed -- and sources declared no freshness. assay was silent because somebody else was covering
it, that somebody said nothing, and nobody was told.

`practices.py` already learned this for its own READS: "an absent table and an empty one are not
the same fact, and only one of them is a pass." It had not learned it for DEFERRALS.
"""

import pytest

from dbt_assay.checks import sources as src_mod


def _project(with_evaluator: bool, n_sources: int = 3, freshness: bool = False):
    from types import SimpleNamespace
    nodes = {"model.p.a": {"package_name": "p", "resource_type": "model"}}
    if with_evaluator:
        nodes["model.dbt_project_evaluator.fct_x"] = {
            "package_name": "dbt_project_evaluator", "resource_type": "model"}
    raw_sources = {}
    for i in range(n_sources):
        raw_sources[f"source.p.s{i}"] = (
            {"freshness": {"warn_after": {"count": 1, "period": "day"}}} if freshness else {})
    return SimpleNamespace(
        raw={"nodes": nodes, "sources": raw_sources},
        sources={k: SimpleNamespace(unique_id=k, name=k.split(".")[-1], source_name="raw",
                                    schema="s", description="", columns=[], children=[])
                 for k in raw_sources})


@pytest.fixture(autouse=True)
def _clear():
    src_mod.DEFERRED.clear()
    yield
    src_mod.DEFERRED.clear()


def test_deferring_to_a_package_is_announced():
    """Silence plus somebody else's silence is nobody checking."""
    src_mod.source_freshness_undeclared(_project(with_evaluator=True))
    assert src_mod.DEFERRED, "assay went quiet and said nothing about going quiet"
    name, why = src_mod.DEFERRED[0]
    assert name == "source_freshness_undeclared"
    assert "3 of 3" in why, why
    assert "BUILT" in why, "it must say the deferral rests on that model being built"


def test_nothing_is_announced_when_there_is_nothing_to_defer():
    """A project whose sources all declare freshness has no silence to explain."""
    src_mod.source_freshness_undeclared(_project(with_evaluator=True, freshness=True))
    assert not src_mod.DEFERRED


def test_without_the_package_it_reports_rather_than_defers():
    """assay covers the case where nobody else does -- that is the whole point of deferring."""
    got = src_mod.source_freshness_undeclared(_project(with_evaluator=False))
    assert got, "no evaluator and no findings: nobody is checking freshness at all"
    assert not src_mod.DEFERRED


def test_installed_answers_declared_not_built():
    """The docstring must not claim more than the manifest can say.

    This is the distinction the bug turned on, so it is pinned rather than left to be re-learned.
    """
    p = _project(with_evaluator=True)
    assert src_mod.installed(p, "dbt_project_evaluator")
    assert "built" in src_mod.installed.__doc__.lower(), \
        "the docstring must warn that this is not 'its models are built'"
