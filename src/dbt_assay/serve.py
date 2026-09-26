"""`assay serve`: the report and the review form over http, and a person's decisions into the store.

*** SAVE RECORDS, AND KEEPS THE FILE. *** (D14) Served, the form's save posts here; the decisions
are recorded in the store straight away (the served report is what the person just read, so there
is nothing to review twice) and kept as `decisions-<when>-<who>.json` in the decisions folder. That
file is the record: the agent that applies the approved fixes fetches it through the server's MCP
(`decisions`), commits it with them, and `withdraw_decisions` reads it to take the save back.
Opened from disk, the form downloads the same file instead.

*** WHAT IT DOES AND DOES NOT DO. ***
- It serves the files the scheduled run already built (assay.html, review.html and their data).
  It never renders a page on request.
- It records VERDICTS and fix decisions only. Config edits stay in the file, as edits for the
  repository, because on a server audit.yml comes from git (S3).
- DuckDB has one writer. If the scheduled run holds the store, the save waits and is retried
  every minute until it can be recorded.
- After a save is recorded, the pages are rebuilt in the background when `--target` is given,
  so the served report shows the new decisions.
- It has no authentication. Bind it to an address only trusted people can reach (a tailnet).

uvicorn serves it, Starlette routes it; one worker thread for recording, one for rebuilds.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import handback as hb

MAX_BODY = 20 * 1024 * 1024
RETRY_SECONDS = 60
_NAME = re.compile(r"^(decisions-|handback)[\w.-]*\.json$")


class Server:
    """The state the request handler reads and the workers write."""

    def __init__(self, pages: Path, handbacks: Path, store: str, config: str = ".",
                 target: str | None = None, monitoring: str | None = None,
                 retry_seconds: int = RETRY_SECONDS):
        self.pages = Path(pages).resolve()
        self.handbacks = Path(handbacks).resolve()
        self.handbacks.mkdir(parents=True, exist_ok=True)
        self.store = store
        self.config = config
        self.target = target
        self.monitoring = monitoring
        self.retry_seconds = retry_seconds
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.rebuild = {"state": "idle", "at": "", "detail": ""}
        self._rebuild_wanted = threading.Event()
        self._stop = threading.Event()

    # ------------------------------------------------------------------ decisions files and state
    def _status_path(self, name: str) -> Path:
        return self.handbacks / (name + ".status")

    def status(self, name: str) -> dict:
        p = self._status_path(name)
        if p.exists():
            try:
                return json.loads(p.read_text())
            except ValueError:
                pass
        return {"state": "new", "at": "", "detail": ""}

    def set_status(self, name: str, **kw) -> dict:
        with self.lock:
            st = {**self.status(name), **kw, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            self._status_path(name).write_text(json.dumps(st, indent=2, default=str))
            return st

    def names(self) -> list[str]:
        return sorted((p.name for p in hb._found(self.handbacks)), reverse=True)

    def save(self, payload: dict) -> str:
        """Keep a save under a name nobody else's can collide with, and queue it to be recorded."""
        with self.lock:
            name = hb.decisions_name(self.handbacks, payload.get("by") or "")
            (self.handbacks / name).write_text(json.dumps(payload, indent=2, default=str))
        self.set_status(name, state="queued", detail="recording")
        self.wake.set()
        return name

    def entry(self, name: str) -> dict:
        if not _NAME.match(name) or not (self.handbacks / name).exists():
            return {"name": name, "error": f"no decisions file called {name}"}
        try:
            payload = json.loads((self.handbacks / name).read_text())
        except (OSError, ValueError) as e:
            return {"name": name, "error": f"cannot be read: {e}", **self.status(name)}
        return {"name": name, "preview": hb.preview(payload), **self.status(name)}

    # ------------------------------------------------------------------ recording, with the lock
    def _apply_one(self, name: str) -> None:
        from .store import Store, StoreLocked
        path = self.handbacks / name
        payload = json.loads(path.read_text())
        if hb.is_saved(payload):
            # recorded already (a restart between the write and the status): never twice
            self.set_status(name, state="applied", detail="recorded")
            return
        try:
            store = Store(self.store)
        except StoreLocked as e:
            self.set_status(name, state="waiting",
                            detail=f"the store is in use by another run; retrying every "
                                   f"{self.retry_seconds}s. {e}")
            return
        except Exception as e:                                   # noqa: BLE001
            self.set_status(name, state="failed", detail=f"could not open the store: {e}")
            return
        try:
            got = hb.record(store, payload)
            hb.write_decisions(path, payload, got)
        except Exception as e:                                   # noqa: BLE001
            self.set_status(name, state="failed", detail=f"recording failed: {e}")
            return
        finally:
            store.close()
        edits = hb.refused_config(payload)
        fd = got.get("fixes_decided") or {}
        self.set_status(name, state="applied",
                        result={k: v for k, v in got.items() if k != "undo"},
                        config_edits=edits,
                        detail=f"recorded {got['recorded']} verdict(s) and "
                               f"{sum(fd.values())} fix decision(s) as {got['by']}"
                               + (f"; {len(edits)} config edit(s) are in the file, for the "
                                  f"repository" if edits else ""))
        self._rebuild_wanted.set()

    def _worker(self) -> None:
        last_try: dict = {}
        while not self._stop.is_set():
            self.wake.wait(timeout=5)
            self.wake.clear()
            for name in sorted(self.names()):
                st = self.status(name).get("state")
                if st == "queued" or (st == "waiting" and
                                      time.monotonic() - last_try.get(name, 0) >= self.retry_seconds):
                    last_try[name] = time.monotonic()
                    self._apply_one(name)

    # ------------------------------------------------------------------ rebuilding the pages
    def _rebuild_worker(self) -> None:
        while not self._stop.is_set():
            if not self._rebuild_wanted.wait(timeout=5):
                continue
            self._rebuild_wanted.clear()
            if not self.target:
                self.rebuild = {"state": "skipped", "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "detail": "no --target, so the pages update on the next "
                                          "scheduled run"}
                continue
            self.rebuild = {"state": "running", "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "detail": "rebuilding the report and the form"}
            ok, why = self._rebuild()
            self.rebuild = {"state": "done" if ok else "failed",
                            "at": time.strftime("%Y-%m-%d %H:%M:%S"), "detail": why}

    def _rebuild(self) -> tuple[bool, str]:
        """Build into a scratch folder beside the pages, then swap the files in, so a reader never
        gets a half-written page."""
        tmp = self.pages / ".rebuild"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        run = [sys.executable, "-c", "from dbt_assay.cli import main; main()"]
        common = ["--target", self.target, "--store", self.store, "--config", self.config]
        mon = ["--monitoring", self.monitoring] if self.monitoring else []
        env = {**os.environ, "ASSAY_LOCK_TIMEOUT": os.environ.get("ASSAY_LOCK_TIMEOUT", "120")}
        steps = [
            (tmp / "assay.html", [*run, "page", str(tmp / "assay.html"), *common, *mon,
                                  "--form", "review.html", "--data", str(tmp / "assay-data")]),
            (tmp / "review.html", [*run, "review", "--emit", str(tmp / "review.html"), *common,
                                   *mon, "--report", "assay.html"]),
        ]
        for out, cmd in steps:
            p = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False,
                               timeout=1800)
            if p.returncode != 0 or not out.exists():
                return False, (p.stderr or p.stdout or "no output")[-400:].strip()
        for f in ("assay.html", "review.html"):
            os.replace(tmp / f, self.pages / f)
        if (tmp / "assay-data").exists():
            shutil.rmtree(self.pages / "assay-data", ignore_errors=True)
            os.replace(tmp / "assay-data", self.pages / "assay-data")
        shutil.rmtree(tmp, ignore_errors=True)
        return True, "the report and the form were rebuilt"

    def start_workers(self) -> None:
        for fn in (self._worker, self._rebuild_worker):
            threading.Thread(target=fn, daemon=True).start()
        # Anything left queued or waiting by a previous run is picked up again.
        self.wake.set()

    def stop(self) -> None:
        self._stop.set()
        self.wake.set()
        self._rebuild_wanted.set()


