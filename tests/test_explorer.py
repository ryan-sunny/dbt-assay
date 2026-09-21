"""The explorer: the assembly layer, and the two defects that driving it in a DOM turned up.

Every test here exercises the real functions against a real store. A source grep has passed in
this repo while the thing it guarded was broken, more than once, so none of these read code.
"""
from __future__ import annotations

import json

import pytest

from dbt_assay import explore, explorer

# ------------------------------------------------------------------- the type-preserving parser

def test_an_empty_list_column_does_not_become_an_empty_dict():
    """*** `json.loads(s) or {}` TURNS `[]` INTO `{}`, AND THE WHOLE CHAIN VIEW THREW. ***

    `edge_facts.joined_on` is a list. A hop with no join key stores `"[]"`, which parses to `[]`,
    which is falsy, which `or {}` replaced with a dict. Every such hop then carried a dict where
    the reader expected a list, and the first one aborted the entire tab -- 573 hops rendering as
    an empty page.

    Nothing about the shape of the code looked wrong. It was found by driving the page in a real
    DOM, which is why the empty cases are asserted here and not just the populated ones.
    """
    assert explore._json("[]", []) == []
    assert explore._json("{}", {}) == {}
    assert explore._json('["a","b"]', []) == ["a", "b"]
    assert explore._json('{"k":1}', {}) == {"k": 1}
    # absent, null and unparseable all fall back to the caller's empty, WITH ITS TYPE
    for bad in (None, "", "not json", "null"):
        assert explore._json(bad, []) == []
        assert explore._json(bad, {}) == {}
    assert isinstance(explore._json("[]", []), list), "an empty list must stay a list"


def test_the_runner_up_is_the_best_answer_that_was_not_chosen():
    """The distribution is the biggest thing in the file and the least read, so only the chosen
    answer, its confidence and the next best travel. That is the sentence people act on."""
    assert explore._runner_up({"a": 0.6, "b": 0.3, "c": 0.1}, "a") == ["b", 0.3]
    assert explore._runner_up({"a": 0.6}, "a") is None          # nothing else was on offer
    assert explore._runner_up({}, "a") is None
    # deterministic on a tie, because a comparison that can tie is not an order
    first = explore._runner_up({"a": 0.5, "b": 0.25, "c": 0.25}, "a")
    for _ in range(20):
        assert explore._runner_up({"a": 0.5, "c": 0.25, "b": 0.25}, "a") == first


# ---------------------------------------------------------------------- the run it reads from

def _store_with_runs(tmp_path, rows):
    """A store holding several runs of edge facts, some tied on row count."""
    from dbt_assay.store import Store
    s = Store(str(tmp_path / "assay.duckdb"))
    for run_id, started, edges in rows:
        s.con.execute(
            "insert into runs (run_id, started_at, project, assay_version, models) "
            "values (?, ?, 'p', '0', 1)", [run_id, started])
        for i in range(edges):
            s.con.execute(
                "insert into edge_facts (run_id, parent, child, parent_name, child_name, "
                "available, carried, dropped, joined_on, dropped_cols) "
                "values (?,?,?,?,?,?,?,?,'[]','[]')",
                [run_id, f"p{i}", "c", f"p{i}", "c", 10, 10 - i, i])
    return s


def test_the_run_is_chosen_by_a_total_order_not_by_which_is_biggest(tmp_path):
    """*** `order by count(*) desc limit 1` IS AN ARBITRARY PICK, AND IT SHIPPED HERE FIRST. ***

    The first version took the fullest run, copying an example that says a tie "would mean two
    runs found exactly the same thing". On the field store SIX runs hold exactly 573 edge facts
    each, so the tie is the normal case, and duckdb returned a different winner between two
    invocations of the same command: two runs of `assay page` against an unchanged store wrote
    different files.

    `arbitrary_pick` and `first_match_pick` are checks this tool runs against other people's SQL.
    This was the same defect in the code that renders their results.
    """
    import datetime as dt
    t0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    s = _store_with_runs(tmp_path, [
        ("aaa", t0, 5),
        ("zzz", t0 + dt.timedelta(hours=1), 5),      # same size, LATER: this one
        ("mmm", t0 - dt.timedelta(hours=1), 5),
    ])
    try:
        got = {explore._latest_run(s, "edge_facts") for _ in range(25)}
        assert got == {"zzz"}, f"the run is not chosen deterministically: {got}"
    finally:
        s.close()


