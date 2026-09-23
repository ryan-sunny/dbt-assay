"""The edit gate: what runs after an agent writes a model, whether or not it read anything.

*** ADVISORY INSTRUCTIONS ARE FOLLOWED AT THE RATE AN AGENT CHOOSES. ***
Every piece of assay's enforcement lived in skill prose, and the field measured the rate: the review
skill's "drain the queue before you emit" was skipped and a person had to catch it. The one hook in
that repo, which blocks hand-rendered reports, works whether or not the agent read CLAUDE.md. This
is the same mechanism pointed at the models.

*** A POST-EDIT HOOK CANNOT UNDO THE EDIT, AND DOES NOT PRETEND TO. ***
By the time it fires the file is written. What it can do is exit 2, which hands the finding text back
to the agent as the reason it may not move on. So the edit stands on disk and the agent is stopped
until it fixes the SQL, or a person accepts or waives what it found.

*** IT COMPILES FIRST, BECAUSE assay READS COMPILED SQL. ***
Without a compile, saving a model changes nothing assay can see and the gate reports "nothing new"
whether or not there was -- the sentence `watch` learned the hard way. The compile uses the
project's own profile, so a project whose macros introspect the warehouse compiles against it rather
than to the empty-column SQL a disconnected compile produces.

*** ONLY WHAT THE EDIT INTRODUCED. ***
The baseline is the latest FULL `check` run in the store. A model with 30 findings nobody has
touched does not block every edit to it; a finding the edit added does, until it is gone.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

MATCHER = "Edit|Write|MultiEdit"
MARK = "assay hook post-edit"


def edited_path(payload: dict) -> str | None:
    """The file a Claude Code PostToolUse payload says was written, if any."""
    ti = payload.get("tool_input") or {}
    p = ti.get("file_path") or ti.get("path") or ti.get("notebook_path")
    return str(p) if p else None


def model_for(path: str, project_root: Path, project) -> str | None:
    """The model a written file is, by its path inside the dbt project. None if it is not one.

    Matched on the manifest's own `original_file_path`, never on the stem alone: two packages can
    both have a `stg_orders.sql`, and a macro or a seed is not a model even if it ends in .sql.
    """
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    try:
        rel = p.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return None
    for m in project.models.values():
        if m.path == rel:
            return m.name
    return None


def looks_like_a_model_file(path: str, project_root: Path) -> str | None:
    """A .sql file under the project's model paths that the manifest may not know yet.

    A NEW model is not in the manifest until something compiles it, so a `Write` that creates one
    would otherwise sail through as "not a model".
    """
    p = Path(path)
    if p.suffix != ".sql":
        return None
    try:
        rel = p.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return None
    roots = ["models"]
    try:
        import yaml
        doc = yaml.safe_load((project_root / "dbt_project.yml").read_text()) or {}
        roots = [str(r).strip("/") for r in (doc.get("model-paths") or doc.get("source-paths")
                                              or roots)]
    except Exception:                                            # noqa: BLE001, S110
        pass                                  # no readable dbt_project.yml: dbt's default, models/
    return p.stem if any(rel.startswith(r + "/") for r in roots) else None


def compile_model(name: str, project_root: Path, dbt_bin: str,
                  profiles_dir: str | None, timeout: float = 600) -> tuple[bool, str]:
    cmd = [*shlex.split(dbt_bin), "compile", "--select", name]
    if profiles_dir:
        cmd += ["--profiles-dir", profiles_dir]
    try:
        r = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True,
                           timeout=timeout, check=False)
    except FileNotFoundError:
        return False, f"could not run {dbt_bin!r}. Pass --dbt with the command that runs dbt here."
    except subprocess.TimeoutExpired:
        return False, f"`dbt compile --select {name}` took longer than {timeout:.0f}s."
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0, "\n".join(out.strip().splitlines()[-25:])


def scoped_check(name: str, target: str, store: str, config: str,
                 dialect: str | None = None) -> tuple[int, dict | None, str]:
    """`assay check --select <name> --new-only --json`, in a child process of this same assay."""
    cmd = [sys.executable, "-m", "dbt_assay.cli", "check", "--target", target, "--store", store,
           "--config", config, "--select", name, "--new-only", "--json"]
    if dialect:
        cmd += ["--dialect", dialect]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    out = r.stdout or ""
    at = out.find("\n{") + 1 if not out.startswith("{") else 0
    doc = None
    if out[at:].startswith("{"):
        try:
            doc = json.loads(out[at:])
        except ValueError:
            doc = None
    return r.returncode, doc, (out + (r.stderr or "")).strip()


def reason(model: str, doc: dict) -> str:
    """What the agent reads as the reason it may not move on."""
    fs = doc.get("findings") or []
    lines = [(f"assay: this edit to `{model}` introduced {len(fs)} finding(s) that the last "
              f"full `assay check` did not have.")]
    for f in fs[:12]:
        lines.append(f"- {f['check']}: {f['summary']}")
        if f.get("detail"):
            lines.append("  " + " ".join(str(f["detail"]).split())[:600])
    if len(fs) > 12:
        lines.append(f"...and {len(fs) - 12} more.")
    lines.append("")
    lines.append("Fix the SQL so these are gone. If a finding is correct and should stand, do not "
                 "work around it: tell the person, who can accept it (`assay review`) or waive it "
                 "in audit.yml. An agent's own verdict does not clear this gate.")
    return "\n".join(lines)


def settings_entry(command: str) -> dict:
    return {"matcher": MATCHER, "hooks": [{"type": "command", "command": command,
                                           "timeout": 900}]}


def hook_command(assay_cmd: str, target: str, store: str, config: str,
                 project_dir: str | None, dbt_bin: str, profiles_dir: str | None) -> str:
    parts = [assay_cmd, "hook", "post-edit", "--target", target, "--store", store,
             "--config", config, "--dbt", dbt_bin]
    if project_dir:
        parts += ["--project-dir", project_dir]
    if profiles_dir:
        parts += ["--profiles-dir", profiles_dir]
    # $CLAUDE_PROJECT_DIR, because a hook runs from wherever the session is and every path here
    # is relative to the directory the hook was installed from.
    quoted = " ".join(p if p == assay_cmd else shlex.quote(p) for p in parts)
    return 'cd "$CLAUDE_PROJECT_DIR" && ' + quoted


def install(settings_path: Path, command: str) -> str:
    """Merge the hook into a settings file. Returns what happened, in words.

    *** NEVER CLOBBER SOMEBODY'S SETTINGS. ***
    The file is theirs and may hold permissions and other hooks. An existing assay entry is
    replaced (so re-running onboard updates the flags); everything else is left byte-for-byte.
    """
    doc: dict = {}
    if settings_path.exists():
        try:
            doc = json.loads(settings_path.read_text() or "{}")
        except ValueError as e:
            raise ValueError(f"{settings_path} is not valid JSON ({e}); left alone. "
                             f"Add the hook by hand: {json.dumps(settings_entry(command))}") from e
    post = doc.setdefault("hooks", {}).setdefault("PostToolUse", [])
    kept, replaced = [], False
    for entry in post:
        hooks = [h for h in (entry.get("hooks") or []) if MARK not in str(h.get("command", ""))]
        if len(hooks) != len(entry.get("hooks") or []):
            replaced = True
        if hooks:
            kept.append({**entry, "hooks": hooks})
    kept.append(settings_entry(command))
    doc["hooks"]["PostToolUse"] = kept
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(doc, indent=2) + "\n")
    return "updated" if replaced else "added"
