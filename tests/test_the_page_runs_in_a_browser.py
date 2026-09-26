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
TABS = ["models", "chain", "claims", "findings", "areas", "monitoring", "suggest", "answers",
        "spend", "questions", "config", "understood"]


def _open_view(page, view: str) -> None:
    """Open a view the way a person does: its section, then the view in the row under it."""
    sec = page.evaluate(f"sectionOf({view!r})")
    page.click(f'nav button[data-section="{sec}"]')
    if view != "understood":
        page.click(f'#subnav button[data-view="{view}"]')


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
                _open_view(page, tab)
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
            _open_view(page, 'chain')
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
            _open_view(page, 'models')
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
            _open_view(page, 'models')
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
                    _open_view(page, tab)
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
            card = page.locator("#p-findings .card").first
            until = card.locator("input.until")
            note = card.locator("textarea.note")
            # R3: nothing past the four verdicts shows until one is picked
            assert until.is_hidden() and note.is_hidden(), "the reason shows before a verdict"
            card.locator('input[value="accept"]').check()
            assert until.is_visible() and note.is_visible()
            assert "why it stays" in card.locator(".vmore .vlab").first.inner_text().lower(), \
                "the reason box does not say what it is for"
            # and the list in the middle shows the verdict on its row
            assert page.locator("#p-findings .frow.on .fstate").first.inner_text() == "accept"
            until.fill("2027-06-01")
            note.fill("intended: the model is a lookup")

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
            card = page.locator("#p-findings .card").first
            card.locator('input[value="accept"]').check()
            box = card.locator("label.write input[type=checkbox]")
            assert box.is_visible() and box.is_checked()
            card.locator("textarea.note").fill("the envelope is a grid cell, not a radius")
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


def test_no_table_scrolls_sideways_and_every_group_is_a_click(page_file):
    """*** "SIDE SCROLL IN THIS TABLE ISNT SOMETHING I REALLY WANT". ***

    The Findings list pushed its last two columns past the pane, and the only way to see them was
    a horizontal scrollbar nobody notices. Cells wrap now, so no scrolling list is wider than
    itself, measured at two window sizes because a width that fits one does not fit the other.

    And every group is a button in the group column, so the last family is one click away rather
    than hidden behind "+19 more, use the filter".
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for size in ({"width": 1440, "height": 900}, {"width": 1100, "height": 700}):
                page = browser.new_page(viewport=size)
                page.goto(page_file.as_uri())
                page.wait_for_timeout(120)
                for tab in TABS:
                    _open_view(page, tab)
                    page.wait_for_timeout(50)
                    wide = page.evaluate(f"""() => [...document.querySelectorAll('#p-{tab} .list')]
                        .filter(e => e.offsetParent && e.scrollWidth > e.clientWidth + 1)
                        .map(e => e.scrollWidth - e.clientWidth)""")
                    assert not wide, (f"`{tab}` at {size['width']}px scrolls sideways by "
                                      f"{wide}px")
                page.close()

            # The fixture has no answers, so the check runs on every tab that navigates by
            # group, and at least one of them must have groups to click.
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(page_file.as_uri())
            clicked = 0
            for tab in ("claims", "answers", "suggest"):
                _open_view(page, tab)
                page.wait_for_timeout(80)
                items = page.query_selector_all(f"#p-{tab} .gnav .gitem")
                assert items, f"`{tab}` has no group column"
                if len(items) < 2:
                    continue
                items[-1].click()
                page.wait_for_timeout(50)
                on = page.query_selector_all(f"#p-{tab} .gnav .gitem.on")
                assert len(on) == 1, f"`{tab}`: clicking the last group did not pick it"
                clicked += 1
            assert clicked, "no tab had two groups to click, so this checked nothing"
        finally:
            browser.close()


def test_a_backtick_anywhere_in_a_panel_renders_as_code(page_file):
    """*** "MARKDOWN IS ATTEMPTED BUT NOT RENDERED IN THE MONITORING TAB". ***

    A monitor's reading printed "`elementary_test_results``'s own write history" with the ticks
    in it, and so did suggestion headlines and question text. One observer on the panels turns a
    backticked span into code wherever text lands, including text a click adds later. It must
    leave a `pre` alone: a YAML draft with a backtick in it is the draft, not markup.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(page_file.as_uri())
            page.wait_for_timeout(100)
            got = page.evaluate("""async () => {
                const host = document.querySelector('#p-understood');
                const p = document.createElement('p');
                p.textContent = '`elementary_test_results` holds 38,609 rows';
                const pre = document.createElement('pre');
                pre.textContent = 'means: "`x`"';
                host.append(p, pre);
                await new Promise(r => setTimeout(r, 30));
                return [p.querySelector('code') && p.querySelector('code').textContent,
                        p.textContent, pre.querySelector('code') === null];
            }""")
            assert got[0] == "elementary_test_results", "a backticked span stayed as text"
            assert got[1] == "elementary_test_results holds 38,609 rows", got[1]
            assert got[2], "a backtick inside a pre was turned into markup"
        finally:
            browser.close()