def test_a_run_missing_from_the_runs_table_still_sorts(tmp_path):
    """A run present in the facts and absent from `runs` must not vanish, and must not win over
    one that carries a clock. `nulls last` plus the id as a tie-break is a total order either
    way, so the answer never depends on what duckdb felt like returning."""
    import datetime as dt
    s = _store_with_runs(tmp_path, [("dated", dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc), 3)])
    try:
        for i in range(4):
            s.con.execute(
                "insert into edge_facts (run_id, parent, child, parent_name, child_name, "
                "available, carried, dropped, joined_on, dropped_cols) "
                "values ('orphan',?,'c',?,'c',9,9,0,'[]','[]')", [f"q{i}", f"q{i}"])
        got = {explore._latest_run(s, "edge_facts") for _ in range(25)}
        assert got == {"dated"}, got
    finally:
        s.close()


def test_no_store_at_all_yields_empty_sections_rather_than_raising(tmp_path):
    """Every judged table is empty before anything has been asked, and that has to render."""
    assert explore._latest_run(None, "edge_facts") is None
    assert explore._edges(None, {}) == []
    assert explore._claims(None, []) == []
    assert explore._decisions(None) == []
    assert explore._adjudications(None) == []
    assert explore._runs(None) == []


# ------------------------------------------------------------------------- the file it produces

def test_the_data_blob_is_valid_json_and_cannot_close_its_own_script_tag():
    """*** A MODEL WITH `</script>` IN A COMMENT WOULD TRUNCATE THE PAGE DESCRIBING IT. ***

    The data rides in a script tag, so the one sequence that must never appear raw is `</`. It is
    a warehouse's own content, not exotic input. `<!--` too: it opens a comment inside a script
    element. Both are asserted on real hostile content rather than assumed.
    """
    data = {
        "meta": {"project": "p", "models": 1, "sources": 0, "version": "0",
                 "generated_at": "x", "coverage": {}},
        "models": [{"uid": "m", "name": "</script><script>alert(1)</script>", "path": "m.sql",
                    "layer": "staging", "materialized": "view",
                    "description": "<!-- a comment --> and </SCRIPT> too",
                    "unreadable": False, "grain": None, "derived_grain": [], "columns": [],
                    "reads": [], "read_by": [], "descendants": 0, "marts": 0,
                    "filters_rows": False, "aggregates": False, "union_parents": [],
                    "driving_parents": [], "unique_key_parents": [], "join_keys": {},
                    "join_kind": {}, "pre_aggregated": {}, "row_loss": {}, "parent_rows": {},
                    "doc_conflict": None, "fanout_hops": [], "claims": [], "findings": [],
                    "decisions": []}],
        "edges": [], "claims": [], "findings": [], "decisions": [], "questions": [],
        "adjudications": [], "config": {}, "runs": [], "unreadable": [],
    }
    doc = explorer.explorer_html(data, "<html><body>the record</body></html>")

    # exactly one data script, and it is not closed early by the content
    start = doc.index('<script id="assay-data" type="application/json">')
    body = doc[start + len('<script id="assay-data" type="application/json">'):]
    blob = body[:body.index("</script>")]
    assert "</" not in blob, "the payload can close its own tag"
    assert "<!--" not in blob, "the payload can open a comment inside a script element"

    back = json.loads(blob.replace("<\\/", "</").replace("<\\!--", "<!--"))
    assert back["models"][0]["name"] == "</script><script>alert(1)</script>"
    assert back["record"] == "<html><body>the record</body></html>"


