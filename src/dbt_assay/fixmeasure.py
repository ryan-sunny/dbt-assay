"""How many findings a fix ACTUALLY resolves: apply it to a copy of the project and check again.

*** "RESOLVES 212" HAS TO BE A COUNT, NOT A HOPE. *** (leverage spec, feasibility review) The
attribution in fixes.py is an estimate: a finding is put against the fix that looks like it should
resolve it. Here the fix's files are written into a scratch copy, dbt parses it (no warehouse:
parse does not connect), and assay reads the copy the way it reads the project. A finding is
resolved when it is gone from the copy.

Checks the copy cannot recompute offline (dbt-project-evaluator's rows are built in the warehouse)
are recomputed from the patched manifest for the rules a fix can move: a model reading a raw
source, a source read by several models, a model with no key test, a model with no description.
A finding neither way can re-check is left out of the count and said so, never counted as
resolved.

The copy is thrown away. Nothing here writes to the project or the warehouse.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

SKIP = ("target", "logs", ".venv", ".git", "__pycache__", "node_modules")


def _copy(root: Path, dst: Path, target_dir: Path) -> None:
    shutil.copytree(root, dst, ignore=shutil.ignore_patterns(*SKIP), symlinks=True,
                    dirs_exist_ok=True)
    t = dst / "target"
    t.mkdir(exist_ok=True)
    for name in ("manifest.json", "partial_parse.msgpack", "catalog.json"):
        if (target_dir / name).exists():
            shutil.copy2(target_dir / name, t / name)
    if (target_dir / "compiled").exists():
        shutil.copytree(target_dir / "compiled", t / "compiled", dirs_exist_ok=True)


def _compiled_for_stage(fx, project, scratch: Path, patched) -> None:
    """A stage fix changes SQL, and parse compiles nothing: the new pass-through model's compiled
    text is `select * from <the source>`, and each reader's is its old compiled text with the
    source relation replaced by the new model's."""
    src = project.sources.get(fx.key)
    if src is None:
        return
    node = (project.raw.get("sources", {}) or {}).get(fx.key) or {}
    rel = node.get("relation_name") or f"{src.schema}.{src.name}"
    comp_root = scratch / "target" / "compiled" / project.project_name
    for path in fx.new_files:
        if path.endswith(".sql"):
            (comp_root / path).parent.mkdir(parents=True, exist_ok=True)
            (comp_root / path).write_text(f"select * from {rel}\n")
    stg_name = Path(next((p for p in fx.new_files if p.endswith(".sql")), "")).stem or ""
    stg_uid = next((u for u, m in patched.models.items() if m.name == stg_name), None)
    stg_rel = ((patched.raw.get("nodes", {}) or {}).get(stg_uid) or {}).get("relation_name") \
        if stg_uid else None
    if not stg_rel:
        return
    for name in fx.models:
        m = next((x for x in project.models.values() if x.name == name), None)
        if m is None or not m.compiled_path or not Path(m.compiled_path).exists():
            continue
        out = comp_root / m.path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(Path(m.compiled_path).read_text().replace(rel, stg_rel))


def _simulated_gone(f, patched) -> bool | None:
    """For the evaluator's rules a fix can move: True/False from the patched manifest, None when
    this is not one of them."""
    ch, uid = f.check, f.subject
    if ch == "reads_raw_source_outside_staging":
        m = patched.models.get(uid)
        return m is not None and not any(p in patched.sources for p in m.parents)
    if ch == "source_read_directly_by_many_models":
        n = sum(1 for m in patched.models.values()
                if uid in m.parents and not m.name.startswith(("stg_", "base_")))
        return n <= 1
    if ch == "no_primary_key_test":
        return any(t.tests_model == uid for t in patched.tests
                   if (t.kind or "") in ("unique", "unique_combination_of_columns", "")
                   or "unique" in (t.name or ""))
    if ch == "model_has_no_description":
        m = patched.models.get(uid)
        return m is not None and bool((m.description or "").strip())
    return None


def offline(target_dir: Path, dialect: str | None = None) -> list:
    """The findings assay produces from the project files alone (no store): what a patched copy
    can be compared against like for like."""
    from . import live
    from .cli import _load
    project, digests, _f, schema, _s = _load(Path(target_dir), dialect)
    return live.all_findings(project, digests, schema, None, store=None)


def measure(fx, project, findings, *, target_dir: Path, before: list, dbt_bin: str = "dbt",
            profiles_dir: str | None = None, dialect: str | None = None) -> None:
    """Set `fx.measured` (resolved on the copy) and `fx.measured_note`. `before` is `offline()`
    of the unpatched project."""
    if not fx.files:
        fx.measured_note = "nothing to apply: this fix is a change outside the project files"
        return
    from . import live, probe
    from .cli import _load
    from .manifest import Project
    root = Path(project.project_root)
    mine = [f for f in findings if f.id in set(fx.findings)]
    b_ids = {f.id for f in before}
    b_keys = {(f.check, f.subject) for f in before}
    with tempfile.TemporaryDirectory(prefix="assay-fix-") as tmp:
        scratch = Path(tmp) / "project"
        _copy(root, scratch, Path(target_dir))
        for path, text in fx.files.items():
            p = scratch / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        cmd = [*dbt_bin.split(), "parse", *probe.profiles_args(profiles_dir)]
        try:
            r = subprocess.run(cmd, cwd=scratch, capture_output=True, text=True, timeout=900,
                               check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            fx.measured_note = f"could not parse the patched copy: {str(e)[:160]}"
            return
        if r.returncode != 0:
            fx.measured_note = ("the patched copy does not parse, so this fix is not ready: "
                                + probe.failure_text(r, 300))
            return
        if fx.kind == "stage_raw_source":
            _compiled_for_stage(fx, project, scratch, Project.load(scratch / "target", scratch))
        project2, digests2, _f, schema2, _s = _load(scratch / "target", dialect)
        after = live.all_findings(project2, digests2, schema2, None, store=None)
    rows = [(f.id, f.check, f.subject) for f in after]
    gone, unsure = 0, 0
    measurable = []
    for f in mine:
        sim = _simulated_gone(f, project2)
        if sim is not None:
            gone += bool(sim)
        elif f.id in b_ids or (f.check, f.subject) in b_keys:
            measurable.append(f)
        else:
            unsure += 1          # judged, or read from the warehouse: not reproducible offline
    gone += len(live.new_findings(measurable, rows))       # the ones the copy no longer has
    fx.measured = gone
    fx.measured_note = (f"measured on a patched copy: {gone} of {len(mine)} gone"
                        + (f"; {unsure} could not be re-checked offline (judged or read from the "
                           f"warehouse) and are not counted" if unsure else ""))


def measure_top(fixes: list, project, findings, *, n: int, target_dir: Path,
                dialect: str | None = None, **kw) -> int:
    todo = [fx for fx in fixes if fx.files][:n]
    if not todo:
        return 0
    before = offline(target_dir, dialect)
    for fx in todo:
        measure(fx, project, findings, target_dir=target_dir, before=before, dialect=dialect,
                **kw)
    return len(todo)
