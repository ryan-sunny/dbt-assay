"""A model's SQL in DuckDB, for the checks that run it on generated rows, and whether that is safe.

*** SNOWFLAKE SQL RAN IN DUCKDB AS WRITTEN. *** The run check (`claimcheck`) executes each model's
SQL on generated inputs in an in-memory DuckDB, and for a project in any other dialect it ran the
text unchanged: most of it failed (unchecked, which is honest), and what happened to run, ran
with DuckDB's meaning. Integer division, string functions, date arithmetic and NULL ordering
differ, so a claim could read held or contradicted for a reason that is not in the model.

So the text is translated with sqlglot (`read=<dialect>, write=duckdb`, refusing what sqlglot
cannot express rather than approximating it), and the translation is kept only when it ROUND
TRIPS: written back in the source dialect and translated again, it is the same DuckDB statement
(`iff` becoming `CASE WHEN` is the same statement; a construct lost on the way is not). That is
not a proof that the two engines mean the same thing by it; it says the translation lost nothing
sqlglot can see.
Every result on a translation says so, and a contradiction found only on a translation is not
reported as one.
"""
from __future__ import annotations

import sqlglot
from sqlglot.errors import ErrorLevel


def to_duckdb(sql: str, dialect: str) -> tuple[str | None, str]:
    """(DuckDB text, "") when the translation round-trips; (None, why) when it does not."""
    d = (dialect or "duckdb").lower()
    if d == "duckdb":
        return sql, ""
    try:
        tree = sqlglot.parse_one(sql, read=d)
    except Exception as e:                                       # noqa: BLE001
        return None, f"sqlglot could not read it as {d}: {str(e)[:120]}"
    try:
        duck = tree.sql(dialect="duckdb", unsupported_level=ErrorLevel.RAISE)
    except Exception as e:                                       # noqa: BLE001
        return None, f"it does not translate from {d} to DuckDB: {str(e)[:160]}"
    try:
        back = sqlglot.parse_one(duck, read="duckdb").sql(dialect=d)
        again = sqlglot.parse_one(back, read=d)
    except Exception as e:                                       # noqa: BLE001
        return None, f"the DuckDB translation does not read back as {d}: {str(e)[:120]}"
    # Stable, not identical: `iff(...)` comes back as `CASE WHEN` and is the same statement. What
    # matters is that the model and its round trip translate to the same DuckDB text.
    try:
        duck_again = again.sql(dialect="duckdb", unsupported_level=ErrorLevel.RAISE)
    except Exception as e:                                       # noqa: BLE001
        return None, f"the DuckDB translation does not read back as {d}: {str(e)[:120]}"
    if duck_again != duck:
        return None, (f"the DuckDB translation does not round-trip: written back as {d} and "
                      f"translated again it is a different statement, so what ran would not be "
                      f"this model")
    return duck, ""


def note(dialect: str) -> str:
    return (f"run on its DuckDB translation (from {dialect}; it round-trips, which is not proof "
            f"the two engines mean the same thing by it)")