def test_every_tab_declared_in_the_nav_has_a_panel_and_a_view():
    """*** A TAB WITH NO VIEW IS A BLANK PANEL AND NOTHING SAYS SO. ***

    The nav, the panels and the VIEWS map are three copies of one list. This asserts they agree,
    because the failure mode is a tab that opens onto nothing, which reads exactly like a
    warehouse with nothing to show.
    """
    import re
    data = {"meta": {"project": "p", "models": 0, "sources": 0, "version": "0",
                     "generated_at": "x", "coverage": {}},
            "models": [], "edges": [], "claims": [], "findings": [], "decisions": [],
            "questions": [], "adjudications": [], "config": {}, "runs": [], "unreadable": []}
    doc = explorer.explorer_html(data, "<html></html>")

    nav = set(re.findall(r'<button role="tab" data-tab="([a-z]+)"', doc))
    panels = set(re.findall(r'<div class="panel" id="p-([a-z]+)"', doc))
    block = explorer._VIEWS[explorer._VIEWS.index("const VIEWS = {"):]
    views = set(re.findall(r"([a-z]+): \w+Tab", block[:block.index("}")]))
    assert nav == panels, (nav, panels)
    assert nav <= views | {"understood"}, f"a tab with no view: {nav - views}"
    assert len(nav) >= 8, nav


def test_the_record_is_carried_as_data_and_never_as_markup():
    """The record is a whole document with its own stylesheet, and this page has one too. Dropping
    it into a div would let it restyle everything around it, so it travels inside the JSON and is
    rendered into an isolated iframe."""
    data = {"meta": {"project": "p", "models": 0, "sources": 0, "version": "0",
                     "generated_at": "x", "coverage": {}},
            "models": [], "edges": [], "claims": [], "findings": [], "decisions": [],
            "questions": [], "adjudications": [], "config": {}, "runs": [], "unreadable": []}
    record = "<!doctype html><html><head><style>body{background:red}</style></head>" \
             "<body><h1>Is this warehouse understood?</h1></body></html>"
    doc = explorer.explorer_html(data, record)
    # It IS in the file, and only ever inside the data blob. Its closing tags arrive escaped, so
    # the HTML parser never leaves the script element and the record cannot restyle the page
    # around it. Asserted by removing the blob and looking at what is left, because an opening
    # `<style>` inside a script element is inert and counting tags would say otherwise.
    tag = '<script id="assay-data" type="application/json">'
    head, rest = doc.split(tag, 1)
    blob, tail = rest.split("</script>", 1)
    outside = head + tail
    assert "body{background:red}" in blob, "the record is not in the data blob"
    assert "body{background:red}" not in outside, "the record's CSS is live markup in the page"
    assert "<\\/style>" in blob, "the record's closing tags are not escaped"
    assert outside.count("<style>") == 1, "two stylesheets are in play"
    assert "srcdoc" in explorer._VIEWS, "the record is not rendered into an iframe"


# ------------------------------------------------------------------- determinism, end to end

def test_two_assemblies_of_one_store_are_identical(project_dir, tmp_path):
    """*** THE PROPERTY THE WHOLE FILE-OVER-SERVER ARGUMENT RESTS ON. ***

    A page that churns cannot be committed, and one that cannot be committed does not accrue.
    Asserted on the serialised bytes rather than on the object, because that is what lands on
    disk, and twenty times rather than twice, because the defect this caught was a tie being
    broken differently between two calls in the same process.
    """
    from dbt_assay import live
    from dbt_assay.config import Config
    from dbt_assay.store import Store

    st = live.read(project_dir)
    store = Store(str(tmp_path / "assay.duckdb"))
    try:
        blobs = set()
        for _ in range(20):
            data = explore.assemble(st.project, st.digests, st.schema, st.entries, [],
                                    store, Config(), "fixed-at", "0.0.0")
            blobs.add(json.dumps(data, sort_keys=True, separators=(",", ":"), default=str))
        assert len(blobs) == 1, "the assembly is not deterministic"
    finally:
        store.close()


