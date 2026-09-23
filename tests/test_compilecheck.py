"""A compile without a warehouse connection: valid SQL that is not the real model."""
from types import SimpleNamespace

from dbt_assay import compilecheck
from dbt_assay.parse import digest

MACROS = {
    "macro.p.present": {"macro_sql": "{% for c in adapter.get_columns_in_relation(r) %}",
                        "depends_on": {"macros": []}},
    "macro.p.col": {"macro_sql": "{{ name if name in present else 'cast(null as varchar)' }}",
                    "depends_on": {"macros": []}},
    "macro.p.wrapper": {"macro_sql": "{{ present(r) }}", "depends_on": {"macros": ["macro.p.present"]}},
}
RAW = ("{% set present = present(source('raw', 't')) %} select {{ col(present, 'alpha') }}, "
       "{{ col(present, 'beta', 'double') }}, {{ col(present, 'gamma') }}, "
       "{{ config(materialized='table') }} from {{ source('raw', 't') }}")


def _project(sql):
    uid = "model.p.m"
    node = {"depends_on": {"macros": ["macro.p.present", "macro.p.col"],
                           "nodes": ["source.p.raw.t"]}, "raw_code": RAW}
    project = SimpleNamespace(raw={"macros": MACROS, "nodes": {uid: node}},
                              models={uid: SimpleNamespace(name="m", readable=True, compiled=sql)})
    return project, {uid: digest(sql, "m", "duckdb")}


def test_a_macro_that_calls_an_introspecting_macro_introspects():
    assert compilecheck.introspective_macros({"macros": MACROS}) == {"macro.p.present",
                                                                     "macro.p.wrapper"}


def test_a_connected_compile_is_left_alone():
    p, d = _project("select alpha, beta, cast(null as varchar) as gamma from raw.t")
    assert compilecheck.blind_models(p, d) == ([], 1)


def test_a_compile_told_nothing_exists_is_caught_and_the_catalog_makes_it_certain():
    p, d = _project("select cast(null as varchar) as alpha, cast(null as double) as beta, "
                    "cast(null as varchar) as gamma from raw.t")
    found, looked = compilecheck.blind_models(p, d)
    assert looked == 1 and found and "uses 0 of them" in found[0].why
    cat = {"sources": {"source.p.raw.t": {"columns": {"alpha": {}, "beta": {}}}}}
    found, _ = compilecheck.blind_models(p, d, cat)
    assert "DO exist on the parent" in found[0].why
    assert "WITHOUT a connection" in compilecheck.sentence(found, 1)


def test_the_type_argument_and_dbt_builtins_are_not_columns():
    """'double', 'table' and the source names are quoted words, not columns looked up."""
    p, d = _project("select alpha, beta, gamma from raw.t")
    assert compilecheck.blind_models(p, d)[0] == []
