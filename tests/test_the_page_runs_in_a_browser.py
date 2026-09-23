"""*** EVERY OTHER CHECK INSPECTS THE ARTIFACT. THIS ONE RUNS IT. ***

File size, exit code, determinism, valid HTML, correct payload and 734 assertions all passed on a
page that was 81 KB of syntactically invalid JavaScript. Every tab rendered, with correct counts,
and did nothing when clicked.

`node --check` catches a parse error in ten seconds and is already a test. It cannot catch the
next layer: a page that PARSES, loads, and throws on the first click -- which is what a list where
a dict was expected did to the chain tab, 573 hops rendering as an empty pane with no error
anywhere a test could see.

So this opens the real file in a real browser, clicks every tab, and asserts two things nothing
else here can: no page error was raised, and no pane is empty.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from dbt_assay.cli import app

pytest.importorskip("playwright.sync_api",
                    reason="playwright is a dev dependency; CI installs it")

# Every tab the page ships. A new one is covered by adding it here, and a tab that stops existing
# fails this test rather than quietly losing its coverage.
TABS = ["models", "chain", "claims", "findings", "suggest", "answers", "spend", "questions",
        "config", "understood"]


@pytest.fixture
def page_file(tmp_path, project_dir):
    """The real page, written by the real commands, against the fixture project.

    `check` first: the edges, findings and runs the page draws live in the store, and a page built
    against an empty one renders every tab as "nothing matches" -- which would pass a test asking
    only whether the tabs are there.
    """
    store = tmp_path / "s.duckdb"
    r = CliRunner().invoke(app, ["check", "--target", str(project_dir), "--store", str(store)])
    assert r.exit_code in (0, 1), r.output        # 1 is "findings were raised", which is normal
    out = tmp_path / "assay.html"
    r = CliRunner().invoke(app, ["page", str(out), "--target", str(project_dir),
                                 "--store", str(store)])
    assert r.exit_code == 0, r.output
    assert out.exists() and out.stat().st_size > 20_000, "the page is suspiciously small"
    return out


def test_the_page_loads_without_raising_and_every_tab_fills(page_file):
    """*** ZERO PAGE ERRORS, AND NO EMPTY PANE. ***

    A pane that renders nothing is the symptom of a handler that threw partway through: the HTML
    is fine, the tab is there, the content is gone. Both halves are needed -- a page can throw and
    still look full, and it can be silent and still be blank.
    """
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.goto(page_file.as_uri())
            page.wait_for_load_state("domcontentloaded")
            assert not errors, "the page threw on load:\n" + "\n".join(errors[:5])

            for tab in TABS:
                button = page.query_selector(f'nav button[data-tab="{tab}"]')
                assert button is not None, f"the page has no `{tab}` tab any more"
                button.click()
                page.wait_for_timeout(60)
                panel = page.query_selector(f"#p-{tab}")
                assert panel is not None, f"`{tab}` has no panel"
                text = (panel.inner_text() or "").strip()
                assert text, (
                    f"the `{tab}` pane is EMPTY. The tab exists, the HTML is fine, and whatever "
                    f"builds it threw partway through -- which is exactly how 573 hops rendered "
                    f"as a blank chain tab with nothing failing.")
                assert not errors, f"`{tab}` threw:\n" + "\n".join(errors[:5])
        finally:
            browser.close()


def test_clicking_a_node_on_the_chain_opens_its_card(page_file):
    """*** THE NODES WERE CLICKABLE, AND THEN THEY WERE NOT, AND NOTHING NOTICED. ***

    Capturing the pointer on `pointerdown` retargets every later event to the SVG root, so the
    click landed on the canvas and never on the node. The handlers were still attached and still
    correct. Only driving it can tell.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(page_file.as_uri())
            page.click('nav button[data-tab="chain"]')
            page.wait_for_timeout(120)
            # The tab opens on the model list; the drawing is one click in.
            row = page.query_selector("#p-chain tbody tr")
            assert row is not None, "the chain tab lists no model with edges"
            row.click()
            page.wait_for_timeout(150)
            node = page.query_selector("#p-chain g.box.clk")
            assert node is not None, (
                "no clickable node is drawn. Every parent, child and the focus model itself "
                "carries a handler, so finding none means the drawing did not happen.")
            node.click()
            page.wait_for_timeout(120)
            assert page.query_selector(".pop") is not None, (
                "clicking a node opened no card. A pan gesture that captures the pointer on "
                "press eats the click, and a tap has to stay a tap.")
            assert not errors, "\n".join(errors[:5])
        finally:
            browser.close()


def test_the_review_form_loads_without_raising(tmp_path, project_dir):
    """The form is the other shipped script, and it owns every box a person types into."""
    from playwright.sync_api import sync_playwright

    out = tmp_path / "review.html"
    r = CliRunner().invoke(app, ["review", "--emit", str(out), "--target", str(project_dir),
                                 "--store", str(tmp_path / "s.duckdb")])
    assert r.exit_code == 0, r.output

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(out.as_uri())
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(120)
            assert not errors, "the form threw on load:\n" + "\n".join(errors[:5])
            body = (page.inner_text("body") or "").strip()
            assert body, "the form rendered nothing at all"
        finally:
            browser.close()