def test_the_spend_window_is_calendar_days_ending_on_the_last_day_recorded(page_file):
    """*** "MAYBE ENSURE IT CAN HAVE A CONFIGURABLE WINDOW ... LAST 7 DAYS OR LAST 30 DAYS". ***

    The per-day charts were fixed at the last 30 days that had a row, so a quiet week vanished
    from the axis. The window is calendar days now, a day with no row is an empty day rather than
    a missing one, and it ends on the last day recorded -- never the viewer's clock, which would
    make an old ledger look empty.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(page_file.as_uri())
            page.wait_for_timeout(100)
            got = page.evaluate("""() => {
                const days = [{day: '2026-01-05', usd: 1}, {day: '2026-01-01', usd: 2}];
                const w = calendar(days, 7), all = calendar(days, 0);
                return [w.map(d => d.day), w.filter(d => d.empty).length,
                        all.map(d => d.day), all.filter(d => d.empty).length];
            }""")
            assert got[0] == ["2025-12-30", "2025-12-31", "2026-01-01", "2026-01-02",
                              "2026-01-03", "2026-01-04", "2026-01-05"], got[0]
            assert got[1] == 5, "a day with no row is not shown as an empty day"
            assert got[2][0] == "2026-01-01" and got[2][-1] == "2026-01-05" and got[3] == 3
        finally:
            browser.close()


def test_a_tip_shows_at_once_and_no_tab_starts_with_a_note(page_file):
    """The explanations are tips now, so the tip has to work: hovering a tipped element shows it
    immediately, inside the window, and a click puts it away. And no tab's first element is a grey
    note, which is what every tab used to open on."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 800})
            page.goto(page_file.as_uri())
            page.wait_for_timeout(100)
            page.hover('nav button[data-section="explore"]')
            box = page.query_selector(".tipbox")
            assert box and box.is_visible(), "hovering a section showed no tip"
            assert "model" in box.inner_text()
            b = box.bounding_box()
            assert b["x"] >= 0 and b["x"] + b["width"] <= 1200, "the tip is outside the window"
            page.mouse.down()
            page.mouse.up()
            assert not box.is_visible(), "a click did not put the tip away"
            for tab in TABS:
                _open_view(page, tab)
                page.wait_for_timeout(50)
                first = page.evaluate(f"""() => {{
                    const p = document.querySelector('#p-{tab}');
                    let n = p.firstElementChild;
                    while (n && n.children.length === 1 && !n.className) n = n.firstElementChild;
                    return n ? n.className : ''; }}""")
                assert "note" not in first.split(), f"`{tab}` opens on a grey note again"
        finally:
            browser.close()


