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


@pytest.fixture(scope="session", autouse=True)
def _chromium_or_skip():
    """*** THE PACKAGE BEING INSTALLED IS NOT THE BROWSER BEING INSTALLED. ***

    `playwright` is a dev dependency, so `importorskip` passes everywhere -- and then
    `chromium.launch()` fails on any machine that has not run `playwright install`. A test that
    goes red because a binary is missing teaches people to ignore this file, which is the one
    file here that runs the artifact rather than inspecting it. The dedicated CI job installs the
    browser and does NOT skip.
    """
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            pw.chromium.launch().close()
    except Exception as e:                                       # noqa: BLE001
        pytest.skip(f"chromium is not installed here: `uv run playwright install chromium` "
                    f"({str(e)[:120]})")

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
                    "h1 > .mark",
                    "el => { const r = el.getBoundingClientRect();"
                    "        return {w: r.width, h: r.height, drawn: e_natural(el)}; }"
                    .replace("e_natural(el)", "el.naturalWidth || 0"))
                assert box["drawn"] > 0, f"{f.name}: the mark failed to decode"
                assert 14 <= box["w"] <= 40 and 14 <= box["h"] <= 40, (
                    f"{f.name}: the mark rendered at {box['w']}x{box['h']}, which is not header "
                    f"size -- an unsized flex SVG collapses or fills the row")
                # and the plates actually decoded: a broken embed renders at zero
                cuts = page.eval_on_selector_all(
                    "img.cut, .taskcut img, .ticketcut img",
                    "els => els.map(e => e.naturalWidth)")
                assert all(w > 0 for w in cuts), f"{f.name}: a plate failed to decode"
                assert not errors, "\n".join(errors[:3])
                page.close()
        finally:
            browser.close()