MD_SAMPLE = """# What this model is

One row per **customer** and `order_date`. See [the spec](https://example.com/spec).

- canceled rows are filtered out
- amount is in cents

```sql
select 1
```
"""


def test_markdown_in_a_description_renders_instead_of_showing_its_syntax(tmp_path, project_dir):
    """*** THE PANES THAT ARE DOCUMENTS WERE RENDERED AS ONE FLAT PARAGRAPH. ***

    Descriptions are written by people who write Markdown, so the hashes, asterisks and fences
    came through as literal characters. Sixty lines, inline, no dependency.
    """
    import json

    from playwright.sync_api import sync_playwright

    man = project_dir / "manifest.json"
    raw = json.loads(man.read_text())
    uid = next(iter(raw["nodes"]))
    raw["nodes"][uid]["description"] = MD_SAMPLE
    man.write_text(json.dumps(raw))

    out = tmp_path / "md.html"
    r = CliRunner().invoke(app, ["page", str(out), "--target", str(project_dir),
                                 "--store", str(tmp_path / "none.duckdb")])
    assert r.exit_code == 0, r.output

    name = raw["nodes"][uid]["name"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(out.as_uri())
            page.click('nav button[data-tab="models"]')
            page.wait_for_timeout(100)
            page.fill('#p-models input[type=search]', name)
            page.wait_for_timeout(100)
            row = page.query_selector("#p-models tbody tr")
            assert row is not None, f"no row for {name}"
            row.click()
            page.wait_for_timeout(150)
            pane = page.query_selector("#p-models .detail")
            html = pane.inner_html()
            text = pane.inner_text()
            assert "<h4" in html, "the heading is still a hash in a paragraph"
            assert "<li" in html, "the bullets are still hyphens in a paragraph"
            assert "<code" in html and "<b" in html
            assert "<pre" in html, "the fenced block is still three backticks"
            assert "# What this model is" not in text, "the syntax is on the screen"
            assert "**customer**" not in text
            assert not errors, "\n".join(errors[:5])
        finally:
            browser.close()


def test_a_description_cannot_inject_anything(tmp_path, project_dir):
    """It builds NODES and never HTML. A description is a warehouse's own content, and some
    warehouses have `<script>` in a comment."""
    import json

    from playwright.sync_api import sync_playwright

    man = project_dir / "manifest.json"
    raw = json.loads(man.read_text())
    uid = next(iter(raw["nodes"]))
    raw["nodes"][uid]["description"] = (
        "<img src=x onerror=alert(1)> and [click](javascript:alert(2)) and <b>not bold</b>")
    man.write_text(json.dumps(raw))
    name = raw["nodes"][uid]["name"]

    out = tmp_path / "xss.html"
    assert CliRunner().invoke(app, ["page", str(out), "--target", str(project_dir),
                                    "--store", str(tmp_path / "n.duckdb")]).exit_code == 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            fired = []
            page.on("dialog", lambda d: (fired.append(d.message), d.dismiss()))
            page.goto(out.as_uri())
            page.click('nav button[data-tab="models"]')
            page.fill('#p-models input[type=search]', name)
            page.wait_for_timeout(100)
            page.query_selector("#p-models tbody tr").click()
            page.wait_for_timeout(200)
            pane = page.query_selector("#p-models .detail")
            assert "<img" not in pane.inner_html(), "a description built an element"
            assert "javascript:" not in pane.inner_html(), "a javascript: url became a link"
            assert "<b>not bold</b>" in pane.inner_text(), "the tag should read as text"
            assert not fired, f"a description executed: {fired}"
        finally:
            browser.close()


def test_the_mark_renders_in_both_headers(page_file, tmp_path, project_dir):
    """An inline SVG that a browser refuses is a blank square, and no static check sees it."""
    from playwright.sync_api import sync_playwright

    form = tmp_path / "review.html"
    assert CliRunner().invoke(app, ["review", "--emit", str(form), "--target", str(project_dir),
                                    "--store", str(tmp_path / "r.duckdb")]).exit_code == 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            errors: list[str] = []
            for f in (page_file, form):
                page = browser.new_page()
                errors.clear()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(f.as_uri())
                page.wait_for_timeout(80)
                box = page.eval_on_selector(
                    "h1 > svg",
                    "el => { const r = el.getBoundingClientRect();"
                    "        return {w: r.width, h: r.height, wells: el.querySelectorAll('circle')"
                    ".length}; }")
                assert box["wells"] == 16, f"{f.name}: the plate lost wells"
                assert 14 <= box["w"] <= 40 and 14 <= box["h"] <= 40, (
                    f"{f.name}: the mark rendered at {box['w']}x{box['h']}, which is not header "
                    f"size -- an unsized flex SVG collapses or fills the row")
                assert not errors, "\n".join(errors[:3])
                page.close()
        finally:
            browser.close()
