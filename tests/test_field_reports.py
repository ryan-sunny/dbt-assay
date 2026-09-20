"""Three bugs reported from a real project, and the guards that keep them fixed.

None of them failed a test. Each one either took hours, destroyed evidence, or killed a command
outright, and the suite was green throughout.
"""
import json
from pathlib import Path

import pytest


def test_adjudicate_narrows_in_one_query_instead_of_one_per_test():
    """*** 1,288 TESTS x A COLD `dbt show` = FIVE AND A HALF HOURS. ***

    Reported from a project that turns store_failures on globally. Thirteen of those audit tables
    held a single row between them; every other call paid a dbt startup to be told a table was
    empty. A `union all` of counts answers it for hundreds of relations at once.
    """
    from dbt_assay import rows

    calls = []

    class _Probe:
        @staticmethod
        def run_sql(sql, *_a, **_k):
            calls.append(sql)
            return [{"rel": "db.aud.t_one", "n": 3}, {"rel": "db.aud.t_two", "n": 0}]

    rels = [f"db.aud.t_{i}" for i in range(400)] + ["db.aud.t_one", "db.aud.t_two"]
    have, unknown = rows.which_have_failures(rels, _Probe, ".", None, "dbt")
    assert have == {"db.aud.t_one"}
    assert unknown == []
    # 402 relations, batched at 200: three statements, not 402.
    assert len(calls) <= 4, len(calls)
    assert "union all" in calls[0]


def test_a_relation_it_cannot_read_is_unknown_and_never_counted_clean():
    """A batch that fails is halved until the culprit is alone, and that one is UNKNOWN. Silence
    has to be distinguishable from absence -- the rule this whole tool is built on."""
    from dbt_assay import rows

    class _Probe:
        @staticmethod
        def run_sql(sql, *_a, **_k):
            return [] if "bad" in sql else [{"rel": "ok", "n": 1}]

    have, unknown = rows.which_have_failures(["good.a", "bad.b"], _Probe, ".", None, "dbt")
    assert "bad.b" in unknown
    assert "bad.b" not in have


def test_compiling_does_not_destroy_which_tests_failed(tmp_path):
    """*** `dbt compile` OVERWRITES run_results.json. ***

    Reported from the field: after `assay onboard --compile`, run_results held one result and no
    test status, so anything downstream reading it saw a clean project. assay caused a check to
    stop seeing and report a pass.
    """
    from dbt_assay.cli import _run_dbt_compile

    (tmp_path / "dbt_project.yml").write_text("name: p\n")
    t = tmp_path / "target"
    t.mkdir()
    (t / "run_results.json").write_text(json.dumps({"results": [{"unique_id": "test.x",
                                                                 "status": "fail"}]}))
    _run_dbt_compile(t, dbt_bin="definitely-not-a-real-dbt")
    kept = t / "run_results.before-assay-compile.json"
    assert kept.exists(), "the previous run_results was not preserved"
    assert json.loads(kept.read_text())["results"][0]["status"] == "fail"


def test_the_review_loop_does_not_need_click_to_exist():
    """*** typer 0.27 STOPPED DEPENDING ON click. ***

    `import click` sat inside the one function that records verdicts, so the review loop -- the
    only path by which a question reaches its gate -- died with ModuleNotFoundError on a current
    typer. click is declared now, and this degrades to line input rather than dying.
    """
    import builtins
    import io
    import sys

    from dbt_assay import cli

    real_import = builtins.__import__

    def no_click(name, *a, **k):
        if name == "click":
            raise ImportError("no click here")
        return real_import(name, *a, **k)

    class _Tty(io.StringIO):
        def isatty(self):        # take the keypress branch, then lose click inside it
            return True

    builtins.__import__ = no_click
    old = sys.stdin
    sys.stdin = _Tty("a\n")
    try:
        assert cli._keypress() == "a", "a missing click must fall back, not raise"
    finally:
        builtins.__import__ = real_import
        sys.stdin = old


def test_click_is_a_declared_dependency():
    root = Path(__file__).parent.parent
    pj = root / "pyproject.toml"
    if not pj.exists():
        pytest.skip("installed as a wheel")
    assert "click>=" in pj.read_text(), "review -i needs click; it must not arrive by accident"


