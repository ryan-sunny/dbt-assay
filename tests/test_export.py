import csv

from dbt_assay import export
from dbt_assay.store import Store


def _store(tmp_path):
    s = Store(tmp_path / "e.duckdb")
    s.con.execute("""insert into findings values
        ('r1','test_cannot_fail','model.p.m','m','m.sql','s','d',2,5.0,3,1,'{}')""")
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
