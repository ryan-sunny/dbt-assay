"""The explorer: the assembly layer, and the two defects that driving it in a DOM turned up.

Every test here exercises the real functions against a real store. A source grep has passed in
this repo while the thing it guarded was broken, more than once, so none of these read code.
"""
from __future__ import annotations

import json
import re

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
        "unconfigured": [], "effectiveness": [], "moved": {},
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
    # *** IT IS CARRIED, AND NO LONGER RENDERED IN THE PAGE. ***
    # The Overview renders every section the record had, natively, so showing it too was the same
    # numbers twice in one scroll -- its hero IS this page's hero. `record.html` still rides in
    # the artifact and `assay page --plain` still writes it; it is simply not duplicated inside
    # the page that replaced it.
    assert "srcdoc" not in explorer._VIEWS, "the record is back in an iframe"


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
            "adjudications": [], "config": {"provider": "auto"}, "runs": [],
            "unreadable": [], "unconfigured": [], "effectiveness": [], "moved": {},
            # A real ledger shape, so the round trip exercises the section rather than comparing
            # two empty dicts and passing for the wrong reason.
            "cost": {"usd": 0.0132, "input_tokens": 314265, "calls": 102, "output_tokens": 0,
                     "output_calls": 0, "calls_without_usage": 1,
                     "id_source": {"provider": 100, "reconstructed": 2},
                     "by_caller": [["assay.claims", 100, 314265, 0.0132]],
                     "by_family": [["sentence_is_a_claim", 100, 314265, 0.0132]],
                     "by_day": [["2026-09-22", 100, 314265, 0.0132]]},
            # A non-empty row, so the round trip is exercising the section rather than comparing
            # two empty lists and passing for the wrong reason.
            "suggestions": [{"section": "vocab", "key": "section_id", "headline": "h",
                             "measured": ["65 join hops"], "draft": "vocab:\n  section_id:",
                             "basis": "joined in many hops, absent from vocab", "rank": 1.0,
                             "decide": ""}],
            # Whether anything is WATCHING, which is a file somebody passed in rather than
            # anything derived from the store. A real shape, so the round trip exercises it.
            "monitoring": {"cadence": {"runs": 54, "explain": "54 writes over 79 days",
                                       "derived_staleness_days": 1, "floored": False,
                                       "configured": False},
                           "readings": [{"relation": "elementary_test_results", "state": "live",
                                         "rows": 10, "newest": "2026-09-19", "age_days": 1.0,
                                         "says": ""}],
                           "test_coverage": {"declared": 12, "ever_ran": 10,
                                             "skipped_results": 3},
                           "stale_failures": [], "unwatched": [], "monitoring": []},
            "areas": {"predicate_clusters": [{"size": 3, "shape": "<col> <> ''",
                                              "models": ["a", "b", "c"]}],
                      "odd_ones_out": [], "same_claim": [],
                      "claim_pairs_code_could_not_settle": 0}}


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

    # *** AND THE TWO SIDES MUST AGREE ON WHAT KEYS EXIST, NOT ONLY ON THIS FIXTURE. ***
    # A section added to `assemble` and not to the artifact round-trips to a page missing it; one
    # added to the reader and not the writer round-trips to a page with an empty section that
    # looks like a real answer. Both read as "there is nothing here".
    written = {n for n in explore._LINES} | {n for n, _ in explore._WHOLE}
    assert written <= set(data), f"the artifact writes sections assemble does not produce: {written - set(data)}"
    assert set(back) - {"record"} == written, "the reader and the writer disagree about sections"


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

    Measured on a 358-model warehouse: median 3 boxes in a neighborhood, p95 12, max 37. So the
    drawing is three bands and straight lines, and past `BAND_MAX` it degrades to a list, because
    36 boxes with 36 converging lines is the hairball the drawing exists to avoid.

    Asserted on the source constants and the degradation branch, and exercised end to end by the
    DOM driver in `scripts/`; a warehouse with a 36-parent model is what this is sized for.
    """
    import re
    v = explorer._VIEWS
    # *** THE THRESHOLD IS A REAL WALL, NOT A LAYOUT LIMIT, AND IT MOVED WHEN THAT CHANGED. ***
    # It was 9 because the SVG sized itself to the drawing and the container clipped whatever did
    # not fit -- so ten boxes had nowhere to go. The viewBox fits the content now and pans and
    # zooms, so the only remaining reason to stop drawing is that a picture of sixty converging
    # lines tells you nothing. Asserted as "there is a threshold and it degrades", because
    # pinning the number made a layout fix look like a regression.
    n = re.search(r"BAND_MAX = (\d+)", v)
    assert n, "the degradation threshold is gone"
    assert 5 <= int(n.group(1)) <= 200, f"BAND_MAX is {n.group(1)}, which is not a threshold"
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

def test_a_toggle_is_a_checkbox_and_not_a_button_with_two_labels():
    """A button whose label flips between two sentences makes you read it to find out which state
    you are in. A checkbox shows you. Reported from the field, and it is the same argument as
    every other one in this file: the control must not be able to disagree with the view."""
    v = explorer._VIEWS
    cb = v[v.index("function chainTab"):]
    cb = cb[:cb.index("\n/* ---")]
    assert "type: 'checkbox'" in cb, "the notable filter is still a button"
    assert "cb.onchange" in cb and "cb.checked" in cb
    assert "controls: [toggle," in cb, "it is not in the filter bar"


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


def test_the_view_switch_is_in_the_same_place_in_every_state():
    """*** IT WAS A BUTTON FLOATING OVER THE TABLE, THEN A SELECT, AND NOW IT IS A CHIP. ***

    The property has never changed: whatever switches the view lives where every other filter
    lives, is visible in every state, and can be undone from where it lands you. Reported from
    the field the first time: "I'd greatly prefer that dropdown to remain so it's flipping between
    the two, rather than different UI."

    It was a chip beside the group chips; the groups are a column of their own now, so it is a
    checkbox in the rows' filter bar, and it COMBINES with the picked group instead of replacing
    it. Picking `contradicted` from the old select threw away whichever model you were looking at.
    """
    v = explorer._VIEWS
    cb = v[v.index("function claimsTab"):v.index("function findingsTab")]
    assert "el('select')" not in cb, "the view switch is a dropdown again"
    assert "toggles:" in cb, "claims has no way to see only the contradicted ones"
    assert "c.contradicted != null" in cb

    db = v[v.index("function drill(opts)"):v.index("\nfunction conf(")]
    # A toggle filters the rows; it never swaps the list for a different one.
    assert "out = out.filter(p => t.where(p[0]))" in db
    # and it sits in the rows' own filter bar, where every other row filter is
    assert "controls: toggleBoxes" in db, "the toggle is not in the rows' filter bar"
    # and the group counts follow it, so a group with nothing contradicted says 0
    assert "rowsBefore(g).length" in db


def test_a_package_model_is_separated_by_owner_and_never_by_whether_it_parsed():
    """*** A FILTER ON `unreadable` WOULD HIDE THE ONE THING YOU WANT TO SEE. ***

    30 of 358 models on the field warehouse belong to an installed package. Every one is
    unreadable and they carry 541 columns of unknown provenance, all diluting numbers about the
    project somebody actually wrote. The tempting filter is "hide what did not parse", and it is
    wrong: a package's model failing to parse is not your problem, and one of YOURS failing to
    parse is the "you did not compile" signal.

    So the split is by owner, which dbt records exactly, and a model of your own can never be
    filtered away by it.
    """
    from dbt_assay.manifest import Model

    mine = Model(unique_id="m", name="a", path="p", layer="staging", schema="main",
                 materialized="view", description="", columns={}, meta={},
                 package="sunny_data", project="sunny_data")
    theirs = Model(unique_id="m2", name="b", path="p", layer="other", schema="main",
                   materialized="view", description="", columns={}, meta={},
                   package="elementary", project="sunny_data")
    assert not mine.is_installed_package
    assert theirs.is_installed_package
    # unreadable is orthogonal: yours stays yours
    assert not mine.readable and not mine.is_installed_package

    # a manifest with no package_name must not start calling everything a package
    silent = Model(unique_id="m3", name="c", path="p", layer="staging", schema="main",
                   materialized="view", description="", columns={}, meta={})
    assert not silent.is_installed_package, "an unknown owner is not a package"

    v = explorer._VIEWS
    assert "const mine = m => showPackaged || m.yours;" in v
    assert "where: m => mine(m)" in v, "the models list does not default to your own"


def test_every_root_the_parser_resolved_gets_a_class():
    """*** A ROOT IS AN ANSWER, SO A COLUMN CARRYING ONE IS NEVER "UNKNOWN". ***

    Measured on 358 models: 233 columns read `unknown` while holding a perfectly good root --
    `filter` 91, `null` 34, `ignorenulls` 20, `paren` 19, then `gt`, `not`, `dpipe`, `div`,
    `like`, `subquery`. The parser had answered and the classifier had no case for the answer, so
    it said "could not resolve where this came from" about an expression it was holding.

    `unknown` now means one thing: the value passes through and nothing says from where.
    """
    from dbt_assay.provenance import _root_class

    assert _root_class("literal") == "constant"
    assert _root_class("null") == "null_placeholder"          # union padding, not a constant
    assert _root_class("cast:null") == "null_placeholder"
    assert _root_class("coalesce:literal") == "defaulted"
    assert _root_class("window:row_number") == "ranked"
    assert _root_class("ignorenulls") == "ranked"
    assert _root_class("agg:sum") == "aggregated"
    assert _root_class("filter") == "aggregated"              # an aggregate's own clause
    assert _root_class("func:round") == "computed"
    # the long tail: anything the parser named is computed, never unknown
    for root in ("paren", "gt", "not", "dpipe", "bracket", "div", "like", "eq", "subquery"):
        assert _root_class(root) == "computed", root
    # and only a passthrough is left for the origin logic to settle
    assert _root_class("column") is None
    assert _root_class("star") is None
    assert _root_class("") is None


def test_a_star_is_attributed_only_when_one_parent_offers_the_name(tmp_path):
    """*** `select *` GIVES THE NAMES AND LOSES WHERE EACH CAME FROM. ***

    236 columns on the field warehouse, 71 of the 73 on one mart, read `unknown` purely because
    the projection was a star over several relations. The parents' column lists are already
    assembled for sqlglot, so the parent offering the name is a lookup.

    AND IT ANSWERS ONLY WHEN EXACTLY ONE DOES. Two parents publishing `wdid` is genuinely
    ambiguous, and picking the first is `first_match_pick`, which is a check this tool runs
    against other people's SQL.
    """
    from dbt_assay.provenance import _parents_offering

    class _Schema:
        def __init__(self):
            self.cols = {"p1": ["a", "shared"], "p2": ["b", "shared"]}

        def columns(self, uid):
            return type("C", (), {"names": self.cols.get(uid, [])})()

    class _Project:
        def __init__(self):
            self.models = {"child": type("M", (), {"parents": ["p1", "p2"]})()}

    proj, sch = _Project(), _Schema()
    assert _parents_offering("child", "a", proj, sch) == ["p1"]
    assert _parents_offering("child", "b", proj, sch) == ["p2"]
    assert _parents_offering("child", "shared", proj, sch) == ["p1", "p2"], "ambiguity is a LIST"
    assert _parents_offering("child", "nowhere", proj, sch) == []


def test_a_column_list_containing_a_star_is_not_a_column_list():
    """When qualify cannot expand a star, the fallback was the raw output columns -- which still
    hold the literal `*`. Downstream that is a column NAMED `*` while every real column of the
    model is absent, so its descendants report unknown provenance and no test on it can be
    evaluated. Three models on the field warehouse, 131 unknown columns among their children."""
    import inspect

    from dbt_assay import infer

    src = inspect.getsource(infer.derive_columns)
    assert 'if cols and "*" in cols:' in src, "the unexpanded star is published as a column"
    assert "star_unexpanded" in src, "the fallback is silent"
    assert '_catalog_columns' in src.split('if cols and "*" in cols:')[1][:400], \
        "it does not fall back to the catalog, which knows the real names"


def test_an_empty_column_on_every_row_says_why_rather_than_printing_nothing():
    """Every one of 5,656 columns had no role, because `assay columns` has never run there.
    Printing "not settled" 5,656 times says the same thing as a check that found nothing."""
    v = explorer._VIEWS
    assert "const HAS_ROLES =" in v
    assert "if (HAS_ROLES) colCols.push(" in v, "the role column is shown even when empty"
    assert "nothing has asked" in v, "it does not name the reason"
    assert "assay columns" in v, "it does not name the command that would fill it"


def test_clicking_a_node_opens_a_card_and_never_types_into_the_filter():
    """*** GOING TO A MODEL USED TO WORK BY TYPING ITS NAME INTO THE SEARCH BOX. ***

    Which left the list showing one row and the box full of text somebody had to clear by hand
    before they could see anything else, and it happened on every node click, so reading the graph
    walked you out of the graph. Reported from the field: "clicking a node adds it to the search
    thing and just FUCKS the ui... ideally if you're clicking a node it's just a popup right there
    with the relevant info."

    Two separate rules now. A node click opens a CARD where the node is. Navigation is a second,
    deliberate click, and even that never touches what the user typed: the detail is authoritative
    and the list is only an index into it.
    """
    v = explorer._VIEWS
    assert "function nodeCard(" in v, "a node click has no card"
    assert "nodeCard(ev.currentTarget," in v, "the boxes do not open it"
    # nothing anywhere sets the value of a search input
    assert "input[type=search]'); s.value" not in v, "navigation still types into the filter"
    assert ".value = name" not in v, "something still types a model name into a control"
    assert "function highlight(panel, name)" in v, "there is no way to mark a row without filtering"
    go = v[v.index("GO.chain = name =>"):]
    go = go[:go.index("};")]
    assert "show(m)" in go and "highlight(" in go, go


def test_the_card_says_what_the_model_is_before_you_go_there():
    """The whole point of not navigating is that you can decide from where you are."""
    v = explorer._VIEWS
    card = v[v.index("function nodeCard("):]
    card = card[:card.index("\nfunction lineage(")]
    for want in ("grain", "reads", "read by", "findings", "claims"):
        assert f"'{want}'" in card, f"the card does not say {want}"
    assert "m.description" in card, "the card does not say what the model is"
    # a node can be a SOURCE, which has no model entry, and that must not render an empty card
    assert "A source, or a relation outside this project" in card
    assert "center the graph here" in card and "open in Models" in card


def test_a_claim_row_says_which_model_it_belongs_to():
    """*** A LIST OF 5,794 SENTENCES WITH NO OWNER IS A FILING CABINET. ***

    This used to be a screen of model names you clicked into. It is one list now, so every row
    has to carry its model -- otherwise the chips are the only thing saying whose claim you are
    reading, and they are a filter rather than a label.
    """
    v = explorer._VIEWS
    cb = v[v.index("function claimsTab"):v.index("function findingsTab")]
    assert "label: 'model'" in cb and "link(c.subject_name)" in cb, \
        "the row no longer names, or no longer links, its model"
    # and the chip is the model, so filtering to one is one click
    assert "chip: g => g.model" in cb


_CLAMP_RULE = (
    "Math.max(pad, Math.min(cx - w / 2, vw - w - pad))",
    "if (top + h + pad > vh) top = Math.max(pad, below - h - 8);",
    "if (top + h + pad > vh) top = pad;",
)


def test_the_card_cannot_be_cut_off_by_anything():
    """*** A CARD INSIDE A SCROLLING BOX GETS CUT BY THE SCROLLING BOX. ***

    It was absolutely positioned inside `.linwrap`, which needs `overflow-x: auto` for a wide
    graph, so a node near the left edge had half its card clipped away. Reported with a
    screenshot: "it gets cut off bruh it needs to fit in the window".

    `position: fixed` on the body escapes every ancestor's overflow, so the only thing left that
    can cut it is the viewport. The clamp is pure arithmetic and is asserted as arithmetic, in
    the positions that actually failed, rather than by hoping a browser agrees.
    """
    v = explorer._VIEWS
    assert "function clampToViewport(" in v
    assert "document.body.append(pop)" in v, "the card is still inside the drawing"
    assert "position:fixed" in explorer.CSS.replace(" ", ""), "the card is not fixed"

    # The arithmetic, restated and run. It is duplicated on purpose and the duplication is
    # checked: `_CLAMP_RULE` below must appear in the shipped source, so a change to one without
    # the other fails here rather than in a browser.
    for line in _CLAMP_RULE:
        assert line in v, f"the shipped clamp no longer does: {line}"

    def clamp(w, h, cx, below, vw, vh, pad=12):
        left = max(pad, min(cx - w / 2, vw - w - pad))
        top = below
        if top + h + pad > vh:
            top = max(pad, below - h - 8)
        if top + h + pad > vh:
            top = pad
        return round(left), round(top)

    W, H, VW, VH = 360, 300, 1200, 800
    for label, cx, below, vh in [("far left", 20, 400, VH), ("middle", 600, 400, VH),
                                 ("far right", 1190, 400, VH), ("no room below", 600, 700, VH),
                                 ("no room either way", 600, 700, 350)]:
        left, top = clamp(W, H, cx, below, VW, vh)
        assert left >= 0 and left + W <= VW, f"{label}: cut off horizontally at {left}"
        assert top >= 0, f"{label}: off the top at {top}"


def test_a_card_on_the_body_does_not_outlive_what_it_points_at():
    """It sits outside the panel, so nothing removes it when that panel changes. Scrolling moves
    the node out from under it and switching tabs replaces everything it described."""
    v = explorer._VIEWS
    assert "function dismissCards()" in v
    for path in ("'Escape'", "'scroll'", "'resize'", "b.onclick = () => { dismissCards();"):
        assert path in v, f"no dismiss on {path}"
    # and a click inside the card must NOT dismiss it, or its own buttons could never be used
    assert "closest('.pop')" in v, "a click on the card's own buttons would dismiss it first"


def test_the_pane_is_never_empty_and_one_click_changes_it():
    """*** THREE SCREENS TO READ ONE THING, TWO OF THEM SHOWING "PICK SOMETHING". ***

    Reported exactly as it deserved: "what in the fuck was your decision process when you decided
    this 3 step process was necessary to get any information... it needs to be rendering shit on
    the right after a single click ALWAYS none of this empty bs."

    So: the grouping is a filter rather than a screen, the list underneath is always the leaves,
    and the right-hand pane is filled on arrival with the first row. One click changes what is on
    the right, and there is never a second one.
    """
    v = explorer._VIEWS
    db = v[v.index("function drill(opts)"):v.index("\nfunction conf(")]
    # no intermediate screen: there is one grid, and its pick goes straight to the detail
    assert db.count("grid(") == 1, "drill builds more than one table, so it is still two screens"
    assert "pick: p => showOne(p[0], p[1])" in db
    # the pane is filled on arrival, and by CLICKING the row, so the list shows what is selected
    assert "const first = $('tbody tr', list);" in db
    assert "if (first) first.click();" in db, "the pane opens empty"
    # and no caller is left able to ask for the old empty state
    assert "Pick a" not in db, "the pane can still tell somebody to pick something"


def test_the_overview_picks_its_form_from_the_data_and_its_color_last():
    """*** THE PAGE'S OWN PILL COLORS FAILED THE VALIDATOR AS A CHART PALETTE. ***

    green `#5a6a2f` vs blue `#2b5c7a` measure dE 14.1 for normal vision, under the 15 floor:
    fine as small text beside a word, genuinely hard to separate as adjacent bars. So they are
    not reused for marks.

    Grain is ORDINAL -- declared beats derived beats judged beats nothing -- so it is one hue
    dark to light, which puts the ordering in the ink instead of a key. Checked monotonic in
    OKLab lightness (.433 / .575 / .764) rather than eyeballed. Status colors are reserved, never
    a series, and always carry their label, because `warning` is sub-3:1 on this surface by
    design.
    """
    v = explorer._VIEWS
    assert "const RAMP = {declared: '#4a443d', derived: '#a8491a', judged: '#d2833a'" in v, \
        "the ordinal ramp is gone"
    assert "'#5a6a2f'" not in v and "'#2b5c7a'" not in v, "the failing pill colors are used as marks"
    # status never appears without its label
    ov = v[v.index("function understoodTab"):]
    assert "would FAIL the build" in ov and "queued for a person" in ov
    # a ranking is one hue: the fallback is ONE constant, never a colour picked per row
    assert "r.color || BAR" in v, "ranked bars stopped using a single hue"
    assert "const BAR = RAMP.derived;" in v, "the single hue is no longer one of the ramp's own"


def test_no_chart_distorts_its_own_labels_or_clips_its_names():
    """*** TWO GEOMETRY BUGS A DOM DRIVER CANNOT SEE. ***

    An SVG bar that fills its container needs `preserveAspectRatio="none"`, which stretches the
    TEXT inside it. And a fixed label gutter has to be guessed: three check names exceed 200px at
    12px monospace, `description_contradicts_the_code` at 230px, so they ran off the left.

    Both are HTML now -- flex for the stack, a grid for the ranking -- where the browser measures
    what the author would otherwise have to predict.
    """
    v = explorer._VIEWS
    # Scoped to the Overview's charts. The LINEAGE drawing is a real SVG and legitimately sets
    # preserveAspectRatio; only these two must not, because only these two hold text that scales.
    charts = v[v.index("/* A composition of a known whole"):v.index("function tile(")]
    # `createElementNS` is the whole assertion: no SVG means no viewBox, so no scaling factor and
    # nothing that can stretch text. Checking for `preserveAspectRatio` by name would only catch
    # the comment that explains why it is absent -- the record, not the behavior.
    assert "createElementNS" not in charts, "the Overview charts went back to SVG"
    assert "preserveAspectRatio" in charts, "the reason it is not SVG stopped being written down"
    assert ".sbar{display:flex" in explorer.CSS.replace(" ", "").replace("\n", "") or \
           ".sbar{display:flex" in explorer.CSS
    assert ".rank{display:grid" in explorer.CSS, "the ranking has no self-measuring gutter"
    # and a label only where one fits, never a number on every mark
    assert "if (pct_ > 7)" in v, "every segment is labelled regardless of width"


def test_every_mark_carries_its_own_numbers():
    """An HTML chart IS interactive; a mark you cannot interrogate is a picture of a number."""
    v = explorer._VIEWS
    sb = v[v.index("function stackedBar"):v.index("function rankedBars")]
    assert "title:" in sb, "a stacked segment has no hover"
    rb = v[v.index("function rankedBars"):v.index("function tile")]
    assert "title: r.tip" in rb, "a ranked row has no hover"


def test_the_page_reaches_out_to_nothing(tmp_path):
    """*** SELF-CONTAINED IS THE WHOLE DELIVERY MODEL. ***

    It is opened from disk, mailed, moved between directories and committed. Anything fetched at
    render time is a thing that is missing the first time one of those happens, and on `file://`
    a blocked request fails silently rather than loudly. Asserted on the SHELL rather than the
    whole file, because a warehouse's own content may legitimately contain a URL.
    """
    data = {"meta": {"project": "p", "models": 0, "sources": 0, "version": "0",
                     "generated_at": "x", "coverage": {}},
            "models": [], "edges": [], "claims": [], "findings": [], "decisions": [],
            "questions": [], "adjudications": [], "config": {}, "runs": [], "unreadable": [],
            "unconfigured": [], "effectiveness": [], "moved": {}}
    doc = explorer.explorer_html(data, "<html></html>")
    shell = doc.split('<script id="assay-data"')[0]
    # *** AN `xmlns` IS AN IDENTIFIER, NOT A REQUEST. ***
    # The inline mark declares the SVG namespace, which no browser has ever fetched. Stripping it
    # keeps the guard pointed at things that actually go out over a wire.
    shell = shell.replace('xmlns="http://www.w3.org/2000/svg"', "")
    for scheme in ("http://", "https://", "//cdn", "fetch(", "XMLHttpRequest"):
        assert scheme not in shell, f"the page shell reaches out to {scheme}"
    # *** THE GUARD IS ABOUT THE WIRE, NOT ABOUT THE TAG. ***
    # It forbade `<img` outright, which was right while the page had no pictures. The plates are
    # `data:` URIs and fetch nothing; an `<img src="cuts/x.gif">` is the thing that breaks the
    # moment somebody emails the file. So: every img must carry a data URI, and no img may name
    # a path.
    imgs = re.findall(r'<img[^>]*>', shell)
    for tag in imgs:
        src = re.search(r'src="([^"]{0,24})', tag)
        assert src and src.group(1).startswith("data:"), f"an image is fetched: {tag[:70]}"

    # And the favicon is the mark's own bytes, not a file beside the page.
    assert 'rel="icon"' in doc
    from dbt_assay import assets
    assert assets.FAVICON in doc, "the favicon is not embedded"



def test_every_css_variable_the_page_uses_is_defined():
    """An undefined `var(--x)` drops the whole declaration, silently.

    *** THIS IS THE GUARD-THAT-CANNOT-SEE SHAPE, IN CSS. ***
    A border written against a token that does not exist is not an error and does not warn: the
    declaration is discarded and the element renders without it, looking like a deliberate choice.
    Caught here first time out, on three tokens invented for a new panel (`--accent`, `--warn`,
    `--panel`) that this page has never had.
    """
    import re
    from pathlib import Path

    from dbt_assay import explorer
    src = Path(explorer.__file__).read_text()
    # *** TO THE CLOSING BRACE, NOT TO A FIXED 400 CHARACTERS. ***
    # The reader used to slice a fixed length, so a `:root` block that grew past it dropped its
    # last tokens and reported them as undefined. A guard that can only see part of the thing it
    # guards is the shape this whole file exists to catch.
    start = src.index(":root{")
    root = src[start:src.index("}", start)]
    defined = set(re.findall(r"--([a-z0-9-]+)\s*:", root))
    assert len(defined) > 5, "the token reader found almost nothing; it is broken"
    used = set(re.findall(r"var\(--([a-z0-9-]+)\)", src))
    assert used, "the usage reader found nothing; it is broken"
    assert not (used - defined), sorted(used - defined)


def test_a_whole_number_in_a_table_is_grouped():
    """*** 45806589 IS NOT A NUMBER ANYBODY READS. ***

    The prose on the page has always grouped its thousands and the TABLES never did, so a token
    count, a row count and a model count all arrived as a run of digits you count with a finger.
    """
    assert "const cellText" in explorer.JS, "the table cell formatter is gone"
    # the default path must route through it rather than through String()
    assert "text: cellText(c.val(r))" in explorer.JS, \
        "a table cell is being rendered with String() again, which drops the separators"


def test_a_fraction_in_a_table_is_left_alone():
    """*** AND GROUPING MUST NOT ROUND A PROBABILITY. ***

    `toLocaleString` caps at three fraction digits by default, so running a confidence through it
    turns 0.8712 into 0.871 -- a displayed number that is not the stored one, on the page whose
    whole argument is that a displayed number is the stored one. Whole numbers only.
    """
    js = explorer.JS
    i = js.index("const cellText")
    decl = js[i:i + 260]
    assert "Number.isInteger" in decl, decl


def test_a_caption_says_what_a_number_means_and_then_stops():
    """*** THE PAGE HAD STARTED EXPLAINING ITSELF. ***

    "A good release makes it look worse. That is the design working." is a sentence about assay,
    on a page about somebody's warehouse, above a number they were trying to read. Four captions
    ran past 200 characters and argued for their own design choices -- one of them explained why
    the bars were a single hue.

    A caption earns its place by saying what the number means or how to read it. The reasoning
    belongs in the source, where it already is, and in the docs.
    """
    import re
    caps = [c.strip("'") for c in
            re.findall(r"class: 'note', text: ('(?:[^']|\\')*')", explorer._VIEWS)]
    assert len(caps) >= 3, "the caption reader found almost nothing; it is broken"
    long = [c for c in caps if len(c) > 140]
    assert not long, f"captions that have started explaining themselves again: {long}"
    # The explanations moved into tips, and a tip is a few sentences, not an essay either.
    tips = re.findall(r"tip: '((?:[^'\\]|\\.)*)'", explorer._VIEWS)
    assert len(tips) > 10, "the tip reader found almost nothing; it is broken"
    essays = [t for t in tips if len(t) > 420]
    assert not essays, f"tips that have become essays: {essays}"


def test_the_lineage_can_be_moved_around():
    """*** YOU COULD SEE WHAT FIT AND NOTHING ELSE. ***

    The SVG sized itself to the drawing inside a narrower container, so a model with five parents
    lost the fifth off the right edge and there was no way to reach it.
    """
    v = explorer._VIEWS
    assert "function panZoom" in v, "the lineage cannot be moved"
    for need in ("pointerdown", "pointermove", "wheel", "preserveAspectRatio"):
        assert need in v, need
    assert "setAttribute('viewBox'" in v, "zooming does not move the viewBox"
    # and the element fills its box rather than dictating it
    assert "width: W, height: H" not in v, "the svg is sizing itself to the drawing again"


def test_every_tab_gets_the_same_pane_geometry():
    """*** FOUR TABS SHARED THE TEMPLATE AND NONE WERE THE SAME SIZE. ***

    Both panes were `max-height`, so each shrank to its own content: the right pane was tall on
    one tab and short on another, and because the tall one grew the page, the left list scrolled
    long past the end of itself into blank screen.
    """
    css = explorer.CSS
    # *** AND THE HEIGHT IS MEASURED, NOT COUNTED ONCE BY HAND. ***
    # It was `calc(100vh - 210px)`, where 210 was header plus nav plus footer plus the paddings
    # between them. Driven in a real browser against a real 12MB page it was out by 12px, so
    # every tab scrolled the document a little -- which is the complaint, and the next time the
    # header gains a line the constant is wrong again. The body is a flex column: the header and
    # footer take what they need, `main` takes the rest, and nothing has to add up.
    assert "--pane-h" not in css, "the magic constant is back"
    body = css[css.index("body{"):css.index("}", css.index("body{"))]
    assert "flex-direction:column" in body and "height:100vh" in body and "overflow:hidden" in body
    main = css[css.index("main{"):css.index("}", css.index("main{"))]
    assert "flex:1 1 auto" in main and "min-height:0" in main, (
        "main does not take the space the header and footer leave")
    assert ".panel{height:100%;overflow:auto}" in css, (
        "the panel does not own the scroll, so the document grows behind the sticky header")
    two = css[css.index(".wrap2{"):css.index("}", css.index(".wrap2{"))]
    assert "align-items:stretch" in two and "height:100%" in two, (
        "the two panes do not fill the same box")


def test_every_group_is_listed_and_every_row_is_reachable():
    """*** "+343 MORE, USE THE FILTER" HID 343 MODELS AND 19 QUESTION FAMILIES. ***

    The groups were a chip row capped at eight, so a family of 3,543 answers could be found only
    by knowing its name first, and the rows were capped at 4,000 of 5,820. Reported with
    screenshots: "you cant even select some of em ... which is awful UI design". The groups are a
    column now, every one of them with its full name and count, and the rows are paged, so every
    row is reachable from a button rather than from a guess typed into a box.
    """
    v = explorer._VIEWS
    db = v[v.index("function drill(opts)"):v.index("\nfunction conf(")]
    assert "sorted.slice(0, 8)" not in db and "more, use the filter'" not in db, "the groups are capped again"
    assert "label.slice(0, 29)" not in db, "group names are cut again"
    assert "class: 'gnav'" in db and "class: 'glist'" in db
    assert "gq.oninput" in db, "the groups have no filter of their own"
    # the rows page instead of stopping
    assert "page: opts.pageSize || 200" in db
    js = explorer.JS
    g = js[js.index("function grid("):js.index("function mdInline(")]
    assert "shown = view.slice(pg * opts.page, (pg + 1) * opts.page)" in g
    assert "prev.disabled = pg === 0" in g
    # a family is a distribution before it is a list: Answers shows how its answers split
    ab = v[v.index("function answersTab"):v.index("function kvAny")]
    assert "facet: {label: 'answered'" in ab


def test_a_long_cell_wraps_and_is_never_cut():
    """*** CUT WITH "..." OR SCROLLED SIDEWAYS, AND BOTH WERE REPORTED. ***

    "not very helpful when all the shit is just like cut off", and of the Findings table: "side
    scroll in this table isnt something i really want". A cell wraps, and a long name breaks at
    its own separators through `<wbr>`, which is not text, so `textContent` is still the name.
    """
    css = explorer.CSS
    clip = css[css.index("td.clip{"):]
    clip = clip[:clip.index("}")]
    assert "ellipsis" not in clip and "nowrap" not in clip, "a clipped cell is cut again"
    js, v = explorer.JS, explorer._VIEWS
    assert "function wbr(" in js
    assert "document.createElement('wbr')" in js, "the break is text, so copying a name breaks it"
    lk = v[v.index("function link("):]
    lk = lk[:lk.index("\n}")]
    assert "wbr(name)" in lk, "a model name in a table cannot wrap"


def test_the_areas_tab_counts_all_three_lists_and_shows_one_at_a_time():
    """*** THE TAB SAID 16 AND THE PAGE HELD 16, 6 AND 39. ***

    The number beside Areas was the first list's, while the same scroll also held "one claim,
    several models (39)" under a heading written the same way. Reported as "kinda confusing? def
    gets lost". The three lists are the groups of the one navigator every high-volume tab uses,
    and the tab's number is all of them.
    """
    import re
    data = {"meta": {"project": "p", "models": 0, "sources": 0, "version": "0",
                     "generated_at": "x", "coverage": {}},
            "models": [], "edges": [], "claims": [], "findings": [], "decisions": [],
            "questions": [], "adjudications": [], "config": {}, "runs": [], "unreadable": [],
            "areas": {"predicate_clusters": [{}] * 16, "odd_ones_out": [{}] * 6,
                      "same_claim": [[{}]] * 39}}
    doc = explorer.explorer_html(data, "<html></html>")
    n = re.search(r'data-tab="areas"[^>]*>Areas<b>([\d,]+)</b>', doc)
    assert n and n.group(1) == "61", f"Areas says {n and n.group(1)}, not 16 + 6 + 39"

    v = explorer._VIEWS
    ab = v[v.index("function areasTab"):v.index("function findingsTab")]
    assert "drill({" in ab, "Areas is a stacked scroll again"
    assert "all: false" in ab, "an 'all' group would mix three lists with different columns"
    assert "colsFor: g => g.cols" in ab


def test_weight_is_shown_with_the_parts_it_is_computed_from():
    """*** "weight 7.6" WITH NO FORMULA ANYWHERE ON THE PAGE. ***

    The parts are computed by the same method the weight is, so the page cannot show a sum that
    disagrees with the order it ranks by, and they are shown in the finding, on the cell's hover
    and in the tab's opening line.
    """
    from dbt_assay.checks.structural import Finding
    f = Finding(check="c", subject="s", subject_name="m", file="f", summary="x", detail="y",
                base=3, descendants=18, marts=12, exposures=["a product"])
    p = f.weight_parts()
    assert p["counts"] == {"descendants": 18, "marts": 12, "exposures": 1}
    assert p["parts"] == {"descendants": 18 / 25, "marts": 10 / 5, "exposures": 5}, \
        "marts are counted up to 10"
    assert abs(f.weight - 3 * (1 + 0.72 + 2 + 5)) < 1e-9
    assert abs(f.weight - p["base"] * p["lift"]) < 1e-9

    import inspect

    from dbt_assay import explore
    assert '"weight_parts": f.weight_parts()' in inspect.getsource(explore._findings), \
        "the page is not handed the parts"
    v = explorer._VIEWS
    fb = v[v.index("function findingsTab"):v.index("function answersTab")]
    assert "['weight', weightBox(f)]" in fb, "the finding shows a bare weight again"
    assert "title: weightLine(f)" in fb, "the weight cell has no parts on hover"


def test_a_page_rendered_from_its_artifact_is_the_same_page(project_dir, tmp_path):
    """*** THE ROUND-TRIP GUARD COULD NOT SEE A MISSING SECTION. ***

    It compared the artifact with a hand-written fixture, and the fixture had no `areas` either,
    so `--from` shipped a page with no Areas tab while the guard passed. This one runs the real
    commands: the page from a store, then the page from the artifact that run wrote, and the two
    files must be identical. Any section `assemble` adds and the artifact drops fails here.
    """
    from typer.testing import CliRunner

    from dbt_assay.cli import app
    run = CliRunner()
    store = tmp_path / "s.duckdb"
    r = run.invoke(app, ["check", "--target", str(project_dir), "--store", str(store)])
    assert r.exit_code in (0, 1), r.output
    first, again = tmp_path / "a.html", tmp_path / "b.html"
    r = run.invoke(app, ["page", str(first), "--target", str(project_dir), "--store", str(store),
                         "--data", str(tmp_path / "art")])
    assert r.exit_code == 0, r.output
    r = run.invoke(app, ["page", str(again), "--from", str(tmp_path / "art")])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "art" / "areas.json").exists(), "the artifact has no areas"
    assert first.read_text() == again.read_text(), \
        "the page rendered from its own artifact differs from the page that wrote it"


def test_no_tab_opens_on_a_grey_sentence():
    """*** "THEYRE ALL NOT BEING READ ITS RANDOM TEXT AT THE TOP". ***

    Every tab opened on a grey paragraph saying what it was, sections carried a grey subtitle and
    every tab ended on an italic footer. None of it was read. What a tab is lives on the tab as a
    tip, a section's explanation is its heading's tip, a column's is its header's, and the only
    sentences left as text are facts about this warehouse.
    """
    v = explorer._VIEWS
    assert "blurb" not in v, "a tab still opens on a sentence"
    assert "note tabhead" not in v
    assert "drilltop" not in v
    blk = v[v.index("function block("):]
    blk = blk[:blk.index("\n}")]
    assert "tip: tipText" in blk and "class: 'note'" not in blk, "a section note is a caption again"
    data = {"meta": {"project": "p", "models": 0, "sources": 0, "version": "0",
                     "generated_at": "x", "coverage": {}},
            "models": [], "edges": [], "claims": [], "findings": [], "decisions": [],
            "questions": [], "adjudications": [], "config": {}, "runs": [], "unreadable": []}
    doc = explorer.explorer_html(data, "<html></html>")
    assert "<footer" not in doc, "the footer is back"
    import re
    tabs = re.findall(r'<button role="tab" data-tab="([a-z]+)"[^>]*data-tip="([^"]+)"', doc)
    assert len(tabs) >= 10, f"tabs without a tip saying what they are: {tabs}"


def test_monitoring_is_a_navigator_and_no_list_is_capped():
    """*** SIX SECTIONS STACKED ON ONE SCROLL, AND THE LONGEST CUT AT 400. ***

    Monitoring was on the list of tabs that "need some love to support the higher volume". Its
    sections are the groups of the navigator the other high-volume tabs use, every list is paged,
    and the facts that were paragraphs are the rows of an "at a glance" group.
    """
    v = explorer._VIEWS
    mb = v[v.index("function monitoringTab"):]
    mb = mb[:mb.index("\n}\n")]
    assert "drill({" in mb, "Monitoring is a stacked scroll again"
    for key in ("'glance'", "'monitors'", "'stale'", "'findings'", "'unwatched'"):
        assert "key: " + key in mb, f"the {key} group is gone"
    assert "cap: 400" not in mb, "the unwatched models are capped again"
    assert "class: 'note'" not in mb, "a grey note is back"
