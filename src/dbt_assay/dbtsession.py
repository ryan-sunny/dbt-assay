"""One dbt for a whole command: the project's own dbt, started once, answering every `show`.

*** TWELVE CALLS AT 16 TO 19 SECONDS, FOR QUERIES THAT TAKE MILLISECONDS. *** (sunny-data box,
RC 62c18a6) A warm daily run spent most of its time starting dbt: import, manifest load, adapter
and plugin load, once per statement. Batching and a private target folder cut the number of
calls and the parse; neither removed the start.

*** AND assay STILL DOES NOT DEPEND ON dbt-core, OR HOLD A CREDENTIAL. *** `dbtRunner` would parse
once and invoke many times, but in assay's process it would pin assay to one dbt and its
adapters. So the worker below runs in the PROJECT's dbt environment (the interpreter behind the
`--dbt` command), holds the parsed manifest, and answers `show --inline` requests over a pipe.
dbt still reads profiles.yml; assay still sees rows and nothing else.

Measured on jaffle_shop with dbt-duckdb 1.11: a `dbt show` subprocess 6.2s; the same statement
through a held `dbtRunner(manifest=...)`, 0.22s after the first.

Anything the session cannot do goes back to one subprocess per statement, as before: an
interpreter it cannot find, a worker that will not start or dies, `cost.measure_bytes` (which
needs dbt's debug log). The fallback is said once, never silent.
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import threading
import time

# Runs inside the project's dbt interpreter. Reads one JSON request per line on stdin, writes one
# JSON reply per line on the fd it was given; dbt's own printing goes to /dev/null, and its
# stderr to a file the parent reads only on failure.
WORKER = r'''
import io, json, os, sys
out = os.fdopen(os.dup(1), "w", buffering=1)
null = os.open(os.devnull, os.O_WRONLY)
os.dup2(null, 1)
def reply(d):
    out.write(json.dumps(d, default=str) + "\n"); out.flush()
try:
    from dbt.cli.main import dbtRunner
except Exception as e:
    reply({"ready": False, "why": "dbt is not importable here: %s" % e}); sys.exit(0)
common = json.loads(sys.argv[1])
r = dbtRunner().invoke(["parse", "--quiet", *common])
if not r.success or r.result is None:
    reply({"ready": False, "why": "dbt parse failed: %s" % (r.exception or "")}); sys.exit(0)
manifest = r.result
parsed = set(manifest.nodes)
runner = dbtRunner(manifest=manifest)
reply({"ready": True})
for line in sys.stdin:
    q = json.loads(line)
    # A `show --inline` that fails leaves its node in the held manifest, and every later one
    # then fails to parse ("Error parsing inline query"). Back to the parsed nodes each time.
    for k in [k for k in manifest.nodes if k not in parsed]:
        del manifest.nodes[k]
    try:
        res = runner.invoke(["show", "--inline", q["sql"], "--output", "json", "--limit",
                             str(q["limit"]), "--quiet", *common])
        if not res.success or not res.result or not res.result.results:
            why = str(res.exception or "")
            for x in (res.result.results if res.result else []):
                why = why or str(getattr(x, "message", "") or "")
            reply({"ok": False, "why": why or "dbt show failed"}); continue
        x = res.result.results[0]
        buf = io.StringIO()
        x.agate_table.to_json(path=buf)
        reply({"ok": True, "show": json.loads(buf.getvalue() or "[]"),
               "adapter": dict(getattr(x, "adapter_response", None) or {}),
               "secs": getattr(x, "execution_time", None)})
    except Exception as e:
        reply({"ok": False, "why": "%s: %s" % (type(e).__name__, e)})
'''

START_TIMEOUT = 600          # the first parse of a large project on a cold box
_SESSIONS: dict = {}
_DEAD: dict = {}             # key -> why it could not be used, said once
_LOCK = threading.Lock()


def interpreter(dbt_bin: str) -> list[str] | None:
    """The command that starts a Python where the `--dbt` command's dbt is importable.

    `uv run dbt` -> `uv run python`; a path to dbt, or bare `dbt` on PATH -> the interpreter on
    its first line. None when neither can be read, and the caller keeps its subprocess."""
    words = (dbt_bin or "dbt").split()
    if not words or not os.path.basename(words[-1]).startswith("dbt"):
        return None
    if len(words) > 1:
        return [*words[:-1], "python"]
    exe = shutil.which(words[0])
    if not exe:
        return None
    try:
        with open(exe, "rb") as fh:
            first = fh.readline().decode(errors="replace").strip()
    except OSError:
        return None
    def py(p: str) -> bool:
        return os.path.basename(p).startswith("python")

    if first.startswith("#!"):
        parts = first[2:].split()
        if parts and os.path.basename(parts[0]) == "env" and len(parts) > 1 and py(parts[1]):
            sib = os.path.join(os.path.dirname(exe), parts[1])
            return [sib] if os.path.exists(sib) else [parts[1]]
        if parts and py(parts[0]) and os.path.exists(parts[0]):
            return [parts[0]]
        if parts and not py(parts[-1]):
            return None                     # a shell script wrapping dbt: keep the subprocess
    sib = os.path.join(os.path.dirname(exe), "python")
    return [sib] if os.path.exists(sib) else None


class Session:
    def __init__(self, cmd: list[str], common: list[str], cwd: str):
        import tempfile
        # open for the worker's whole life, closed in close()
        self.err = tempfile.TemporaryFile(mode="w+")  # noqa: SIM115
        self.proc = subprocess.Popen([*cmd, "-c", WORKER, json.dumps(common)], cwd=cwd,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self.err, text=True, bufsize=1)
        self.lock = threading.Lock()
        self.started = time.monotonic()
        hello = self._read(START_TIMEOUT)
        if not hello or not hello.get("ready"):
            why = (hello or {}).get("why") or self._stderr() or "the dbt worker did not start"
            self.close()
            raise RuntimeError(why)
        self.ready_ms = int((time.monotonic() - self.started) * 1000)

    def _stderr(self) -> str:
        try:
            self.err.seek(0)
            return self.err.read()[-600:]
        except Exception:                                        # noqa: BLE001
            return ""

    def _read(self, timeout: float) -> dict | None:
        box: list = []
        t = threading.Thread(target=lambda: box.append(self.proc.stdout.readline()), daemon=True)
        t.start()
        t.join(timeout)
        if not box or not box[0]:
            return None
        try:
            return json.loads(box[0])
        except json.JSONDecodeError:
            return None

    def show(self, sql: str, limit: int, timeout: float) -> dict:
        with self.lock:
            if self.proc.poll() is not None:
                raise RuntimeError("the dbt worker exited: " + self._stderr())
            self.proc.stdin.write(json.dumps({"sql": sql, "limit": limit}) + "\n")
            self.proc.stdin.flush()
            got = self._read(timeout)
            if got is None:
                self.close()
                raise RuntimeError(f"the dbt worker gave no answer in {int(timeout)}s")
            return got

    def close(self) -> None:
        try:
            if self.proc.poll() is None:
                self.proc.stdin.close()
                self.proc.wait(5)
        except Exception:                                        # noqa: BLE001
            self.proc.kill()
        try:
            self.err.close()
        except Exception:                                        # noqa: BLE001,S110
            pass


def enabled() -> bool:
    return str(os.environ.get("ASSAY_DBT_SESSION", "1")).lower() not in ("0", "false", "no")


def get(project_dir: str, dbt_bin: str, common: list[str]) -> Session | None:
    """The command's one dbt for this project, started on first use; None means use a
    subprocess per statement (and why is in `why_not`)."""
    if not enabled():
        return None
    key = (os.path.abspath(project_dir or "."), dbt_bin, tuple(common))
    with _LOCK:
        if key in _DEAD:
            return None
        s = _SESSIONS.get(key)
        if s is not None and s.proc.poll() is None:
            return s
        cmd = interpreter(dbt_bin)
        if cmd is None:
            _DEAD[key] = f"no Python found behind `{dbt_bin}`"
            return None
        try:
            s = Session(cmd, common, project_dir or ".")
        except Exception as e:                                   # noqa: BLE001
            _DEAD[key] = str(e)[:400]
            return None
        _SESSIONS[key] = s
        return s


def why_not(project_dir: str, dbt_bin: str, common: list[str]) -> str:
    return _DEAD.get((os.path.abspath(project_dir or "."), dbt_bin, tuple(common)), "")


def forget(project_dir: str, dbt_bin: str, common: list[str], why: str) -> None:
    key = (os.path.abspath(project_dir or "."), dbt_bin, tuple(common))
    with _LOCK:
        s = _SESSIONS.pop(key, None)
        if s:
            s.close()
        _DEAD[key] = why[:400]


@atexit.register
def close_all() -> None:
    for s in list(_SESSIONS.values()):
        s.close()
    _SESSIONS.clear()
