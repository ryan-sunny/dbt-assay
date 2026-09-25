"""SQL outside dbt (sunny-data: 101 tables queried from Python in 17 files). A file under
`outside_dbt.paths` counts as a reader of what its SQL reads; a relation dbt does not know is a
finding; a `paid` path makes what it reads customer-facing."""
from dbt_assay import outside, priority
from dbt_assay.checks.sources import source_reaches_nothing
from dbt_assay.manifest import Project


def test_outside_readers_become_exposures_and_unknown_tables_findings(tmp_path, project_dir):
    code = tmp_path / "product" / "reports"
    code.mkdir(parents=True)
    (code / "water.py").write_text(
        'Q = """select * from stg_ok_tilde s join analytics.mystery m on m.id = s.id"""\n'
        'def f(con, y):\n    return con.sql(f"select count(*) from int_ok_unique where y = {y}")\n'
        'NOT_SQL = "select a font from the menu"\n')
    outside.set_policy({"paths": ["product/"], "paid": ["product/reports"]}, tmp_path)
    outside._CACHE.clear()
    try:
        p = Project.load(project_dir)
        fs = outside.findings(p)
        checks = sorted(f.check for f in fs)
        assert checks == ["read_outside_dbt_undeclared", "sql_outside_dbt"], checks
        unknown = next(f for f in fs if f.check == "read_outside_dbt_undeclared")
        assert unknown.subject_name == "analytics.mystery"
        per_file = next(f for f in fs if f.check == "sql_outside_dbt")
        assert per_file.evidence["queries"] == 2 and per_file.evidence["paid"]
        uid = next(u for u, m in p.models.items() if m.name == "stg_ok_tilde")
        assert p.outside_readers[uid] == ["product/reports/water.py"]
        assert [e.title for e in p.exposures_of(uid)] == ["product/reports/water.py"]
        assert priority.Context.of(p).customer.get(uid)          # paid: customer-facing
    finally:
        outside.set_policy({})
        outside._CACHE.clear()


def test_a_source_only_python_reads_is_not_read_by_nothing(tmp_path, project_dir):
    import json
    m = json.loads((project_dir / "manifest.json").read_text())
    m["sources"]["source.p.raw.permits"] = {
        "unique_id": "source.p.raw.permits", "name": "permits", "source_name": "raw",
        "schema": "raw", "description": "", "columns": {}, "resource_type": "source"}
    (project_dir / "manifest.json").write_text(json.dumps(m))
    (tmp_path / "etl.py").write_text('SQL = "select * from raw.permits"\n')
    outside.set_policy({"paths": ["etl.py"]}, tmp_path)
    outside._CACHE.clear()
    try:
        p = Project.load(project_dir)
        assert [f.subject for f in source_reaches_nothing(p)] == ["source.p.raw.permits"]
        outside.apply(p)
        assert source_reaches_nothing(p) == []
    finally:
        outside.set_policy({})
        outside._CACHE.clear()
