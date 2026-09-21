import csv

from dbt_assay import export
from dbt_assay.store import Store


def _store(tmp_path):
    s = Store(tmp_path / "e.duckdb")
    # Named, never positional. A positional insert assumes a column order and a migration appends
    # at the END, so the two disagree the moment the store is upgraded -- which is exactly what
    # happened when `finding_id` was added.
    s.con.execute("""insert into findings
        (run_id, check_name, subject, subject_name, file, summary, detail,
         base, weight, descendants, marts, evidence, finding_id)
        values ('r1','test_cannot_fail','model.p.m','m','m.sql','s','d',2,5.0,3,1,'{}','abc123')
    """)
    s.adjudicate("model.p.m", "role__x", "column_role", "measure", "agree", note="ok")
    return s


def test_seeds_land_as_csv_any_adapter_can_load(tmp_path):
    s = _store(tmp_path)
    out = export.to_seeds(s, tmp_path / "seeds")
    names = {e.table for e in out}
    assert "findings" in names and "adjudications" in names
    f = next(e for e in out if e.table == "findings")
    rows = list(csv.DictReader(f.path.open()))
    assert rows[0]["check_name"] == "test_cannot_fail"
    s.close()


def test_a_table_that_does_not_exist_is_skipped_not_fatal(tmp_path):
    s = Store(tmp_path / "empty.duckdb")
    out = export.to_seeds(s, tmp_path / "seeds")
    assert all(e.rows >= 0 for e in out)          # no crash on a store with nothing in it
    s.close()


def test_assays_own_tables_arrive_documented(tmp_path):
    """It has opinions about undocumented models."""
    s = _store(tmp_path)
    out = export.to_seeds(s, tmp_path / "seeds")
    y = export.schema_yml(s, out)
    assert "version: 2" in y and "seeds:" in y
    for e in out:
        assert f"name: {export.PREFIX}{e.table}" in y
    # the columns whose meaning is easy to misread carry an explanation
    assert "distribution concentration" in y
    assert "cache hit whose state moved is a miss" in y
    s.close()


def test_every_exported_table_has_a_description():
    assert set(export.TABLES) >= {"inventory", "findings", "model_decisions", "adjudications"}
    assert all(v and len(v) > 40 for v in export.TABLES.values())


def test_the_runs_table_is_exported_because_a_count_is_not_a_clock(tmp_path):
    """*** EVERY OTHER EXPORTED TABLE IS KEYED BY run_id AND CARRIES NO CLOCK. ***

    So a model asking "what is our debt NOW" had to guess which run was current. The shipped
    example guessed with `order by count(*) desc`, and on the field warehouse three runs tied at
    349 findings, making it an arbitrary pick. Adding `run_id` to that order makes it stable and
    makes it permanently WRONG, because the release that made findings honest also made them
    fewer: 349 became 234, and a smaller run can never win a comparison by size.

    Exercised through the real export, and it asserts the CLOCK is in the file -- a `runs` table
    exported without `started_at` would fix nothing.
    """
    import csv

    from dbt_assay import export
    from dbt_assay.store import Store

    s = Store(str(tmp_path / "assay.duckdb"))
    try:
        s.con.execute(
            "insert into runs (run_id, started_at, project, assay_version, models) "
            "values ('r1', timestamp '2026-01-01 10:00:00', 'p', '0.25.1', 3)")
        out = export.to_seeds(s, tmp_path / "seeds")
    finally:
        s.close()

    written = {e.table: e.path for e in out}
    assert "runs" in written, f"runs is not exported: {sorted(written)}"
    with written["runs"].open() as fh:
        rows = list(csv.DictReader(fh))
    assert rows and rows[0]["run_id"] == "r1"
    assert rows[0]["started_at"].startswith("2026-01-01"), rows[0]["started_at"]
    assert "started_at" in export._SEED_TYPE, "the clock must not arrive as text"
    assert export._SEED_TYPE["started_at"] == "timestamp"
