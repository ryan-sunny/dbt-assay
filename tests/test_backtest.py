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


def test_a_throwaway_profile_is_generated_under_the_projects_own_profile_name(tmp_path):
    """dbt needs a CONNECTION even to compile, and a fresh worktree has no warehouse file.
    Pointing a replay at the live warehouse would collide with whatever else is using it."""
    import yaml
    (tmp_path / "dbt_project.yml").write_text("name: demo\nprofile: my_profile\n")
    c = backtest.Compiler.__new__(backtest.Compiler)
    c.repo, c.project_subdir = str(tmp_path), "."
    assert c._profile_name() == "my_profile"
    d = c._throwaway_profile(str(tmp_path))
    prof = yaml.safe_load((tmp_path / "profiles.yml").read_text())
    assert "my_profile" in prof
    out = prof["my_profile"]["outputs"]["assay"]
    assert out["type"] == "duckdb" and out["path"].endswith("replay.duckdb")
    assert d == str(tmp_path)


def test_a_project_without_a_profile_key_still_gets_one(tmp_path):
    (tmp_path / "dbt_project.yml").write_text("name: demo\n")
    c = backtest.Compiler.__new__(backtest.Compiler)
    c.repo, c.project_subdir = str(tmp_path), "."
    assert c._profile_name() == "default"


def test_a_replay_records_how_it_was_read():
    assert Replay("a", "s", "m", "f").via == "stripped"
    assert Replay("a", "s", "m", "f", via="compiled").via == "compiled"


def test_compiled_sql_needs_no_jinja_strip():
    """It is already what the warehouse ran."""
    fired, skip = backtest._fire_compiled(
        "select row_number() over (order by ST_Distance(a, b)) rn from t", "m")
    assert not skip and "ranks_by_degrees" in fired


def test_a_leftover_macro_becomes_an_identifier_not_a_literal():
    """`1` only parses where a VALUE belongs, so anything standing in for a table name, a column
    or a clause died. On a real Snowflake project: `1` parsed 14 of 25 models, an identifier 21."""
    from dbt_assay.parse import digest
    out = backtest.dejinja("select a from {{ some_macro() }} where b = 1")
    assert digest(out).ok
    out2 = backtest.dejinja("select {{ dbt_utils.star(from=ref('x')) }} from t")
    assert digest(out2).ok


def test_a_macro_on_its_own_line_inside_a_from_clause_is_not_a_statement():
    """Deleting standalone Jinja left `from` with nothing after it. A macro returning a TABLE NAME
    is routinely written on its own line. Measured across three projects, keeping it wins."""
    from dbt_assay.parse import digest
    sql = ("{{ config(alias='x') }}\n\nselect a\nfrom\n"
           "    {{ set_datalake_project('schema.table') }}\n    as t")
    out = backtest.dejinja(sql)
    assert "config" not in out                 # config IS removed
    assert digest(out).ok, out                 # and the FROM still has an operand


def test_a_relative_repo_links_packages_that_resolve(tmp_path, monkeypatch):
    """`--repo .` from inside the repo is the natural invocation, and it broke on commit two."""
    import os

    from dbt_assay.backtest import Compiler
    repo = tmp_path / "repo"
    (repo / "transform" / "dbt_packages").mkdir(parents=True)
    monkeypatch.chdir(repo)
    c = Compiler(".", "transform")
    c.dir = str(tmp_path / "replay")
    os.makedirs(os.path.join(c.dir, "transform"))
    c._link_packages()
    c._link_packages()                     # the second commit: must not raise
    link = os.path.join(c.dir, "transform", "dbt_packages")
    assert os.path.islink(link) and os.path.isdir(link), "the link must resolve"


def test_the_unreadable_replays_are_named_by_model():
    """*** "26 OF 85 COULD NOT BE READ" CANNOT BE ACTED ON; "THESE FOUR MODELS" CAN. ***"""
    from dbt_assay.backtest import dark_models
    rs = [Replay("a", "s", "water_provenance", "p.sql", skipped="parse error"),
          Replay("b", "s", "water_provenance", "p.sql", skipped="parse error"),
          Replay("c", "s", "water_provenance", "p.sql"),
          Replay("d", "s", "stg_x", "x.sql", skipped="parse error"),
          Replay("e", "s", "new_model", "n.sql", skipped="added or removed here")]
    assert dark_models(rs) == [("water_provenance", 2, 3), ("stg_x", 1, 1)]
