"""`assay serve`: the report and the review form over http, and handbacks into the store.

*** THE PAGE ALWAYS SAID A SERVER WAS THE NEXT RUNG. ***
Opened from disk, the form can only download its handback, and a browser cannot be told which
folder to save into, so on a server nothing could pick the file up. Served, the form posts the
handback here instead (S1), it lands in the handback folder, and a person applies it from the
handbacks page without a terminal (S2).

*** WHAT IT DOES AND DOES NOT DO. ***
- It serves the files the scheduled run already built (assay.html, review.html and their data).
  It never renders a page on request.
- Applying a handback records its VERDICTS ONLY. The config section is refused and listed, because
  on a server audit.yml comes from git (S3).
- DuckDB has one writer. If the scheduled run holds the store, the handback waits and is retried
  every minute until it can be recorded.
- After a handback is applied, the pages are rebuilt in the background when `--target` is given,
  so the served report shows the new verdicts.
- It has no authentication. Bind it to an address only trusted people can reach (a tailnet).

uvicorn serves it, Starlette routes it; one worker thread for applies, one for rebuilds.
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
_NAME = re.compile(r"^handback[\w.-]*\.json$")


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

    # ------------------------------------------------------------------ handback files and state
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
        return sorted((p.name for p in self.handbacks.glob("handback*.json")), reverse=True)

    def save(self, payload: dict) -> str:
        """Keep a handback under a name nobody else's can collide with."""
        by = re.sub(r"[^\w-]+", "-", str(payload.get("by") or "anonymous"))[:32].strip("-")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = f"handback-{stamp}-{by or 'anonymous'}"
        name, n = base + ".json", 1
        while (self.handbacks / name).exists():
            n += 1
            name = f"{base}-{n}.json"
        (self.handbacks / name).write_text(json.dumps(payload, indent=2, default=str))
        self.set_status(name, state="new", detail="saved; not applied yet")
        return name

    def entry(self, name: str) -> dict:
        p = self.handbacks / name
        try:
            payload = json.loads(p.read_text())
        except (OSError, ValueError) as e:
            return {"name": name, "error": f"cannot be read: {e}", **self.status(name)}
        return {"name": name, "preview": hb.preview(payload), **self.status(name)}

    # ------------------------------------------------------------------ applying, with the lock
    def apply(self, name: str) -> dict:
        """Queue one handback. The worker records it, retrying while the store is busy."""
        if not _NAME.match(name) or not (self.handbacks / name).exists():
            return {"error": f"no handback called {name}"}
        st = self.status(name)
        if st.get("state") == "applied":
            return {"name": name, **st, "note": "already applied; nothing recorded twice"}
        st = self.set_status(name, state="queued", detail="waiting for the worker")
        self.wake.set()
        return {"name": name, **st}

    def _apply_one(self, name: str) -> None:
        from .store import Store, StoreLocked
        payload = json.loads((self.handbacks / name).read_text())
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
        except Exception as e:                                   # noqa: BLE001
            self.set_status(name, state="failed", detail=f"recording failed: {e}")
            return
        finally:
            store.close()
        refused = hb.refused_config(payload)
        self.set_status(name, state="applied", result=got, refused=refused,
                        detail=f"recorded {got['recorded']} verdict(s) as {got['by']}"
                               + (f"; refused {len(refused)} config edit(s)" if refused else ""))
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


# ---------------------------------------------------------------------- the handbacks page
CSS = """
:root{--paper:#faf8f3;--ink:#1a1714;--ash:#615a52;--faint:#948c81;--rule:#cec5b6;
--rule2:#e3dbcd;--rust:#a8491a}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
font:15px/1.55 "Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif}
header{padding:16px 26px 10px;border-bottom:3px double var(--ink);display:flex;gap:18px;
align-items:baseline;flex-wrap:wrap}
header h1{margin:0;font-size:24px;font-weight:400}
header a{color:var(--rust)}
main{padding:18px 26px;max-width:none}
.up{border:1px solid var(--rule);background:#f1ede4;padding:12px 14px;margin:0 0 18px;
display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.hb{border-top:1px solid var(--rule);padding:12px 0}
.hbh{display:flex;gap:14px;align-items:baseline;flex-wrap:wrap}
.name{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
.state{font-size:13px;border:1px solid var(--rule);padding:0 7px}
.state.applied{border-color:var(--ink)}
.state.waiting,.state.failed{border-color:var(--rust);color:var(--rust)}
.detail{color:var(--ash);font-size:14px;margin:4px 0}
dl{display:grid;grid-template-columns:auto 1fr;gap:2px 16px;margin:6px 0;font-size:14px}
dt{color:var(--ash)}dd{margin:0}
button{font:inherit;font-size:14px;border:1px solid var(--rust);color:var(--rust);
background:none;padding:3px 12px;cursor:pointer}
button:hover{background:var(--rust);color:var(--paper)}
button:disabled{border-color:var(--rule);color:var(--faint);background:none;cursor:default}
.refused{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;
color:var(--ash);margin:2px 0 0 0;padding-left:14px;border-left:2px solid var(--rule)}
.empty{color:var(--faint);font-style:italic}
.rb{font-size:13.5px;color:var(--ash)}
"""

