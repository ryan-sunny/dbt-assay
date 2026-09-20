from dbt_assay.infer import Schema, derive_columns, relation_name
from dbt_assay.manifest import Project
from dbt_assay.parse import digest


def test_relation_name_uses_the_alias_not_the_model_name():
    assert relation_name({"database": "db", "schema": "s", "alias": "a", "name": "n"}) == "db.s.a"
    assert relation_name({"database": "db", "schema": "s", "identifier": "i"}) == "db.s.i"


def test_columns_are_derived_and_carry_their_provenance(project_dir):
    p = Project.load(project_dir)
    d = {uid: digest(m.compiled, m.name) for uid, m in p.models.items() if m.readable}
    sch = Schema.load(p, project_dir)
    derive_columns(p, d, sch)
    cols = sch.columns("model.p.stg_bad_notnull")
    assert cols.source_of == "derived"
    assert set(cols.names) == {"id", "amount"}


def test_a_missing_catalog_is_not_an_error(project_dir):
    sch = Schema.load(Project.load(project_dir), project_dir)
    assert sch.catalog_present is False
