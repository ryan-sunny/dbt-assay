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


def test_a_distinct_on_in_the_final_select_is_the_grain_and_one_in_a_lookup_is_not():
    import sqlglot

    from dbt_assay.parse import _final_dedupe
    final = ("with a as (select distinct on (kind, id) kind, id, x from t order by kind, id) "
             "select * from a")
    lookup = ("with l as (select distinct on (code) code, label from lk) "
              "select t.id, l.label from t join l on l.code = t.code")
    assert _final_dedupe(sqlglot.parse_one(final, read="duckdb")) == ("distinct_on", ["kind", "id"])
    assert _final_dedupe(sqlglot.parse_one(lookup, read="duckdb")) == ("", [])


def test_a_group_by_two_ctes_down_is_the_grain():
    import sqlglot

    from dbt_assay.parse import _final_dedupe
    sql = ("with o as (select owner_id, count(*) n from t group by owner_id), "
           "c as (select * from o) select owner_id, n from c")
    assert _final_dedupe(sqlglot.parse_one(sql, read="duckdb")) == ("group_by", ["owner_id"])


def test_a_loader_row_hash_is_never_a_grain():
    from dbt_assay.contracts import LOADER_ROW_IDS
    assert "_dlt_id" in LOADER_ROW_IDS
