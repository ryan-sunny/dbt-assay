"""Every CLI command as an MCP tool: the whole verb surface, not a curated subset.

*** MCP WAS A STRICT SUBSET, AND THE SKILL EXISTED TO COVER THE GAP WITH BASH. ***
21 tools, 19 of which read; 37 of 47 commands had no tool at all -- no `check`, no `ask`, no
`probe`, no `config`. An agent without a shell could read what a previous run stored and rule on it,
and could not run assay. The curated tools stay: they are shaped answers, and `contract` is fifteen
lines where the CLI prints a page. These sit beside them so that nothing the CLI can do is out of
reach of a tool.

*** ONE CODE PATH, NOT TWO. ***
Each tool runs the real command in a child process of the same assay, so a tool and its command can
never disagree -- the defect this codebase has found three times where MCP and the CLI computed the
same fact separately. The list is read off the app, so a command added tomorrow has a tool tomorrow.

*** A LONG COMMAND BECOMES A JOB INSTEAD OF A TIMEOUT. ***
`probe` can run ten minutes and `watch` never ends, and an MCP call that blocks that long is killed
by the client. Every tool waits up to `wait_seconds`; a run still going then is handed back as a job
id, and `job_status` returns its progress and, when it ends, its whole output. Nothing is special-
cased by name, because which commands are slow depends on the warehouse, not the command.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# A command that waits for a person at a keyboard has nothing to wait for over MCP.
INTERACTIVE = {"review": ("-i", "--interactive")}
MAX_OUTPUT = 60_000


def commands() -> list[dict]:
    """[{name, args, first, flags}] for every registered command, read off the app."""
    import typer.main

    from .cli import app
    out = []
    for c in app.registered_commands:
        name = c.name or c.callback.__name__.removesuffix("_cmd").replace("_", "-")
        doc = (c.help or c.callback.__doc__ or "").strip()
        first = doc.split("\n\n")[0].replace("\n", " ").strip()
        if "." in first:
            first = first[:first.index(".") + 1]
        params = typer.main.get_params_convertors_ctx_param_name_from_function(c.callback)[0]
        args, flags = [], []
        for p in params:
            got = [o for o in getattr(p, "opts", []) if o.startswith("-")]
            if not got:
                args.append(f"<{p.name}>")
                continue
            # A hidden option is accepted and not taught: it exists so a habit does not error.
            if "--help" in got or getattr(p, "hidden", False):
                continue
            flags.append("/".join(sorted(got, key=lambda o: (not o.startswith("--"), o))))
            flags += [o for o in getattr(p, "secondary_opts", []) if o.startswith("--")]
        out.append({"name": name, "args": args, "first": first, "flags": flags})
    return sorted(out, key=lambda r: r["name"])


def tool_name(command: str) -> str:
    return "assay_" + command.replace("-", "_")


def description(cmd: dict) -> str:
    sig = " ".join(cmd["args"])
    return (f"`assay {cmd['name']}{(' ' + sig) if sig else ''}` -- {cmd['first']} "
            f"Pass flags as one string, exactly as on the command line, e.g. "
            f"args=\"--json\". Flags: {' '.join(cmd['flags']) or 'none'}. "
            f"--target and --store default to this server's. A run longer than wait_seconds "
            f"comes back as a job id: call job_status.")


@dataclass
class Job:
    id: str
    argv: list
    log: Path
    proc: subprocess.Popen
    started: float = field(default_factory=time.time)


_JOBS: dict[str, Job] = {}


def _read(log: Path) -> tuple[str, bool]:
    try:
        text = log.read_text(errors="replace")
    except OSError:
        return "", False
    if len(text) > MAX_OUTPUT:
        return "...(start cut)\n" + text[-MAX_OUTPUT:], True
    return text, False


def _result(job: Job, code: int | None) -> dict:
    text, cut = _read(job.log)
    out: dict = {"command": " ".join(job.argv[3:]), "exit_code": code,
                 "seconds": round(time.time() - job.started, 1)}
    stripped = text.strip()
    at = stripped.find("{")
    if at >= 0 and stripped.endswith("}"):
        try:
            out["json"] = json.loads(stripped[at:])
            if at:
                out["said_first"] = stripped[:at].strip()[-2000:]
            return out
        except ValueError:
            pass
    out["output"] = text
    if cut:
        out["truncated"] = f"the first part was cut; the log is {job.log}"
    return out


def run(command: str, args: str = "", target: str | None = None, store: str | None = None,
        wait_seconds: float = 90, accepts: set | None = None) -> dict:
    """Run one command. Its result, or a job id when it outlasts `wait_seconds`."""
    try:
        argv = shlex.split(args or "")
    except ValueError as e:
        return {"error": f"could not read args: {e}"}
    if any(a in INTERACTIVE.get(command, ()) for a in argv):
        return {"error": (f"`assay {command} {INTERACTIVE[command][0]}` waits for "
                          f"keypresses, and a tool has no keyboard. Use `review --emit` for a "
                          f"form a person fills in, or the `rule` tool to record one verdict.")}
    have = {a.split("=")[0] for a in argv if a.startswith("-")}
    accepts = accepts or set()
    if target and "--target" in accepts and not have & {"--target", "-t"}:
        argv += ["--target", target]
    if store and "--store" in accepts and "--store" not in have:
        argv += ["--store", store]
    full = [sys.executable, "-m", "dbt_assay.cli", command, *argv]
    log = Path(tempfile.gettempdir()) / f"assay-job-{uuid.uuid4().hex[:10]}.log"
    env = {**os.environ, "COLUMNS": "200", "TERM": "dumb", "NO_COLOR": "1"}
    fh = log.open("w")
    proc = subprocess.Popen(full, stdout=fh, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=env)
    job = Job(uuid.uuid4().hex[:10], full, log, proc)
    try:
        code = proc.wait(timeout=max(0.0, float(wait_seconds)))
    except subprocess.TimeoutExpired:
        _JOBS[job.id] = job
        text, _cut = _read(log)
        return {"job": job.id, "status": "running", "command": " ".join(full[3:]),
                "so_far": text[-1500:],
                "next": f"job_status(job=\"{job.id}\") until it finishes; job_stop to end it."}
    finally:
        fh.close()
    return _result(job, code)


def status(job_id: str) -> dict:
    job = _JOBS.get(job_id)
    if job is None:
        return {"error": f"no job {job_id!r}. `jobs` lists the ones this server started."}
    code = job.proc.poll()
    if code is None:
        text, _cut = _read(job.log)
        return {"job": job_id, "status": "running",
                "seconds": round(time.time() - job.started, 1), "so_far": text[-3000:]}
    out = _result(job, code)
    out.update({"job": job_id, "status": "finished"})
    return out


def stop(job_id: str) -> dict:
    job = _JOBS.get(job_id)
    if job is None:
        return {"error": f"no job {job_id!r}"}
    if job.proc.poll() is None:
        job.proc.terminate()
        try:
            job.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            job.proc.kill()
    out = _result(job, job.proc.poll())
    out.update({"job": job_id, "status": "stopped"})
    return out


def listing() -> dict:
    return {"jobs": [{"job": j.id, "command": " ".join(j.argv[3:]),
                      "status": "running" if j.proc.poll() is None else "finished",
                      "seconds": round(time.time() - j.started, 1)} for j in _JOBS.values()]}