# ---------------------------------------------------------------------- http
# *** UVICORN RUNS IT, STARLETTE ROUTES IT. *** Both come with the `serve` extra (and with `mcp`),
# so the http layer is a list of routes rather than a request parser written here.

def server_modules():
    """(starlette pieces, uvicorn), or a RuntimeError naming the install."""
    try:
        import uvicorn
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import (
            FileResponse,
            HTMLResponse,
            JSONResponse,
            RedirectResponse,
            Response,
        )
        from starlette.routing import Route
    except ImportError as e:
        raise RuntimeError("assay serve needs uvicorn and starlette: "
                           "pip install 'dbt-assay[serve]'") from e
    return (Starlette, Request, FileResponse, HTMLResponse, JSONResponse, RedirectResponse,
            Response, Route), uvicorn


def build_app(srv: Server):
    """The Starlette app for one Server. Separate from `run`, so a test can call it."""
    (Starlette, _Request, FileResponse, _HTMLResponse, JSONResponse, RedirectResponse,
     Response, Route), _uv = server_modules()

    async def home(_req):
        if (srv.pages / "assay.html").exists():
            return RedirectResponse("/assay.html", status_code=302)
        return Response("the daily run has not built the report yet", status_code=404,
                        media_type="text/plain")

    async def listing(_req):
        return JSONResponse({"decisions": [srv.entry(n) for n in srv.names()],
                             "rebuild": srv.rebuild})

    async def one(req):
        out = srv.entry(req.path_params["name"])
        return JSONResponse(out, status_code=404 if "error" in out and "state" not in out
                            else 200)

    async def body_of(req):
        if int(req.headers.get("content-length") or 0) > MAX_BODY:
            return None, "the decisions are larger than 20 MB"
        try:
            return await req.json(), ""
        except ValueError as e:
            return None, f"not JSON: {e}"

    async def post(req):
        body, why = await body_of(req)
        if body is None:
            return JSONResponse({"error": why}, status_code=400)
        if not isinstance(body, dict) or not isinstance(body.get("verdicts"), list):
            return JSONResponse({"error": "these are not decisions the review form wrote: there "
                                          "is no `verdicts` list"}, status_code=400)
        name = srv.save(body)
        return JSONResponse({"saved": name, "preview": hb.preview(body), **srv.status(name)})

    async def static(req):
        # A file the scheduled run built, and nothing outside that folder.
        f = (srv.pages / req.path_params["path"]).resolve()
        if not str(f).startswith(str(srv.pages) + os.sep) or not f.is_file() \
                or ".rebuild" in f.relative_to(srv.pages).parts:
            return Response("not found", status_code=404, media_type="text/plain")
        return FileResponse(f, headers={"Cache-Control": "no-store"})

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        srv.start_workers()
        yield
        srv.stop()

    return Starlette(routes=[
        Route("/", home),
        Route("/api/decisions", listing), Route("/api/decisions", post, methods=["POST"]),
        Route("/api/decisions/{name}", one),
        # a form built before 0.54.1 posts here; the daily run rebuilds it, until then it works
        Route("/api/handback", post, methods=["POST"]),
        Route("/{path:path}", static),
    ], lifespan=lifespan)


def run(srv: Server, host: str, port: int) -> None:
    _mods, uvicorn = server_modules()
    uvicorn.run(build_app(srv), host=host, port=port, log_level="info")
