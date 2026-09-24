"""`assay serve` (S1, S2): the form posts its handback, the server keeps it, and applying it
records the verdicts only, waiting while another process holds the store."""
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
        h = {x["name"]: x for x in client.get("/api/handbacks").json()["handbacks"]}[name]
        if h["state"] == want:
            return h
        time.sleep(0.25)
    raise AssertionError(f"{name} never reached {want}: {h}")


def test_a_posted_handback_is_kept_previewed_and_applied_as_verdicts_only(site):
    srv, store = site
    with TestClient(serve.build_app(srv)) as c:
        assert c.get("/", follow_redirects=False).headers["location"] == "/assay.html"
        assert c.get("/assay.html").text == "<html>report</html>"
        assert c.get("/../secret.txt").status_code == 404
        assert c.post("/api/handback", json={"no": "verdicts"}).status_code == 400
        r = c.post("/api/handback", json=HANDBACK).json()
        name = r["saved"]
        assert (srv.handbacks / name).exists(), "the handback was not kept"
        assert r["preview"]["verdicts"] == 1
        assert r["preview"]["config_edits"] == ["vocab.wdid.means: a structure id"]
        assert c.post("/api/apply", json={"name": name}).status_code == 200
        h = _wait(c, name, "applied")
        assert h["refused"] == ["vocab.wdid.means: a structure id"]
        again = c.post("/api/apply", json={"name": name}).json()
        assert "already applied" in again.get("note", ""), "a handback could be recorded twice"
    import duckdb
    con = duckdb.connect(str(store), read_only=True)
    n = con.execute("select count(*) from adjudications where decided_by = 'ryan'").fetchone()[0]
    assert n >= 1, "applying recorded nothing"


def test_a_busy_store_makes_the_handback_wait_and_it_is_recorded_after(site):
    srv, store = site
    hold = (f"import duckdb, time; c = duckdb.connect({str(store)!r}); print('held', flush=True); "
            f"time.sleep(4)")
    holder = subprocess.Popen([sys.executable, "-c", hold], stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    try:
        with TestClient(serve.build_app(srv)) as c:
            name = c.post("/api/handback", json=HANDBACK).json()["saved"]
            c.post("/api/apply", json={"name": name})
            h = _wait(c, name, "waiting")
            assert "in use by another run" in h["detail"]
            holder.wait(timeout=10)
            _wait(c, name, "applied", secs=30)
    finally:
        holder.kill()


def test_the_form_sends_when_served_and_downloads_from_disk():
    from dbt_assay import reviewform
    js = reviewform._JS
    assert "const SERVED = /^https?:$/.test(location.protocol);" in js
    assert "fetch(new URL('api/handback', location.href)" in js
    assert "download: 'handback.json'" in js, "opened from disk it must still download"
