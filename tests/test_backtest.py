from dbt_assay import backtest
from dbt_assay.backtest import Replay


def test_statement_level_jinja_is_removed_not_substituted():
    """`{{ config(...) }}` became a bare `1`, which parses as a complete expression and then
    collides with the `with` that follows. 46 of 75 replays died on exactly that."""
    out = backtest.dejinja("{{ config(materialized='table') }}\n-- c\nwith x as (select 1) "
                           "select * from x")
    assert "1\n" not in out.lstrip()[:3]
    from dbt_assay.parse import digest
    assert digest(out).ok


def test_refs_and_sources_become_relation_names():
    out = backtest.dejinja("select * from {{ ref('stg_a') }} a "
                           "join {{ source('raw', 't') }} b on 1=1")
    assert "stg_a" in out and "raw.t" in out and "{{" not in out


def test_an_inline_macro_keeps_the_statement_parseable():
    from dbt_assay.parse import digest
    out = backtest.dejinja("select {{ dbt_utils.surrogate_key(['a','b']) }} as k from t")
    assert digest(out).ok


def test_a_blob_that_cannot_be_parsed_is_skipped_never_counted_clean():
    r = Replay(sha="a", subject="s", model="m", path="m.sql",
               skipped="could not parse after a Jinja strip: ParseError")
    assert r.verdict == "unparseable"
    t = backtest.tally([r])
    assert t.get("caught", 0) == 0
    assert t["_had_something"] == 0 and t["_silenced_rate"] is None


def test_the_verdicts_read_the_transition_not_the_message():
    fired_then_quiet = Replay("a", "s", "m", "m.sql", before={"x"}, after=set())
    quiet_then_fired = Replay("a", "s", "m", "m.sql", before=set(), after={"x"})
    both = Replay("a", "s", "m", "m.sql", before={"x"}, after={"x"})
    neither = Replay("a", "s", "m", "m.sql")
    assert fired_then_quiet.verdict == "caught"
    assert quiet_then_fired.verdict == "introduced"
    assert both.verdict == "still_firing"
    assert neither.verdict == "silent"
    assert fired_then_quiet.checks_that_caught == ["x"]


def test_the_denominator_is_replays_where_something_was_firing():
    """Dividing by every commit measures how often people touch models that never had the defect."""
    rs = [Replay("a", "s", "m", "f", before={"x"}, after=set()),      # caught
          Replay("b", "s", "m", "f", before={"x"}, after={"x"}),      # still firing
          *[Replay(str(i), "s", "m", "f") for i in range(50)]]        # silent, irrelevant
    t = backtest.tally(rs)
    assert t["_had_something"] == 2
    assert t["_silenced_rate"] == 0.5          # not 1/52


def test_the_message_is_a_label_never_a_filter():
    """Three of four commits that removed a known defect in a real repo never said 'fix'."""
    assert backtest.FIXY.search("fix the degree ranking")
    assert not backtest.FIXY.search(
        "Two more models ranked spatial candidates in degrees, found by asking all 192")
    r = Replay("a", "s", "m", "f", before={"x"}, after=set(), message_says_fix=False)
    assert r.verdict == "caught"               # counted regardless of wording


def test_a_new_model_is_not_the_same_as_a_blob_assay_could_not_read():
    """319 of 403 'skips' on a real repo were new files. Sharing one bucket made the tool look
    far blinder than it is."""
    added = Replay("a", "s", "m", "f", skipped="the file was added or removed by this commit")
    broken = Replay("a", "s", "m", "f", skipped="could not parse after a Jinja strip: ParseError")
    assert added.verdict == "no_pair"
    assert broken.verdict == "unparseable"
    t = backtest.tally([added, broken])
    assert t["no_pair"] == 1 and t["unparseable"] == 1 and t["skipped"] == 2
