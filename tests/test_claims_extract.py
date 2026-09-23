"""`claims --extract` and `verify` on a project that writes a claim twice.

*** THE BETTER A PROJECT'S PROSE, THE MORE LIKELY IT COULD NOT EXTRACT ITS CLAIMS. ***
Reported from the field (25.19): the merged-sentence report bound `_n` as a loop variable, which
made the module's number formatter a local string for the rest of `claims()`. A project with no
duplicated claim prose never entered the loop and worked; one with 58 crashed -- after the store
write, so the claims were saved, `--write` never ran, and the traceback said nothing happened.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from fakejev import install
from typer.testing import CliRunner

from dbt_assay.cli import app

runner = CliRunner()

SENTENCE = "Every row is one permit and permit_id is never null."


def _project(tmp_path: Path) -> Path:
    """One model whose description and SQL comment carry the same claim."""
    sql = f"-- {SENTENCE}\nselect permit_id, issued_date from raw.permits\n"
    uid = "model.p.stg_permits"
    path = "models/staging/stg_permits.sql"
    f = tmp_path / "target" / "compiled" / "p" / path
    f.parent.mkdir(parents=True)
    f.write_text(sql)
    node = {"unique_id": uid, "name": "stg_permits", "resource_type": "model",
            "package_name": "p", "original_file_path": path, "path": path,
            # a backtick apart: the near-duplicate the field report merged 58 of
            "description": SENTENCE.replace("permit_id", "`permit_id`"), "columns": {}, "config": {"materialized": "table"},
            "depends_on": {"nodes": [], "macros": []}, "raw_code": sql,
            "checksum": {"name": "sha256", "checksum": "x"}}
    (tmp_path / "target" / "manifest.json").write_text(json.dumps({
        "metadata": {"project_name": "p", "dbt_version": "1.11.0", "adapter_type": "duckdb"},
        "nodes": {uid: node}, "sources": {}, "parent_map": {uid: []}, "child_map": {uid: []}}))
    return tmp_path / "target"


def test_extract_survives_a_claim_written_twice_and_writes_the_file_it_was_asked_for(
        tmp_path, monkeypatch):
    target = _project(tmp_path)
    install(monkeypatch, pick=lambda _qid, q: "claim_about_output"
            if "claim_about_output" in (q.get("criteria") or {}) else None)
    out = tmp_path / "claims.yml"
    r = runner.invoke(app, ["claims", "--extract", "-t", str(target),
                            "--store", str(tmp_path / "a.duckdb"), "--config", str(tmp_path),
                            "--write", str(out)])
    assert r.exit_code == 0, r.output + repr(r.exception)
    assert "merged as the same claim written twice" in r.output
    assert out.exists(), "--extract --write must write the file it was asked for"
    assert "is never null" in out.read_text()


def test_a_second_extract_with_nothing_new_still_writes_the_file(tmp_path, monkeypatch):
    target = _project(tmp_path)
    install(monkeypatch, pick=lambda _qid, q: "claim_about_output"
            if "claim_about_output" in (q.get("criteria") or {}) else None)
    args = ["claims", "--extract", "-t", str(target), "--store", str(tmp_path / "a.duckdb"),
            "--config", str(tmp_path)]
    assert runner.invoke(app, args).exit_code == 0
    out = tmp_path / "again.yml"
    r = runner.invoke(app, [*args, "--write", str(out)])
    assert r.exit_code == 0, r.output
    assert out.exists()


def _module_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """(functions, imports) defined at module level.

    An import counts too: `live = ...` inside a function that also reads `live.findings_for` is
    the same crash with a module in place of a helper, and it was found in `suggestions`.
    """
    fns = {n.name for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
    mods = set()
    for n in tree.body:
        if isinstance(n, ast.Import | ast.ImportFrom):
            mods |= {(a.asname or a.name).split(".")[0] for a in n.names}
    return fns, mods


def _bound_in(fn: ast.FunctionDef) -> dict[str, int]:
    """Every name a function binds by assignment, loop, `with` or comprehension target."""
    out: dict[str, int] = {}
    for node in ast.walk(fn):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign | ast.AugAssign | ast.For | ast.AsyncFor):
            targets = [node.target]
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            targets = [node.optional_vars]
        for t in targets:
            for n in ast.walk(t):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    out.setdefault(n.id, node.lineno if hasattr(node, "lineno") else 0)
    return out


def test_no_function_rebinds_a_module_level_function_name():
    """*** THE SECOND SHADOWING DEFECT IN ONE FIELD REPORT. ***

    Python scope is per function, so a loop variable named like a module helper turns every later
    call to that helper into a call on whatever the loop left behind -- only when the loop runs.
    Checked over the whole package, because the crash only fires on data that enters the loop.
    """
    root = Path(__file__).parent.parent / "src" / "dbt_assay"
    bad = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text())
        fns, mods = _module_names(tree)
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            called = {n.func.id for n in ast.walk(fn)
                      if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
            dotted = {n.value.id for n in ast.walk(fn)
                      if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
            for name, line in _bound_in(fn).items():
                # Rebinding alone is harmless; rebinding a helper the same function also CALLS,
                # or a module it also reads from, is the crash -- and it only fires on the data
                # that reaches the binding.
                if name == fn.name:
                    continue
                if (name in fns and name in called) or (name in mods and name in dotted):
                    bad.append(f"{path.name}:{line} {fn.name}() rebinds `{name}` and uses it")
    assert not bad, "\n".join(bad)
