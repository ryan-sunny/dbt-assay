"""*** SNOWFLAKE SQL RAN IN DUCKDB AS WRITTEN. *** The run check translates it, keeps only a
translation that round-trips, and never reports a contradiction found only on a translation."""
from dbt_assay import translate


def test_a_translation_that_round_trips_is_kept():
    duck, why = translate.to_duckdb(
        "select id, iff(amount > 0, 'pos', 'neg') as sign from raw.orders", "snowflake")
    assert why == "" and duck is not None and "CASE WHEN" in duck.upper()


def test_duckdb_is_itself_and_nonsense_is_refused_with_a_reason():
    assert translate.to_duckdb("select 1", "duckdb") == ("select 1", "")
    duck, why = translate.to_duckdb("select from where", "snowflake")
    assert duck is None and "snowflake" in why


def test_a_contradiction_on_a_translation_is_not_a_contradiction(monkeypatch):
    from dbt_assay import claimcheck
    monkeypatch.setattr(claimcheck, "_check_model",
                        lambda *a, **k: {"p": (claimcheck.CONTRADICTED, "2 rows"),
                                         "q": (claimcheck.HOLDS, "")})
    got = claimcheck.check_model(None, None, "m", "select 1 as a from t", "snowflake",
                                 [{"property": "p"}, {"property": "q"}])
    assert got["p"][0] == claimcheck.UNCHECKED and "translation" in got["p"][1]
    assert got["q"][0] == claimcheck.HOLDS and "translation" in got["q"][1]
