"""Functions a project registers outside its SQL, for an in-memory DuckDB run. (M4)

A dbt-duckdb project can register Python functions on every connection through a plugin
(`plugins: [{module: ...}]` in profiles.yml). SQL that calls one cannot run in a fresh DuckDB, so
the run check (`claimcheck`) read "could not run" for 42 claims on one project.

1. The profile's plugins, when they can be imported here: each one's `configure_connection` runs
   on the connection, as dbt-duckdb does. Importing one runs the project's own code, the code its
   dbt already runs on every build.
2. Any function the SQL calls that DuckDB still does not have: a STAND-IN, a macro returning its
   first argument. The claims run here (a key is unique, a join does not add rows) hold whatever a
   function computes, so a stand-in cannot make one hold that would not; every result that used
   one names it.

Nothing here reads a credential: only the output's `plugins:` list.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import sqlglot
from sqlglot import exp


def _yaml(path: Path) -> dict:
    import yaml
    try:
        with open(path) as fh:
            return yaml.safe_load(fh) or {}
    except Exception:                                            # noqa: BLE001
        return {}


def project_dir_for(target_dir, project_dir: str = ".") -> Path:
    """Where dbt_project.yml is: the one given, else the target directory's parent."""
    for d in (Path(project_dir or "."), Path(target_dir).resolve().parent):
        if (d / "dbt_project.yml").exists():
            return d.resolve()
    return Path(project_dir or ".").resolve()


def plugin_modules(project_dir, profiles_dir: str | None = None) -> list[str]:
    """The modules under `plugins:` in the profile's target output."""
    pdir = Path(project_dir)
    profile = (_yaml(pdir / "dbt_project.yml").get("profile") or "")
    for d in [Path(profiles_dir)] if profiles_dir else [
            pdir, Path(os.environ.get("DBT_PROFILES_DIR", "") or pdir), Path.home() / ".dbt"]:
        prof = _yaml(d / "profiles.yml").get(profile) or {}
        if not prof:
            continue
        outputs = prof.get("outputs") or {}
        out = outputs.get(prof.get("target")) or next(iter(outputs.values()), {}) or {}
        return [str(p.get("module")) for p in (out.get("plugins") or [])
                if isinstance(p, dict) and p.get("module")]
    return []


def load_plugins(con, modules: list[str], project_dir=None) -> list[str]:
    """Run each importable plugin's `configure_connection` on `con`. [what was loaded]."""
    loaded = []
    extra = [str(Path(project_dir).resolve()), str(Path(project_dir).resolve().parent)] \
        if project_dir else []
    for mod in modules:
        added = [p for p in extra if p not in sys.path]
        sys.path[:0] = added
        try:
            m = importlib.import_module(mod)
            cls = getattr(m, "Plugin", None)
            if cls is None:
                continue
            try:
                plugin = cls(mod, {})
            except TypeError:
                plugin = cls.__new__(cls)
            plugin.configure_connection(con)
            loaded.append(mod)
        except Exception:                                        # noqa: BLE001, S112
            continue
        finally:
            for p in added:
                if p in sys.path:
                    sys.path.remove(p)
    return loaded


def stand_ins(con, sql: str, dialect: str = "duckdb") -> list[str]:
    """A macro for each function `sql` calls that `con` does not have: it returns its first
    argument. [the names stood in for]."""
    from .parse import deep
    try:
        with deep():
            tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:                                            # noqa: BLE001
        return []
    arity: dict = {}
    for f in tree.find_all(exp.Anonymous):
        name = str(f.name).lower()
        arity[name] = max(arity.get(name, 0), len(f.expressions))
    if not arity:
        return []
    have = {r[0].lower() for r in con.execute(
        "select distinct function_name from duckdb_functions()").fetchall()}
    out = []
    for name, n in sorted(arity.items()):
        if name in have or not name.replace("_", "").isalnum() or n == 0:
            continue
        params = ", ".join(f"a{i}" for i in range(n))
        try:
            con.execute(f'create or replace macro "{name}"({params}) as a0')
            out.append(name)
        except Exception:                                        # noqa: BLE001, S112
            continue
    return out
