"""`assay serve` (S1, S2, D14): the form's save is recorded straight into the store and kept as
a decisions file, waiting while another process holds the store."""
import json
import subprocess
import sys
import time

import pytest
from typer.testing import CliRunner

from dbt_assay.cli import app

pytest.importorskip("starlette")
from starlette.testclient import TestClient

from dbt_assay import serve

HANDBACK = {"by": "ryan", "verdicts": [
    {"subject": "model.p.orders", "question": "arbitrary_pick", "verdict": "agree",
     "note": "", "model": "orders", "findings": ["abc"]}],
    "config": [{"path": ["vocab", "wdid", "means"], "value": "a structure id"}]}


@pytest.fixture
def site(tmp_path, project_dir):
    store = tmp_path / "s.duckdb"
    r = CliRunner().invoke(app, ["check", "--target", str(project_dir), "--store", str(store)])
    assert r.exit_code in (0, 1), r.output
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "assay.html").write_text("<html>report</html>")
    (tmp_path / "secret.txt").write_text("outside the pages")
    srv = serve.Server(pages, tmp_path / "handbacks", str(store), str(tmp_path),
                       retry_seconds=1)
    return srv, store


def _wait(client, name, want, secs=20):
    for _ in range(secs * 4):
        h = client.get(f"/api/decisions/{name}").json()
        if h["state"] == want:
            return h
        time.sleep(0.25)
    raise AssertionError(f"{name} never reached {want}: {h}")


def test_a_save_is_recorded_and_kept_as_a_decisions_file(site):
    srv, store = site
    with TestClient(serve.build_app(srv)) as c:
        assert c.get("/", follow_redirects=False).headers["location"] == "/assay.html"
        assert c.get("/assay.html").text == "<html>report</html>"
        assert c.get("/../secret.txt").status_code == 404
        assert c.get("/handbacks").status_code == 404, "the handbacks page is gone"
        assert c.post("/api/decisions", json={"no": "verdicts"}).status_code == 400
        r = c.post("/api/decisions", json=HANDBACK).json()
        name = r["saved"]
        assert name.startswith("decisions-") and name.endswith("-ryan.json"), name
        assert r["preview"]["verdicts"] == 1
        h = _wait(c, name, "applied")
        assert h["config_edits"] == ["vocab.wdid.means: a structure id"]
        listed = c.get("/api/decisions").json()["decisions"]
        assert [x["name"] for x in listed] == [name]
        assert c.get("/api/decisions/nope.json").status_code == 404
        # a form built before 0.54.1 posts to the old route, and it still records
        old = c.post("/api/handback", json=HANDBACK).json()["saved"]
        _wait(c, old, "applied")
    doc = json.loads((srv.handbacks / name).read_text())
    assert doc["by"] == "ryan" and doc["verdicts"] == HANDBACK["verdicts"]
    assert doc["recorded"]["recorded"] == 1 and doc["saved_at"].endswith("Z")
    assert doc["undo"], "the file cannot take the save back"
    import duckdb
    con = duckdb.connect(str(store), read_only=True)
    n = con.execute("select count(*) from adjudications where decided_by = 'ryan'").fetchone()[0]
    assert n >= 1, "saving recorded nothing"


def test_a_recorded_file_is_never_recorded_twice(site):
    """A restart between writing the file and writing its status must not record it again."""
    srv, store = site
    with TestClient(serve.build_app(srv)) as c:
        name = c.post("/api/decisions", json=HANDBACK).json()["saved"]
        _wait(c, name, "applied")
    first = (srv.handbacks / name).read_text()
    import duckdb
    con = duckdb.connect(str(store), read_only=True)
    at = con.execute("select decided_at from adjudications where subject = 'model.p.orders'"
                     ).fetchall()
    con.close()
    srv.set_status(name, state="queued")
    srv._apply_one(name)
    # recording it again would overwrite the verdict's time and rewrite the file's `undo` with
    # the first save's rows, so a withdraw would put back this save instead of what came before
    assert (srv.handbacks / name).read_text() == first
    con = duckdb.connect(str(store), read_only=True)
    assert con.execute("select decided_at from adjudications where subject = 'model.p.orders'"
                       ).fetchall() == at
    assert srv.status(name)["state"] == "applied"


def test_a_busy_store_makes_the_save_wait_and_it_is_recorded_after(site):
    srv, store = site
    hold = (f"import duckdb, time; c = duckdb.connect({str(store)!r}); print('held', flush=True); "
            f"time.sleep(4)")
    holder = subprocess.Popen([sys.executable, "-c", hold], stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    try:
        with TestClient(serve.build_app(srv)) as c:
            name = c.post("/api/decisions", json=HANDBACK).json()["saved"]
            h = _wait(c, name, "waiting")
            assert "in use by another run" in h["detail"]
            holder.wait(timeout=10)
            _wait(c, name, "applied", secs=30)
    finally:
        holder.kill()


def test_the_form_saves_when_served_and_downloads_from_disk():
    from dbt_assay import reviewform
    js = reviewform._JS
    assert "const SERVED = /^https?:$/.test(location.protocol);" in js
    assert "fetch(new URL('api/decisions', location.href)" in js
    assert "download: fname" in js and "'decisions-' + stamp" in js, \
        "opened from disk it must still download the decisions file"
    assert "b.textContent = 'save';" in js
    assert "Put your name in first" in js, "a file with no name records nobody"
