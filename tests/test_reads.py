"""`assay read`: the judged reading `review --reads` takes, which nothing used to write."""
import json

import pytest
from typer.testing import CliRunner

from dbt_assay import cli, reads
from dbt_assay.cli import app
from dbt_assay.store import Store

runner = CliRunner()


def test_a_reading_uses_the_forms_own_verdicts_and_a_selected_reason():
    r = reads.reading({"answer": "misreads_the_sql", "confidence": 0.7,
                       "probabilities": {"misreads_the_sql": 0.6, "correct": 0.3,
                                         "cannot_tell": 0.1}})
    assert r["verdict"] == "disagree"
    crit = reads.bank()["criteria"]["misreads_the_sql"]["what"]
    assert " ".join(crit.split()) in r["why"], "the why is the option's own words"
    assert "0.60" in r["why"] and "correct at 0.30" in r["why"]
    assert set(reads.VERDICT_OF) == set(reads.bank()["criteria"]), "every option maps"


@pytest.fixture
def fake_jev(monkeypatch):
    """No key, no spend: `decide` answers `correct` for everything and counts its calls."""
    calls = []

    class C:
        available, calls, input_tokens, spent_usd, model = True, 0, 0, 0.0, "fake"

        def __init__(self, **_kw):
            pass

    def decide(_store, _client, rec, questions, **_kw):
        calls.append(rec.key)
        return {k: {"kind": "choice", "answer": "correct", "confidence": 0.9,
                    "probabilities": {"correct": 0.9, "cannot_tell": 0.1}} for k in questions}
    monkeypatch.setattr(cli, "Client", C)
    monkeypatch.setattr(cli, "decide", decide)
    monkeypatch.setattr(cli, "prefetch", lambda *_a, **_k: {"sent": 0})   # decide is the stub
    return calls


def _args(project_dir, tmp_path, out):
    return ["read", "--out", str(out), "-t", str(project_dir),
            "--store", str(tmp_path / "s.duckdb"), "--config", str(tmp_path)]