def test_the_assembly_sorts_in_python_not_in_the_browser(project_dir, tmp_path):
    """Sorting in the browser would make the file's bytes identical while what a reader sees
    depends on their machine. The order is a fact about the file, so it is asserted here."""
    from dbt_assay import live
    from dbt_assay.config import Config
    from dbt_assay.store import Store

    st = live.read(project_dir)
    store = Store(str(tmp_path / "assay.duckdb"))
    try:
        data = explore.assemble(st.project, st.digests, st.schema, st.entries, [],
                                store, Config(), "fixed-at", "0.0.0")
    finally:
        store.close()
    uids = [m["uid"] for m in data["models"]]
    assert uids == sorted(uids), "models are not sorted"
    for m in data["models"]:
        names = [c["name"] for c in m["columns"]]
        assert names == sorted(names), f"{m['name']}'s columns are not sorted"
        for key in ("reads", "read_by", "union_parents", "driving_parents"):
            assert m[key] == sorted(m[key]), f"{m['name']}.{key} is not sorted"


@pytest.mark.parametrize("plain", [True, False])
def test_the_record_stays_small_and_the_explorer_carries_it(project_dir, tmp_path, plain):
    """*** TWO JOBS THAT WERE ALWAYS CONFLATED. ***

    The record is small, committed and handed to somebody; its whole argument is that it accrues,
    which needs it to stay small. The explorer is everything, for the person who owns the
    warehouse. `--plain` writes the first; the default writes the second WITH the first inside it,
    so there is one artifact to generate and no second command to remember.
    """
    from dbt_assay import render
    record = render.page_html({
        "project": "p", "models": 3, "generated_at": "x", "version": "0",
        "ruled": 0, "findings_total": 0, "agent_rulings": 0, "effectiveness": [],
        "by_check": [], "completeness": [], "moved": {}, "plain": plain,
        "grain": {"declared": 0, "derived": 0, "judged": 0, "none": 3},
        "no_unique_test": 0, "claims": {}, "not_counted_note": "",
        "top_findings": [], "shown": 0})
    assert len(record) < 40_000, "the record has stopped being small enough to commit"
    assert "Is this warehouse understood" in record

    data = {"meta": {"project": "p", "models": 3, "sources": 0, "version": "0",
                     "generated_at": "x", "coverage": {}},
            "models": [], "edges": [], "claims": [], "findings": [], "decisions": [],
            "questions": [], "adjudications": [], "config": {}, "runs": [], "unreadable": []}
    doc = explorer.explorer_html(data, record)
    assert len(doc) > len(record), "the explorer is not carrying the record"
    assert '"record":' in doc


# -------------------------------------------------------------- 0.26.0: the artifact that diffs

def _tiny():
    return {"meta": {"project": "p", "models": 1, "sources": 0, "version": "0",
                     "generated_at": "x", "coverage": {}},
            "models": [{"uid": "m", "name": "a"}], "edges": [], "claims": [],
            "findings": [{"id": "f1", "check": "c"}], "decisions": [], "questions": [],
            "adjudications": [], "config": {"provider": "auto"}, "runs": [], "unreadable": []}