JS = r"""
const $ = s => document.querySelector(s);
async function refresh() {
  const r = await fetch('api/handbacks'); const d = await r.json();
  $('#rb').textContent = d.rebuild.state === 'idle' ? '' :
    'pages: ' + d.rebuild.state + (d.rebuild.detail ? ' (' + d.rebuild.detail + ')' : '')
    + (d.rebuild.at ? ', ' + d.rebuild.at : '');
  const host = $('#list'); host.replaceChildren();
  if (!d.handbacks.length) { const p = document.createElement('p'); p.className = 'empty';
    p.textContent = 'No handbacks yet. Send one from the review form, or upload a file above.';
    host.append(p); return; }
  for (const h of d.handbacks) host.append(row(h));
}
function el(t, cls, text) { const n = document.createElement(t); if (cls) n.className = cls;
  if (text != null) n.textContent = text; return n; }
function row(h) {
  const box = el('div', 'hb'); const head = el('div', 'hbh');
  head.append(el('span', 'name', h.name), el('span', 'state ' + h.state, h.state));
  const b = el('button', null, h.state === 'applied' ? 'applied' : 'apply the verdicts');
  b.disabled = ['applied', 'queued'].includes(h.state) || !!h.error;
  b.onclick = async () => { b.disabled = true;
    await fetch('api/apply', {method: 'POST', headers: {'content-type': 'application/json'},
                              body: JSON.stringify({name: h.name})});
    setTimeout(refresh, 800); };
  head.append(b); box.append(head);
  if (h.detail) box.append(el('div', 'detail', h.detail));
  if (h.error) { box.append(el('div', 'detail', h.error)); return box; }
  const p = h.preview, dl = el('dl');
  const kv = (k, v) => { dl.append(el('dt', null, k), el('dd', null, v)); };
  kv('by', p.by);
  kv('verdicts', p.verdicts + ' (' + Object.entries(p.by_verdict).map(([k, n]) => n + ' ' + k)
                                        .join(', ') + ')');
  kv('questions', Object.entries(p.by_question).map(([k, n]) => k + ' ' + n).join(', ') || 'none');
  if (p.recorded_nothing) kv('not recorded', p.recorded_nothing + ' row(s) with no verdict');
  box.append(dl);
  if (p.config_edits.length) {
    box.append(el('div', 'detail', p.config_edits.length + ' config edit(s) '
      + (h.state === 'applied' ? 'were refused' : 'will be refused')
      + '. audit.yml on this server comes from git: apply them from a checkout of the repository '
      + 'with `assay review --load ' + h.name + ' --apply`, then commit audit.yml:'));
    for (const e of p.config_edits) box.append(el('div', 'refused', e));
  }
  return box;
}
$('#file').onchange = async ev => {
  const f = ev.target.files[0]; if (!f) return;
  const r = await fetch('api/handback', {method: 'POST', headers: {'content-type': 'application/json'},
                                         body: await f.text()});
  const d = await r.json(); $('#upmsg').textContent = d.error ? d.error : 'saved as ' + d.saved;
  ev.target.value = ''; refresh();
};
refresh(); setInterval(refresh, 5000);
"""


def handbacks_page() -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>assay handbacks</title><style>{CSS}</style></head><body>"
            f"<header><h1>Handbacks</h1><a href='assay.html'>the report</a>"
            f"<a href='review.html'>the review form</a><span class='rb' id='rb'></span></header>"
            f"<main><div class='up'><label>upload a handback made elsewhere "
            f"<input type='file' id='file' accept='.json,application/json'></label>"
            f"<span id='upmsg' class='detail'></span></div><div id='list'></div></main>"
            f"<script>{JS}</script></body></html>")


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
    (Starlette, _Request, FileResponse, HTMLResponse, JSONResponse, RedirectResponse,
     Response, Route), _uv = server_modules()

    async def home(_req):
        return RedirectResponse("/assay.html" if (srv.pages / "assay.html").exists()
                                else "/handbacks", status_code=302)

    async def page(_req):
        return HTMLResponse(handbacks_page(), headers={"Cache-Control": "no-store"})

    async def listing(_req):
        return JSONResponse({"handbacks": [srv.entry(n) for n in srv.names()],
                             "rebuild": srv.rebuild})

    async def body_of(req):
        if int(req.headers.get("content-length") or 0) > MAX_BODY:
            return None, "the handback is larger than 20 MB"
        try:
            return await req.json(), ""
        except ValueError as e:
            return None, f"not JSON: {e}"

    async def post_handback(req):
        body, why = await body_of(req)
        if body is None:
            return JSONResponse({"error": why}, status_code=400)
        if not isinstance(body, dict) or not isinstance(body.get("verdicts"), list):
            return JSONResponse({"error": "this is not a handback the review form wrote: it has "
                                          "no `verdicts` list"}, status_code=400)
        name = srv.save(body)
        return JSONResponse({"saved": name, "preview": hb.preview(body), "view": "handbacks"})

    async def apply(req):
        body, why = await body_of(req)
        out = srv.apply(str((body or {}).get("name") or "")) if body is not None \
            else {"error": why}
        return JSONResponse(out, status_code=400 if "error" in out else 200)

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
        Route("/", home), Route("/handbacks", page),
        Route("/api/handbacks", listing), Route("/api/handback", post_handback, methods=["POST"]),
        Route("/api/apply", apply, methods=["POST"]), Route("/{path:path}", static),
    ], lifespan=lifespan)


def run(srv: Server, host: str, port: int) -> None:
    _mods, uvicorn = server_modules()
    uvicorn.run(build_app(srv), host=host, port=port, log_level="info")