def test_no_tab_scrolls_the_document(page_file):
    """*** THE SCROLL BELONGS TO THE PANE, AND EVERY TAB WAS OUT BY 12px. ***

    "the left area INCLUDING filters and dropdowns and checkboxes and the scrollable list need to
    be the same size as the window... so that the screen doesnt jankily scroll down when it
    shouldnt."

    The height was `calc(100vh - 210px)`, a number counted once by hand, and driving the real
    page found it 12px short on every single tab. No static check can see that -- the CSS is
    valid and the arithmetic is somebody's -- so it is measured here, in a browser, at two
    window sizes, because a constant is right at one size and wrong at the other.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for size in ({"width": 1440, "height": 900}, {"width": 1100, "height": 620}):
                page = browser.new_page(viewport=size)
                page.goto(page_file.as_uri())
                page.wait_for_timeout(120)
                for tab in TABS:
                    page.click(f'nav button[data-tab="{tab}"]')
                    page.wait_for_timeout(50)
                    over = page.evaluate(
                        "() => document.documentElement.scrollHeight - window.innerHeight")
                    assert over <= 1, (
                        f"`{tab}` at {size['width']}x{size['height']} makes the DOCUMENT scroll "
                        f"by {over}px. The panes scroll; the page does not.")
                page.close()
        finally:
            browser.close()


def test_accept_on_a_card_and_a_waiver_from_the_tab_reach_the_handback(tmp_path, project_dir):
    """*** A VERDICT THE FORM CANNOT HAND BACK DOES NOT EXIST. ***

    `accept` is recorded from the card, with the date it lapses, and a finding already accepted
    shows up on the Waivers tab as one complete named waiver -- never as loose fields audit.yml
    cannot load, which is what that tab used to write.
    """
    import json

    from playwright.sync_api import sync_playwright
    store = str(tmp_path / "s.duckdb")
    r = CliRunner().invoke(app, ["check", "-t", str(project_dir), "--store", store, "--json"])
    fid = json.loads(r.output)["findings"][0]["finding"]
    r = CliRunner().invoke(app, ["review", "--store", store, "-t", str(project_dir),
                                 "--finding", fid, "--verdict", "accept",
                                 "--note", "a grid cell, not a radius", "--until", "2999-01-01"])
    assert r.exit_code == 0, r.output
    out = tmp_path / "review.html"
    r = CliRunner().invoke(app, ["review", "--emit", str(out), "--target", str(project_dir),
                                 "--store", store])
    assert r.exit_code == 0, r.output

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(accept_downloads=True)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(out.as_uri())
            page.click('button[data-pane="findings"]')
            card = page.locator(".card").first
            until = card.locator("input.until")
            assert until.is_hidden(), "the date only belongs to an accept"
            card.locator('input[value="accept"]').check()
            assert until.is_visible()
            drafted = card.locator("input.note").input_value()
            assert drafted.endswith("It stays because "), \
                "the evidence half of the reason is drafted from the finding"
            until.fill("2027-06-01")
            card.locator("input.note").fill("intended: the model is a lookup")

            page.click('button[data-pane="waivers"]')
            page.locator("label.write input[type=checkbox]").first.check()
            with page.expect_download() as dl:
                page.click("#dl")
            doc = json.loads(dl.value.path().read_text())
            assert not errors, "\n".join(errors[:5])
        finally:
            browser.close()

    v = [x for x in doc["verdicts"] if x["verdict"] == "accept"]
    assert v and v[0]["until"] == "2027-06-01" and v[0]["note"]
    waivers = [c for c in doc["config"] if c["path"][0] == "waivers"]
    # One from the Waivers tab (the finding accepted earlier) and one from the card accepted here.
    assert len(waivers) == 2 and all(len(w["path"]) == 2 for w in waivers)
    from_card = [w for w in waivers if w["value"]["reason"].startswith("intended")]
    assert from_card and from_card[0]["value"]["until"] == "2027-06-01"
    waivers = [w for w in waivers if w not in from_card]
    body = waivers[0]["value"]
    assert body["reason"] == "a grid cell, not a radius" and body["until"] == "2999-01-01"
    assert body["question"] and body["applies_to"]
    from dbt_assay.config import Config
    Config.from_dict({"waivers": {waivers[0]["path"][1]: body}})       # and it loads


def test_a_waiver_is_written_from_the_card_it_was_decided_on(tmp_path, project_dir):
    """Accept on the card, finish the reason, and the handback carries the waiver -- one
    complete named waiver for that model and check. Untick it and it is gone."""
    import json

    from playwright.sync_api import sync_playwright
    store = str(tmp_path / "s.duckdb")
    CliRunner().invoke(app, ["check", "-t", str(project_dir), "--store", store])
    out = tmp_path / "review.html"
    r = CliRunner().invoke(app, ["review", "--emit", str(out), "--target", str(project_dir),
                                 "--store", store])
    assert r.exit_code == 0, r.output

    def handback(page):
        with page.expect_download() as dl:
            page.click("#dl")
        return json.loads(dl.value.path().read_text())

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(accept_downloads=True)
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(out.as_uri())
            page.click('button[data-pane="findings"]')
            card = page.locator(".card").first
            card.locator('input[value="accept"]').check()
            box = card.locator("label.write input[type=checkbox]")
            assert box.is_visible() and box.is_checked()
            card.locator("input.note").fill("the envelope is a grid cell, not a radius")
            doc = handback(page)
            ws = [c for c in doc["config"] if c["path"][0] == "waivers"]
            assert len(ws) == 1 and ws[0]["value"]["reason"].startswith("the envelope")
            v = next(x for x in doc["verdicts"] if x["verdict"] == "accept")
            assert ws[0]["value"]["question"] == v["question"]
            box.uncheck()
            doc = handback(page)
            assert not [c for c in doc["config"] if c["path"][0] == "waivers"]
            tabs = [t.get_attribute("data-pane") for t in page.locator(".tabs button").all()]
            assert tabs[-1] == "settings", tabs
            assert not errors, errors
        finally:
            browser.close()