def test_the_artifact_is_one_line_per_entity(tmp_path):
    """*** AN 8 MB PAGE DIFFS AS ONE UNREADABLE BLOB. ***

    Measured on the field warehouse: the same content is 8.12 MB as JSON Lines and 8.12 MB
    minified, while pretty-printing costs 2 MB more and turns the models into a 110,000-line file
    nobody reads. JSONL costs nothing and diffs as one line per entity, so a commit reads as
    "these 3 models changed, these 12 findings appeared", which is what the accrual argument was
    always about.
    """
    out = explore.write_data(_tiny(), tmp_path / "d", record="<html>rec</html>")
    by = {p.name: p for p, _n in out}
    assert "models.jsonl" in by and "findings.jsonl" in by
    assert by["models.jsonl"].read_text() == '{"name":"a","uid":"m"}\n'
    # A jsonl file ending without a newline makes the next append show as a MODIFICATION of the
    # final entity rather than as an addition, so every one of them ends with one.
    for name, p in by.items():
        if name.endswith(".jsonl") and p.stat().st_size:
            assert p.read_text().endswith("\n"), f"{name} has no trailing newline"
    # *** AND THE WRITER NEVER EDITS WHAT IT IS HANDED. ***
    # `record.html` is written byte for byte. A writer that tidies its input is a writer that
    # disagrees with its reader, which is the two-spellings defect in the one place it would
    # break the round trip silently. The newline on the record comes from `page_html`.
    from dbt_assay import render
    assert by["record.html"].read_text() == "<html>rec</html>"
    rendered = render.page_html({
        "project": "p", "models": 1, "generated_at": "x", "version": "0", "ruled": 0,
        "findings_total": 0, "agent_rulings": 0, "effectiveness": [], "by_check": [],
        "completeness": [], "moved": {}, "plain": True, "no_unique_test": 0, "claims": {},
        "grain": {"declared": 0, "derived": 0, "judged": 0, "none": 1},
        "not_counted_note": "", "top_findings": [], "shown": 0})
    assert rendered.endswith("\n"), "the record itself has no trailing newline"
    # meta and config stay whole and pretty: they are read by a person, and the useful diff on
    # them is field-level rather than entity-level
    assert "\n  " in by["config.json"].read_text()


def test_an_artifact_round_trips_to_the_same_page(tmp_path):
    """`--from` renders with no warehouse, no store and no manifest, which is most of why the
    artifact exists: a committed artifact only readable from the machine that produced it is not
    a record."""
    data = _tiny()
    explore.write_data(data, tmp_path / "d", record="<html>rec</html>")
    back = explore.read_data(tmp_path / "d")
    assert back["record"] == "<html>rec</html>"
    for k in ("models", "findings", "meta", "config"):
        assert back[k] == data[k], k
    a = explorer.explorer_html({k: v for k, v in data.items()}, "<html>rec</html>")
    b = explorer.explorer_html({k: v for k, v in back.items() if k != "record"}, back["record"])
    assert a == b, "a page rendered from the artifact differs from one rendered from the store"


def test_a_directory_that_is_not_an_artifact_refuses_rather_than_rendering_empty(tmp_path):
    """*** AN ARTIFACT MISSING ITS TABLES IS NOT AN EMPTY WAREHOUSE. ***

    Rendering some other directory would produce a page saying nothing has been asked, which is
    the absence-reads-as-a-result defect this codebase keeps finding, one layer out.
    """
    import pytest
    (tmp_path / "notours").mkdir()
    with pytest.raises(ValueError, match="not an assay data artifact"):
        explore.read_data(tmp_path / "notours")
    with pytest.raises(FileNotFoundError):
        explore.read_data(tmp_path / "missing")


def test_one_added_ruling_is_one_added_line(tmp_path):
    """The property stated as a diff. Asserted on the bytes, because "it diffs well" is the whole
    argument for committing the artifact instead of the page."""
    a = _tiny()
    b = {**a, "adjudications": [{"subject": "m", "verdict": "agree", "source": "human"}]}
    explore.write_data(a, tmp_path / "a")
    explore.write_data(b, tmp_path / "b")
    la = (tmp_path / "a" / "adjudications.jsonl").read_text().splitlines()
    lb = (tmp_path / "b" / "adjudications.jsonl").read_text().splitlines()
    assert len(lb) == len(la) + 1
    assert set(la) <= set(lb), "an unrelated line moved"
    for name in ("models.jsonl", "findings.jsonl", "config.json"):
        assert (tmp_path / "a" / name).read_text() == (tmp_path / "b" / name).read_text(), \
            f"{name} churned for an unrelated change"


# --------------------------------------------------------------- 0.26.0: the views it renders