def test_a_family_nothing_asks_is_reported_rather_than_shown_as_coverage():
    """*** THREE CUSTOM FAMILIES LINTED CLEAN, LISTED AS `yours`, AND DID NOTHING. ***

    Every call site names a SHIPPED family by string literal; there is no generic runner. So a
    family with a NEW name is loaded, validated, displayed and inert -- and it looks exactly like
    coverage. Worse, the documented example used a new name.
    """
    from dbt_assay.contracts import SHIPPED
    from dbt_assay.lint import CALLERS, caller_of

    uncalled = [f for f in SHIPPED if caller_of(f) is None]
    assert not uncalled, f"shipped families nothing asks: {uncalled}"
    assert caller_of("a_family_nobody_wrote_a_call_site_for") is None
    for fam, (_mod, cmd, state) in CALLERS.items():
        assert fam in SHIPPED, f"{fam} is claimed to have a caller but is not a shipped family"
        assert cmd.startswith("assay "), fam
        assert state, fam


def test_the_caller_table_matches_the_source_that_asks():
    """A hand-kept table is a second copy of a fact, and the second copy is what drifts."""
    import importlib
    import inspect

    from dbt_assay.lint import CALLERS
    for fam, (mod, _cmd, _state) in CALLERS.items():
        src = inspect.getsource(importlib.import_module(f"dbt_assay.{mod}"))
        assert f'"{fam}"' in src, f"{mod}.py does not mention {fam}"


def test_the_documented_example_replaces_a_shipped_family():
    """The docs taught the broken pattern: their example used a NEW name, so anyone following
    them wrote a question that cannot run."""
    from pathlib import Path

    from dbt_assay.contracts import SHIPPED
    root = Path(__file__).parent.parent
    p = root / "docs" / "OVERVIEW.md"
    if not p.exists():
        pytest.skip("no docs in a wheel install")
    import re
    block = re.search(r"```yaml\n# assay_questions/(.*?)```", p.read_text(), re.DOTALL)
    assert block, "the worked example is gone"
    named = set(re.findall(r"^([a-z_]+):$", block.group(1), re.MULTILINE))
    assert named & set(SHIPPED), (
        f"the example defines {named}, none of which is a shipped family, so following it "
        f"produces a question nothing asks")


def test_a_parent_collapsed_before_the_join_is_seen():
    """*** 33% OF 543 HOPS READ `silently_multiplied`, AND THE TOP ONE WAS THIS. ***

    `join (select wdid, count(*) from int_water_diligence group by wdid) dl on dl.wdid = r.wdid`
    is already one row per key. Without this the state says "joins on wdid, no grouping" -- every
    word true, conclusion wrong. Measured: cannot_tell @0.24 became same_thing @0.41.
    """
    from dbt_assay.parse import digest

    sub = digest("select r.wdid, dl.n from water_rights r left join "
                 "(select wdid, count(*) as n from int_water_diligence group by wdid) dl "
                 "on dl.wdid = r.wdid", "m", "duckdb")
    assert sub.pre_aggregated == {"int_water_diligence": ["wdid"]}

    plain = digest("select r.wdid, d.note from water_rights r "
                   "join int_water_diligence d on d.wdid = r.wdid", "m", "duckdb")
    assert plain.pre_aggregated == {}


def test_a_top_level_group_by_is_not_mistaken_for_pre_aggregation():
    """That would mask a deliberate narrowing as "the parent was already collapsed"."""
    from dbt_assay.parse import digest
    assert digest("select wdid, count(*) n from water_rights group by wdid",
                  "m", "duckdb").pre_aggregated == {}


def test_a_missing_dbt_suggests_the_wrapper_this_project_actually_uses(tmp_path):
    """It failed with one quiet line, the run completed looking successful, and the models stayed
    unreadable. A uv or poetry project has no bare `dbt` on PATH, which is the common case."""
    from dbt_assay.cli import _run_dbt_compile

    (tmp_path / "dbt_project.yml").write_text("name: p\n")
    (tmp_path / "uv.lock").write_text("")
    t = tmp_path / "target"
    t.mkdir()
    _ok, why = _run_dbt_compile(t, dbt_bin="definitely-not-a-real-dbt")
    assert "uv run dbt" in why
