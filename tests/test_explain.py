"""The engine plans each model's compiled SQL (EXPLAIN) before the round trip: free, runs nothing,
and a model it refuses says so in the engine's words."""
from types import SimpleNamespace

from dbt_assay import parsecheck


def test_many_models_in_one_call_and_a_refusal_is_isolated():
    calls = []

    def runner(sql):
        calls.append(sql)
        bad = "select nope" in sql
        return SimpleNamespace(failed=bad, rows=[] if bad else [{"assay_explained": 1}],
                               why="dbt noise\nEncountered an error:\n  Catalog Error: no table")
    items = [(f"m{i}", f"select {i} as x") for i in range(9)] + [("bad", "select nope from t")]
    refused = parsecheck.explain_all(items, "snowflake", runner)
    assert refused == {"bad": "Catalog Error: no table"}
    assert "explain using text select 0 as x" in calls[0] and len(calls) <= 9
    # braces in the SQL stay SQL: they sit inside a raw block
    stmt = parsecheck._explain_stmt([("m", "select '{{ x }}' as y")], "duckdb")
    assert "{% raw %}explain select '{{ x }}' as y{% endraw %}" in stmt
