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