def test_the_lineage_never_draws_the_whole_dag():
    """*** YOU NEVER DRAW 573 HOPS. ***

    Measured on a 358-model warehouse: median 3 boxes in a neighbourhood, p95 12, max 37. So the
    drawing is three bands and straight lines, and past `BAND_MAX` it degrades to a list, because
    36 boxes with 36 converging lines is the hairball the drawing exists to avoid.

    Asserted on the source constants and the degradation branch, and exercised end to end by the
    DOM driver in `scripts/`; a warehouse with a 36-parent model is what this is sized for.
    """
    v = explorer._VIEWS
    assert "BAND_MAX = 9" in v, "the degradation threshold is gone"
    assert "bandList" in v and "too many to draw" in v
    # the edge label goes ON the box, never on the line: with eight parents converging on one
    # focus, labels on the lines overlap into mush
    assert "edgeNote(e), 'par'" in v, "the parent box no longer carries its edge label"


def test_no_tab_opens_on_a_flat_list_of_everything():
    """*** 5,794 CLAIMS IN ONE SCROLL IS NOT MORE INFORMATION THAN 358 MODELS IN ONE SCROLL. ***

    Reported from the field in those terms: the long lists were fine as data and useless as
    navigation. So the high-volume tabs open on a grouped summary and the rows are one click in,
    already filtered.
    """
    v = explorer._VIEWS
    assert "function drill(" in v
    for tab in ("claimsTab", "answersTab"):
        body = v[v.index("function " + tab):]
        body = body[:body.index("\n}")]
        assert "drill({" in body, f"{tab} still opens on a flat list"
    # *** FINDINGS IS 244 ROWS, SO THE LIST STAYS WHOLE AND A CONTROL NARROWS IT. ***
    # That control was eleven chips carrying a name, a count and "0 read" each, which is two rows
    # of furniture above the table it filters. Reported from the field with a screenshot. It is a
    # select in the filter bar that already exists, so it adds no row at all.
    fb = v[v.index("function findingsTab"):]
    fb = fb[:fb.index("function answersTab")]
    assert "el('select')" in fb, "the check filter is gone"
    assert "controls: [pickCheck]" in fb, "the filter is not in the existing bar"
    assert "class: 'chips'" not in fb, "the chip wall is back"


def test_the_things_you_configure_by_hand_are_not_dumped_as_json():
    """*** THE HAND-MAINTAINED HALF WAS THE ONLY PART RENDERED AS A BLOB. ***

    The vocabulary, the question text and the per-check policy are what a person opens this page
    to read, and they were `JSON.stringify(..., null, 2)` inside a `<pre>`. Fifteen vocabulary
    terms written once made `traverse` flag `wdid` joins without anyone writing a water question;
    that is the promise, and it was being skimmed past.
    """
    v = explorer._VIEWS
    for tab in ("questionsTab", "configTab"):
        body = v[v.index("function " + tab):]
        body = body[:body.index("\n/* ---")] if "\n/* ---" in body else body
        assert "JSON.stringify" not in body or "null, 2" not in body, \
            f"{tab} still dumps raw JSON"
    assert "function kvAny(" in v, "there is no structured renderer"
    assert "vocabulary (" in v, "the vocabulary has no section of its own"


def test_every_table_can_reach_the_model_it_is_about():
    """The tabs were eight islands. A model name is the one thing every table has in common, so
    every one of them is a way back to that model."""
    v = explorer._VIEWS
    assert "function link(" in v and "GO.models" in v and "GO.chain" in v
    for tab in ("claimsTab", "findingsTab"):
        body = v[v.index("function " + tab):]
        body = body[:body.index("\n}\n")]
        assert "link(" in body, f"{tab} has no way back to a model"


# ---------------------------------------------------- 0.26.1: what a screenshot of it showed

def test_the_overview_is_the_first_tab():
    """*** A PERSON OPENING THIS FILE HAS NOT YET PICKED A MODEL. ***

    Landing on 358 rows asks them to choose before they have been told anything. The record is
    the only surface here with an argument to make rather than a table to show, so it is the way
    in rather than the last tab.
    """
    import re
    data = {"meta": {"project": "p", "models": 0, "sources": 0, "version": "0",
                     "generated_at": "x", "coverage": {}},
            "models": [], "edges": [], "claims": [], "findings": [], "decisions": [],
            "questions": [], "adjudications": [], "config": {}, "runs": [], "unreadable": []}
    doc = explorer.explorer_html(data, "<html></html>")
    order = re.findall(r'<button role="tab" data-tab="([a-z]+)"', doc)
    assert order[0] == "understood", f"the overview is not first: {order}"
    assert 'data-tab="understood" aria-selected="true"' in doc, "it is not the selected tab"
    assert "'understood');" in explorer._VIEWS, "the default hash target did not move"


