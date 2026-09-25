"""G-B: a finding a person agreed with, that was fixed, and came back."""
import hashlib
import json

from typer.testing import CliRunner

from dbt_assay.cli import app
from dbt_assay.store import Store

BAD = "select id from raw.t where name ~ 'Denver'"
GOOD = "select id from raw.t where name ~ '.*Denver.*'"
UID = "model.p.stg_bad_tilde"


def _set_sql(target, sql):
    f = target / "compiled" / "p" / "models" / "staging" / "stg_bad_tilde.sql"
    if not f.exists():
        f = next(target.glob("compiled/p/models/*/stg_bad_tilde.sql"))
    f.write_text(sql)
    m = json.loads((target / "manifest.json").read_text())
    m["nodes"][UID]["checksum"]["checksum"] = hashlib.sha256(sql.encode()).hexdigest()
    (target / "manifest.json").write_text(json.dumps(m))


def _check(target, store):
    r = CliRunner().invoke(app, ["check", "--target", str(target), "--store", str(store)])
    assert r.exit_code in (0, 1), r.output
    return r.output


def _tilde_ids(store):
    s = Store(str(store))
    try:
        run = s.latest_run("p")
        return [r[0] for r in s.con.execute(
            "select finding_id from findings where run_id = ? and subject = ? and "
            "check_name <> 'fixed_finding_returned'", [run, UID]).fetchall()]
    finally:
        s.close()


def test_agree_fix_reintroduce_is_one_regressed_naming_both_commits(project_dir, tmp_path):
    store = tmp_path / "s.duckdb"
    _check(project_dir, store)
    ids = _tilde_ids(store)
    assert ids, "the fixture's tilde finding did not fire"
    s = Store(str(store))
    for fid in ids:
        s.adjudicate(f"{UID}::finding::{fid}", "x", "x", "", "agree", who="ryan")
    s.close()
    _set_sql(project_dir, GOOD)
    out = _check(project_dir, store)
    assert "are gone" in out and "back after" not in out
    _set_sql(project_dir, BAD)
    out = _check(project_dir, store)
    assert "back after they were fixed" in out, out
    s = Store(str(store))
    try:
        run = s.latest_run("p")
        rows = s.con.execute("select evidence, summary from findings where run_id = ? and "
                             "check_name = 'fixed_finding_returned'", [run]).fetchall()
    finally:
        s.close()
    # only the tilde finding went with the fix; the others on the model never left
    assert len(rows) == 1, rows
    assert json.loads(rows[0][0])["check_returned"] == "duckdb_full_match"
    t = json.loads(rows[0][0])["timeline"]
    assert t["agreed_by"] == "ryan" and t["gone_run"] and t["back_run"] == "now"
    assert rows[0][1].startswith("came back after it was fixed: ")


def test_a_finding_that_went_because_assay_changed_is_not_regressed(project_dir, tmp_path):
    """Gone with the file unchanged is retired, so coming back is not a regression."""
    from dbt_assay import outcomes
    store = tmp_path / "s.duckdb"
    _check(project_dir, store)
    ids = _tilde_ids(store)
    s = Store(str(store))
    for fid in ids:
        s.adjudicate(f"{UID}::finding::{fid}", "x", "x", "", "agree", who="ryan")
    # a run in which the finding is absent while the file is the same
    s.con.execute("insert into runs (run_id, started_at, project) values ('gap', now(), 'p')")
    s.close()
    out = _check(project_dir, store)
    assert "back after" not in out
    s = Store(str(store))
    try:
        assert outcomes.returned(s, [], None) == []
    finally:
        s.close()


def test_the_new_checks_default_to_queue():
    from types import SimpleNamespace

    from dbt_assay.judged import default_action
    for c in ("fixed_finding_returned", "float_sum_is_not_reproducible",
              "incremental_key_not_unique"):
        assert default_action(SimpleNamespace(check=c, base=1)) == "queue"
    assert default_action(SimpleNamespace(check="narrow_read", base=1)) == "annotate"


def _git(root, *args):
    import subprocess
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin"})
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=False).stdout.strip()


def test_the_page_shows_both_commits_of_a_returned_finding(project_dir, tmp_path):
    from conftest import require_chromium
    require_chromium()
    from playwright.sync_api import sync_playwright
    root = project_dir.parent
    store = tmp_path / "store" / "s.duckdb"
    store.parent.mkdir()
    _git(root, "init", "-q")
    _git(root, "remote", "add", "origin", "git@github.com:acme/warehouse.git")
    _git(root, "add", "-A"); _git(root, "commit", "-qm", "one")
    _check(project_dir, store)
    s = Store(str(store))
    run = s.latest_run("p")
    for (fid,) in s.con.execute("select finding_id from findings where run_id = ? and "
                                "check_name = 'duckdb_full_match'", [run]).fetchall():
        s.adjudicate(f"{UID}::finding::{fid}", "x", "x", "", "agree", who="ryan")
    s.close()
    _set_sql(project_dir, GOOD)
    fixed = _git(root, "commit", "-qam", "fix")
    _check(project_dir, store)
    _set_sql(project_dir, BAD)
    back = _git(root, "commit", "-qam", "revert")
    _check(project_dir, store)
    out = tmp_path / "assay.html"
    r = CliRunner().invoke(app, ["page", str(out), "--target", str(project_dir),
                                 "--store", str(store)])
    assert r.exit_code == 0, r.output
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        try:
            page = b.new_page(viewport={"width": 1500, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(out.as_uri() + "#findings")
            page.wait_for_timeout(300)
            page.locator(".gitem", has_text="fixed_finding_returned").first.click()
            pane = page.locator(".detail").first
            text = pane.inner_text()
            assert "what happened" in text.lower() and "ryan" in text
            assert fixed[:9] in text and back[:9] in text, text
            hrefs = pane.locator("a.lk").evaluate_all("as => as.map(a => a.href)")
            assert f"https://github.com/acme/warehouse/commit/{fixed}" in hrefs, hrefs
            page.click('nav button[data-tab="understood"]')
            page.wait_for_timeout(300)
            assert "regressed" in page.locator("#p-understood").inner_text()
            assert not errors, errors
        finally:
            b.close()