def test_dry_run_prices_and_writes_nothing(project_dir, tmp_path, fake_jev):
    out = tmp_path / "reads.json"
    r = runner.invoke(app, [*_args(project_dir, tmp_path, out), "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "card(s) to read" in r.output
    assert not out.exists() and not fake_jev


def test_it_writes_a_file_records_no_verdict_and_never_pays_twice(project_dir, tmp_path,
                                                                    fake_jev):
    out = tmp_path / "reads.json"
    r = runner.invoke(app, _args(project_dir, tmp_path, out))
    assert r.exit_code == 0, r.output
    doc = json.loads(out.read_text())
    assert doc and all(v["verdict"] == "agree" for v in doc.values())
    assert all("::" in k for k in doc), "keyed the way `review --reads` looks them up"
    s = Store(str(tmp_path / "s.duckdb"))
    assert s.con.execute("select count(*) from adjudications").fetchone()[0] == 0
    s.close()

    n = len(fake_jev)
    r = runner.invoke(app, _args(project_dir, tmp_path, out))
    assert r.exit_code == 0 and len(fake_jev) == n, "a card already read is not read again"


def test_the_file_lands_on_the_form(project_dir, tmp_path, fake_jev):
    out = tmp_path / "reads.json"
    runner.invoke(app, _args(project_dir, tmp_path, out))
    form = tmp_path / "form.html"
    r = runner.invoke(app, ["review", "--emit", str(form), "--target", str(project_dir),
                            "--store", str(tmp_path / "s.duckdb"), "--reads", str(out)])
    assert r.exit_code == 0, r.output
    assert "What the findings state is true" in form.read_text()


def test_a_reading_aid_does_not_move_a_finding_id():
    from dbt_assay.checks.structural import Finding
    a = Finding("models_disagree_about_a_column", "m", "m", "", "s", "",
                evidence={"column": "x", "one_of_each": ["1"]})
    b = Finding("models_disagree_about_a_column", "m", "m", "", "s", "",
                evidence={"column": "x", "one_of_each": ["2", "3"]})
    assert a.id == b.id


def test_a_dismissal_below_the_floor_is_suggested_as_unclear():
    """*** 47 PERMANENT DISMISSALS AT A MEDIAN CONFIDENCE OF 0.22. *** (25.1)"""
    r = reads.reading({"answer": "misreads_the_sql", "confidence": 0.22,
                       "probabilities": {"misreads_the_sql": 0.46, "correct": 0.39}})
    assert r["verdict"] == "unclear" and r["floored"] and r["answer"] == "misreads_the_sql"
    assert "below 0.5" in r["why"]
    # Only a DISMISSAL is held back: an unsure agreement removes nothing.
    ok = reads.reading({"answer": "correct", "confidence": 0.2, "probabilities": {}})
    assert ok["verdict"] == "agree" and "floored" not in ok


SQL = """with base as (
    select * from raw.owners
)
select *, row_number() over (partition by matched_name order by scraped_at desc) as rn
from base
qualify row_number() over (partition by id_business order by trank, officer_name) = 1"""


def _state():
    return {"model": "int_azcc_owners", "check": "arbitrary_pick", "sql": SQL,
            "findings": [{"summary": "dedupe on ['id_business'] whose tie-break may not be total",
                          "evidence": {"partition_by": ["id_business"],
                                       "order_by": ["trank", "officer_name"]}}]}


def test_the_locator_offers_only_lines_the_findings_name_and_a_way_out():
    q, lines = reads.locator(_state())
    opts = q["rests"]["criteria"]
    assert "none_of_these" in opts and "a_listed_line" not in opts
    texts = [v["text"] for v in lines.values()]
    assert any("id_business" in t for t in texts)
    assert not any(t.startswith("with base") for t in texts), "a line naming nothing"
    for k, v in lines.items():
        assert v["text"] in opts[k]["what"], "the option IS the line, copied"


def test_a_window_over_several_lines_is_one_candidate_and_a_comment_is_none():
    sql = ("-- id_business is unique per filing\n"
           "select *, row_number() over (\n"
           "    partition by id_business\n"
           "    order by trank) as rn\n"
           "from t")
    _q, lines = reads.locator({"sql": sql, "findings": [
        {"summary": "dedupe on `id_business`", "evidence": {}}]})
    texts = [v["text"] for v in lines.values()]
    assert texts == ["select *, row_number() over ( partition by id_business order by trank) as rn"]
    assert next(iter(lines.values()))["first"] == "select *, row_number() over ("


def test_the_chosen_line_is_placed_in_the_models_own_file_or_not_at_all():
    _q, lines = reads.locator(_state())
    key = next(k for k, v in lines.items() if "id_business" in v["text"])
    raw = "-- header\n" + SQL.replace("raw.owners", "{{ source('raw', 'owners') }}")
    r = reads.reading({"answer": "correct", "confidence": 0.8, "probabilities": {}},
                      {"answer": key, "confidence": 0.7}, lines, "models/int_azcc_owners.sql", raw)
    assert r["rests_on"]["line"] == 7 and "id_business" in r["rests_on"]["text"]
    # A line the file does not carry verbatim (a macro expanded it) gets no number, not a guess.
    r2 = reads.reading({"answer": "correct", "confidence": 0.8, "probabilities": {}},
                       {"answer": key, "confidence": 0.7}, lines, "m.sql", "select 1")
    assert r2["rests_on"]["line"] is None and r2["rests_on"]["text"]


def test_a_card_with_no_sql_asks_no_locator():
    assert reads.locator({"model": "raw.src", "findings": [], "sql": ""}) == (None, None)


def test_a_cached_pick_can_never_name_a_different_line():
    """Option keys come from the text, so a moved candidate list cannot re-point an old answer."""
    _q, a = reads.locator(_state())
    moved = dict(_state(), sql="select 1 as filler_id_business\n" + SQL)
    _q, b = reads.locator(moved)
    for k in set(a) & set(b):
        assert a[k]["text"] == b[k]["text"]
    r = reads.reading({"answer": "correct", "confidence": 0.8, "probabilities": {}},
                      {"answer": "line_0000000000", "confidence": 0.9}, b, "m.sql", SQL)
    assert "rests_on" not in r, "an answer naming no current line puts nothing on the card"


def test_a_cte_opener_is_not_joined_and_the_window_inside_it_survives():
    sql = ("with s as (\n"
           "    select parcel, price,\n"
           "        row_number() over (partition by parcel order by recordingdate desc) as rn\n"
           "    from raw.sales\n"
           ")\n"
           "select * from s where rn = 1")
    _q, lines = reads.locator({"sql": sql, "findings": [
        {"summary": "dedupe on `parcel`", "evidence": {"order_by": ["recordingdate DESC"]}}]})
    texts = [v["text"] for v in lines.values()]
    assert ("row_number() over (partition by parcel order by recordingdate desc) as rn"
            in texts), texts
    assert not any(t.startswith("with s as") and "row_number" in t for t in texts)


def test_comments_inside_a_window_do_not_split_it():
    sql = ("row_number() over (\n"
           "    partition by t.isf_key, e.which\n"
           "    -- NAME FIRST, THEN DISTANCE.\n"
           "    -- two more lines\n"
           "    -- of reasoning\n"
           "    order by case when n.nm = t.name then 0 else 1 end,\n"
           "             n.comid) as rn")
    _q, lines = reads.locator({"sql": sql, "findings": [
        {"summary": "dedupe on `isf_key`", "evidence": {}}]})
    texts = [v["text"] for v in lines.values()]
    assert len(texts) == 1 and texts[0].endswith("n.comid) as rn") and "--" not in texts[0]


def test_a_long_model_keeps_the_lines_its_findings_name():
    """*** 69 OF 328 FIELD MODELS RAN PAST THE CUT, AND THE WINDOW WAS OFTEN AFTER IT. ***"""
    from dbt_assay.subjects import _SQL_CHARS, _sql_excerpt
    filler = "\n".join(f"    , col_{i} as filler_{i}" for i in range(900))
    sql = ("select a" + "\n" + filler + "\nfrom t\nqualify row_number() over "
           "(partition by id_business order by trank) = 1")
    assert len(sql) > _SQL_CHARS
    got = _sql_excerpt(sql, {"id_business", "trank"})
    assert len(got) <= _SQL_CHARS and "partition by id_business" in got
    assert "-- assay:" in got and "line(s) not shown" in got
    assert _sql_excerpt("select 1", {"x"}) == "select 1", "a short model is sent whole"