def test_the_tab_strip_is_one_row_and_a_menu_on_a_phone(page_file):
    """*** "THE TABS GO OVER TO A SECOND ROW. UNACCEPTABLE". ***

    One row at every desktop width, stepping down in size and then dropping the counts; on a
    phone one menu button names the tab you are on and opens the grouped list."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for w in (1920, 1440, 1366, 1280, 1180, 1100, 1024, 960):
                page = browser.new_page(viewport={"width": w, "height": 800})
                page.goto(page_file.as_uri())
                page.wait_for_timeout(100)
                # the sections, and the longest row under them (Explore's views)
                page.click('nav button[data-section="explore"]')
                for sel in ("nav button[data-section]", "#subnav button"):
                    rows = page.evaluate("""(sel) => new Set([...document.querySelectorAll(sel)]
                        .filter(b => b.offsetParent)
                        .map(b => Math.round(b.getBoundingClientRect().bottom))).size""", sel)
                    right = page.evaluate("""(sel) => Math.max(...[...document.querySelectorAll(
                        sel)].map(b => b.getBoundingClientRect().right))""", sel)
                    assert rows == 1, f"{sel} takes {rows} rows at {w}px"
                    assert right <= w, f"{sel} runs off the screen at {w}px"
                page.close()
            page = browser.new_page(viewport={"width": 420, "height": 800})
            page.goto(page_file.as_uri())
            page.wait_for_timeout(100)
            assert page.locator("#navmenu").is_visible(), "a phone gets rows of tabs again"
            assert not page.locator("nav").is_visible()
            page.click("#navmenu")
            page.click('nav button[data-section="explore"]')
            assert page.locator("#navcur").inner_text() == "Explore"
            assert not page.locator("nav").is_visible(), "picking a section did not close the menu"
            page.click('#subnav button[data-view="findings"]')
            assert page.locator("#p-findings").is_visible()
        finally:
            browser.close()


def test_the_form_header_is_one_line_and_says_nothing_is_in_force(tmp_path, project_dir):
    from playwright.sync_api import sync_playwright

    from dbt_assay import explorer, reviewform
    for src in (reviewform._JS, explorer._VIEWS):
        assert "in force" not in src, "'in force' is back on a page"
    out = tmp_path / "review.html"
    out.write_text(reviewform.form_html([], {}, "p", "x", "0"))
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for w in (1500, 1100):
                page = browser.new_page(viewport={"width": w, "height": 600})
                page.goto(out.as_uri())
                h = page.evaluate("() => document.querySelector('.hmeta').getBoundingClientRect().height")
                assert h < 30, f"the form header wraps at {w}px ({h}px tall)"
                page.close()
        finally:
            browser.close()


@pytest.fixture
def broken_page(tmp_path, project_dir):
    """The fixture project with one declared key counted as duplicated: a broken premise."""
    import duckdb

    from dbt_assay.infer import Schema
    from dbt_assay.manifest import Project
    store = tmp_path / "s.duckdb"
    r = CliRunner().invoke(app, ["check", "--target", str(project_dir), "--store", str(store)])
    assert r.exit_code in (0, 1), r.output
    p = Project.load(project_dir)
    rel = (Schema.load(p, project_dir).relation.get("model.p.int_bad_unique") or "")
    con = duckdb.connect(str(store))
    con.execute("""insert into observed_keys (relation, column_name, row_count, non_null,
                   distinct_ct, status, detail, observed_at, via, minimality, sampled, sample_pct)
                   values (?, 'section_id', 100, 100, 82, 'has_duplicates', '', now(), 'test',
                           '', false, 0)""", [rel.replace('"', '').lower()])
    con.close()
    r = CliRunner().invoke(app, ["check", "--target", str(project_dir), "--store", str(store)])
    assert "broke" in r.output, r.output
    out = tmp_path / "assay.html"
    r = CliRunner().invoke(app, ["page", str(out), "--target", str(project_dir),
                                 "--store", str(store)])
    assert r.exit_code == 0, r.output
    return out


def test_guarantees_shows_a_broken_premise_with_its_evidence_and_dependents(broken_page):
    """Spec 4: open Guarantees, pick the broken premise, find its evidence and what rests on it."""
    from playwright.sync_api import sync_playwright
    errors: list = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for w in (1500, 1100, 420):
                page = browser.new_page(viewport={"width": w, "height": 900})
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(broken_page.as_uri() + "#guarantees")
                page.wait_for_timeout(300)
                if w < 940:
                    assert page.locator("#navcur").inner_text() == "Explore"
                tab = page.locator('#subnav button[data-view="guarantees"]').inner_text()
                assert "Guarantees" in tab
                page.locator(".gitem", has_text="broken").first.click()
                page.locator("tbody tr", has_text="section_id").first.click()
                pane = page.locator(".detail").first.inner_text()
                assert "premise" in pane.lower() and "section_id unique in int_bad_unique" in pane
                assert "18 duplicate value(s) in 100 rows" in pane, pane
                assert "what rests on it" in pane.lower() and "the grain of" in pane
                assert "what to do" in pane.lower()
                right = page.evaluate("() => document.documentElement.scrollWidth")
                assert right <= w, f"the page scrolls sideways at {w}px"
                page.close()
        finally:
            browser.close()
    assert not errors, errors


def test_the_model_pane_says_what_it_is_correct_as_long_as(broken_page):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 900})
            page.goto(broken_page.as_uri() + "#models")
            page.wait_for_timeout(300)
            page.evaluate("() => GO.models('int_bad_unique')")
            pane = page.locator(".detail").first.inner_text()
            assert "correct as long as" in pane.lower()
            assert "counted duplicates" in pane
        finally:
            browser.close()


def test_a_float_sum_pane_shows_the_column_its_type_and_the_fix(tmp_path):
    import importlib.util
    from pathlib import Path

    from playwright.sync_api import sync_playwright

    from dbt_assay import explorer
    spec = importlib.util.spec_from_file_location("te", Path(__file__).with_name("test_explorer.py"))
    te = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(te)
    data = te._tiny()
    data["findings"] = [{
        "id": "ff1", "check": "float_sum_is_not_reproducible", "model": "a", "subject": "m",
        "summary": "`total` is a sum over a floating-point input", "detail": "d", "weight": 2.0,
        "base": 2, "marts": 1, "descendants": 1, "exposures": [], "file": "m.sql",
        "evidence": {"column": "total", "aggregate": "sum", "input": "t.amount",
                     "input_type": "DOUBLE", "type_from": "`t`'s declared type",
                     "recommendation": "sum(cast(t.amount as decimal(18, 2)))"}}]
    data["models"][0]["findings"] = ["ff1"]
    out = tmp_path / "p.html"
    out.write_text(explorer.explorer_html(data, ""))
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        try:
            page = b.new_page(viewport={"width": 1100, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(out.as_uri() + "#findings")
            page.wait_for_timeout(300)
            text = page.locator(".detail").first.inner_text()
            assert "write instead" in text and "sum(cast(t.amount as decimal(18, 2)))" in text
            assert "DOUBLE" in text and not errors, errors
        finally:
            b.close()


def test_a_finding_from_a_proven_rule_shows_the_badge(tmp_path):
    import importlib.util
    from pathlib import Path

    from playwright.sync_api import sync_playwright

    from dbt_assay import explorer
    spec = importlib.util.spec_from_file_location("te", Path(__file__).with_name("test_explorer.py"))
    te = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(te)
    data = te._tiny()
    data["findings"] = [{"id": "p1", "check": "arbitrary_pick", "model": "a", "subject": "m",
                         "summary": "s", "detail": "d", "weight": 2.0, "base": 2, "marts": 0,
                         "descendants": 0, "exposures": [], "file": "m.sql",
                         "evidence": {"partition_by": ["k"],
                                      "proven_rule": "pick_total_on_unique_key"}}]
    out = tmp_path / "p.html"
    out.write_text(explorer.explorer_html(data, ""))
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        try:
            page = b.new_page(viewport={"width": 1100, "height": 900})
            page.goto(out.as_uri() + "#findings")
            page.wait_for_timeout(300)
            text = page.locator(".detail").first.inner_text()
            assert "proven rule" in text and "pick_total_on_unique_key" in text
        finally:
            b.close()


@pytest.mark.skipif(__import__("dbt_assay.toolchain", fromlist=["x"]).lake_for_build() is None,
                    reason="no Lean toolchain at the pinned version here")
def test_the_page_shows_what_is_proven_and_what_lean_refuted(tmp_path):
    import importlib.util
    from pathlib import Path

    from playwright.sync_api import sync_playwright
    spec = importlib.util.spec_from_file_location("tp", Path(__file__).with_name("test_prove.py"))
    tp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tp)
    target = tp.build(tmp_path)
    store = tmp_path / "s.duckdb"
    CliRunner().invoke(app, ["check", "--target", str(target), "--store", str(store)])
    r = CliRunner().invoke(app, ["prove", "--target", str(target), "--store", str(store)])
    assert r.exit_code == 0, r.output
    assert "proven" in r.output
    out = tmp_path / "p.html"
    r = CliRunner().invoke(app, ["page", str(out), "--target", str(target), "--store", str(store)])
    assert r.exit_code == 0, r.output
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        try:
            errors = []
            page = b.new_page(viewport={"width": 1500, "height": 900})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(out.as_uri() + "#models")
            page.wait_for_timeout(300)
            page.evaluate("() => GO.models('uncovered')")
            text = page.locator(".detail").first.inner_text()
            assert "proven (0 of 1)" in text.lower() and "join on id too" in text
            page.evaluate("() => GO.models('covered')")
            text = page.locator(".detail").first.inner_text()
            assert "proven (1 of 1)" in text.lower() and "as long as" in text
            # in its own pane a certificate says "its rows", not the model's name again
            assert "cannot multiply its rows" in text and "covered's rows" not in text, text
            _open_view(page, 'guarantees')
            page.wait_for_timeout(200)
            page.locator(".gitem", has_text="not proven").first.click()
            pane = page.locator(".detail").first.inner_text()
            assert "proof" in pane.lower() and "what is missing" in pane.lower()
            assert not errors, errors
        finally:
            b.close()


def test_a_card_can_be_ruled_finding_by_finding_and_loads_back(tmp_path):
    """Ryan: "what if some findings on a model are right and some aren't". A card's verdict is
    every finding's default; one ruled differently comes back as its own row, and loading the
    handback records each finding with its own verdict."""
    import json

    from playwright.sync_api import sync_playwright

    from dbt_assay import handback, reviewform
    from dbt_assay.store import Store
    card = {"key": "model.p.orders::column_has_no_description", "subject": "model.p.orders",
            "model": "orders", "question": "column_has_no_description",
            "title": "Columns with no description", "file": "models/orders.sql", "marts": 0,
            "descendants": 0, "exposures": [], "agent": None, "read": None,
            "findings": [{"id": f"f{i}", "summary": f"orders.col{i}: no description",
                          "detail": "", "claim": ""} for i in range(3)]}
    out = tmp_path / "review.html"
    out.write_text(reviewform.form_html([card], {}, "p", "x", "0"))
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(accept_downloads=True)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(out.as_uri())
            page.click('button[data-pane="findings"]')
            page.locator(".frow").first.click()
            c = page.locator("#p-findings .card").first
            c.locator('input[value="agree"]').check()
            each = c.locator("details.each")
            each.locator("summary").click()
            each.locator("select").nth(1).select_option("disagree")
            assert "1 of 3" in each.locator("summary").inner_text()
            with page.expect_download() as dl:
                page.click("#dl")
            doc = json.loads(dl.value.path().read_text())
            assert not errors, "\n".join(errors[:5])
        finally:
            browser.close()
    v = {x["verdict"]: x for x in doc["verdicts"]}
    assert set(v) == {"agree", "disagree"} and all(x["split"] == 3 for x in v.values())
    assert v["disagree"]["findings"] == ["f1"] and v["agree"]["findings"] == ["f0", "f2"]
    s = Store(str(tmp_path / "s.duckdb"))
    got = handback.record(s, doc)
    assert got["split_cards"] == 1 and got["findings_dismissed"] == 1 \
        and got["findings_agreed"] == 2
    s.close()


def test_a_fix_is_approved_on_its_card_and_loads_back(tmp_path):
    """The Fix cards (leverage v1): one decision per change. A reject needs a reason; an
    approval reaches the store through the handback, never through an agent."""
    import json

    from playwright.sync_api import sync_playwright

    from dbt_assay import fixes, handback, reviewform
    from dbt_assay.store import Store
    fx = {"id": "abc123", "kind": "document", "kind_title": "Document columns", "kind_rank": 4,
          "title": "Document 2 column(s) of orders", "resolves": 1, "measured": None,
          "decisions": 1, "files": ["models/schema.yml"], "new_files": [], "models": ["orders"],
          "why": ["3 marts downstream"], "how": "Drafted from what assay knows.",
          "recipe": ["dbt parse"], "refused": [], "effect": "", "status": "proposed",
          "diff": "--- a/models/schema.yml\n+++ b/models/schema.yml\n+  - name: id\n"}
    stage = dict(fx, id="st1", kind="stage_raw_source", kind_title="Stage a raw source",
                 kind_rank=8, title="Stage raw.p for 2 model(s) that read it raw", resolves=0)
    ctx = {"words": [], "explanations": [], "waivers": [], "settings": [], "fixes": [fx, stage],
           "open_findings": 5}
    out = tmp_path / "review.html"
    out.write_text(reviewform.form_html([], {}, "p", "x", "0", ctx))
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(accept_downloads=True)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(out.as_uri() + "#embed&pane=fixes")
            assert page.evaluate("document.body.classList.contains('embed')")
            assert page.locator("nav.tabs").is_hidden()
            # one ranked list, no column of kinds, and the header counts the list (Ryan: "fixes
            # 380 / structure 47" over a list of kinds saying 207, 129 and 80)
            assert page.locator("#p-fixes .fgroups").count() == 0
            rows = page.locator("#p-fixes .frow").count()
            head = page.locator("#p-fixes .tgrid").inner_text()
            assert rows == 2 and head.split("\n")[1].startswith("2 clear"), head
            c = page.locator("#p-fixes .card").first
            assert "Document 2 column(s) of orders" in c.inner_text()
            c.locator('input[value="approve"]').check()
            with page.expect_download() as dl:
                page.click("#dl")
            doc = json.loads(dl.value.path().read_text())
            assert not errors, "\n".join(errors[:5])
        finally:
            browser.close()
    assert doc["fixes"] == [{"fix": "abc123", "verdict": "approve", "note": "",
                             "title": "Document 2 column(s) of orders", "kind": "document"}]
    s = Store(str(tmp_path / "s.duckdb"))
    got = handback.record(s, doc)
    assert got["fixes_decided"]["approved"] == 1
    assert fixes.statuses(s)["abc123"]["status"] == "approved"
    # a reject with no reason is refused, like an accept with none
    got = handback.record(s, {"fixes": [{"fix": "abc123", "verdict": "reject"}]})
    assert fixes.statuses(s)["abc123"]["status"] == "approved" and got["recorded_nothing"]
    s.close()


def test_fix_and_decide_are_the_form_embedded_on_its_panes(tmp_path, project_dir):
    """(Ryan) Four sections in one app. Fix and Decide are the review form, embedded once and
    pointed at a pane; without a form beside the page, the section says how to write one."""
    from playwright.sync_api import sync_playwright
    store = str(tmp_path / "s.duckdb")
    CliRunner().invoke(app, ["check", "--target", str(project_dir), "--store", store])
    page_html = tmp_path / "assay.html"
    CliRunner().invoke(app, ["review", "--emit", str(tmp_path / "review.html"), "--target",
                             str(project_dir), "--store", store])
    r = CliRunner().invoke(app, ["page", str(page_html), "--target", str(project_dir),
                                 "--store", store, "--form", "review.html"])
    assert r.exit_code == 0, r.output
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(page_html.as_uri())
            page.click('nav button[data-section="fix"]')
            page.wait_for_timeout(800)
            src = page.locator("#p-form iframe").get_attribute("src")
            assert src == "review.html#embed&pane=fixes", src
            page.click('nav button[data-section="decide"]')
            page.click('#subnav button[data-view="decide:words"]')
            page.wait_for_timeout(500)
            assert page.locator("#p-form iframe").count() == 1        # one form, re-pointed
            assert page.locator("#p-form iframe").get_attribute("src").endswith("pane=words")
            frame = page.frames[1]
            assert frame.evaluate("document.body.classList.contains('embed')")
            assert not frame.locator("#p-words").is_hidden()
            assert page.evaluate("location.hash") == "#decide/words"
            assert not errors, errors
        finally:
            browser.close()
    # a page with no form beside it says so, in the section, rather than opening a broken frame
    bare = tmp_path / "bare.html"
    CliRunner().invoke(app, ["page", str(bare), "--target", str(project_dir), "--store", store])
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(bare.as_uri() + "#fix")
            page.wait_for_timeout(300)
            assert "assay review --emit" in page.locator("#p-form").inner_text()
            assert page.locator("#p-form iframe").count() == 0
        finally:
            browser.close()


def test_a_group_verdict_answers_its_cards_and_the_view_is_capped(tmp_path):
    """(Ryan: "im not deciding on 1600 cards") One verdict for a check's cards, any card can say
    otherwise, and the handback carries one row per card. The groups past 25 sit behind a count."""
    import json

    from playwright.sync_api import sync_playwright

    from dbt_assay import reviewform

    def card(model, check):
        return {"key": f"model.p.{model}::{check}", "subject": f"model.p.{model}",
                "model": model, "question": check, "title": check.replace("_", " "),
                "file": "", "marts": 0, "descendants": 0, "exposures": [],
                "findings": [{"id": f"{model}-{check}", "summary": "s", "detail": "d",
                              "claim": ""}], "agent": None, "read": None}
    cards = [card(m, "code_contradicts_a_claim") for m in ("a", "b", "c")]
    cards += [card("z", f"check_{i:02d}") for i in range(29)]
    out = tmp_path / "review.html"
    out.write_text(reviewform.form_html(cards, {}, "p", "x", "0",
                                        {"words": [], "explanations": [], "waivers": [],
                                         "settings": []}))
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(accept_downloads=True, viewport={"width": 1400, "height": 900})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(out.as_uri() + "#pane=findings")
            names = page.locator("#p-findings .fg .fgname").all_inner_texts()
            assert names[0] == "every card" and len(names) == 27 and names[-1] == "5 more", names
            page.locator("#p-findings .fg", has_text="5 more").click()
            assert page.locator("#p-findings .fg").count() == 31
            # the first group opens by itself; its verdict answers all three cards
            page.locator(".gcard .rbtns button", has_text="agree").first.click()
            page.locator("#p-findings .frow").nth(1).click()
            page.locator('#p-findings .card input[value="disagree"]').check()
            page.locator("#p-findings .card textarea.note").first.fill("b is fine")
            assert page.locator("#count").inner_text() == "3 of 32 answered"
            with page.expect_download() as dl:
                page.click("#dl")
            doc = json.loads(dl.value.path().read_text())
            assert not errors, errors
        finally:
            browser.close()
    got = {(v["model"], v["verdict"]) for v in doc["verdicts"]}
    assert got == {("a", "agree"), ("b", "disagree"), ("c", "agree")}, doc["verdicts"]
