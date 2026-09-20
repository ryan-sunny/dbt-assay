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