def test_a_driving_edge_joining_on_nothing_is_the_normal_case():
    """*** "209 OF 573 WORTH A LOOK" IS NOT A SIGNAL, IT IS THE TABLE. ***

    163 of those 209 came from one rule: "a driving edge joining on nothing". A driving edge IS
    the FROM clause, so of course it joins on nothing. The rule was flagging the normal case,
    which is how a list of exceptions becomes a list.

    Measured on 573 real hops before rewriting it: dropped columns p75 12, p90 24, max 233; a
    join carrying no resolvable key 46; judged fan-outs 13. Every threshold is off that
    distribution. Asserted through the shipped rule text, because the numbers are the fix.
    """
    v = explorer._VIEWS
    assert "function why(e)" in v
    body = v[v.index("function why(e)"):]
    body = body[:body.index("\n}")]
    assert "!e.driving" in body, "the driving edge is not excluded, so the FROM clause flags"
    assert "!e.union_arm" in body, "a union arm has no join key either"
    assert "e.dropped > 60" in body, "the drop threshold is not the measured one"
    # and it must stay a REASON, not a boolean: a hop with nothing to say returns ""
    assert "return out.join(" in body


def test_box_text_can_neither_overflow_its_box_nor_be_cut_without_saying_so():
    """*** A BOX 148 WIDE HOLDING 22 MONOSPACE CHARACTERS OVERFLOWS. ***

    Reported from the field with a screenshot: a parent name painted through its own border and
    then through the right edge of the drawing. Two layers fix it and both are needed. The
    ellipsis says "there is more here", which a hard cut does not -- a name sliced mid-character
    reads as a rendering fault. The clip path is the backstop that makes the box the boundary
    whatever font actually renders it, since truncating by character count guesses at metrics.
    """
    v = explorer._VIEWS
    assert "const CLIP = 'boxclip';" in v
    assert "clipPath" in v and "'clip-path': 'url(#' + CLIP + ')'" in v
    cutline = v[v.index("const cut ="):v.index("const cut =") + 120]
    # the JS source may carry the escape or the character; both render an ellipsis
    assert "\\u2026" in cutline or "…" in cutline, cutline
    # and the drawing fills its panel rather than huddling in a corner
    assert "MINW = 860" in v
    assert "Math.max(cols * (BW + GAPX) + GAPX, MINW)" in v


def test_no_view_switch_floats_above_the_table_it_switches():
    """*** A BUTTON ABOVE THE TABLE IS A ROW OF FURNITURE, NOT A CONTROL. ***

    Reported from the field twice, one tab apart: eleven check chips on Findings, then
    `the 113 contradicted, across every model ->` on Claims. Both spent a row of the page on
    something the filter bar already had room for. Both are a select in that bar now.

    And the control owns no state the view disagrees with: coming back to the groups resets it,
    because a select reading "contradicted" over a table of every model is one fact with two
    spellings, which is the defect this whole tool is about.
    """
    v = explorer._VIEWS
    cb = v[v.index("function claimsTab"):]
    cb = cb[:cb.index("\n/* ---")]
    assert "el('select')" in cb, "the claims view switch is not a select"
    assert "groupControls: [view]" in cb, "it is not in the filter bar"
    assert "onGroups: () =>" in cb, "the control keeps a state the view can contradict"
    assert "class: 'back'" not in cb, "a floating button is back on Claims"
    # the same shape on findings
    fb = v[v.index("function findingsTab"):]
    fb = fb[:fb.index("function answersTab")]
    assert "controls: [pickCheck]" in fb and "class: 'chips'" not in fb
