"""One construct written in several models is one edit, and the tools say so.

*** NINE FINDINGS, ONE MACRO, AND `plan` LISTED NINE FIX SHAPES. *** (25.21, 25.23b)
"""
from __future__ import annotations

from types import SimpleNamespace

from dbt_assay import groups, plan
from dbt_assay.checks.structural import Finding

CASE = ("CASE WHEN REGEXP_MATCHES(_cls, 'new.*(building|construction)') THEN 'New Construction' "
        "WHEN REGEXP_MATCHES(_cls, 'addition') THEN 'Addition' END")


def _f(model, expression, column="permit_class", check="test_cannot_fail"):
    return Finding(check=check, subject=f"model.p.{model}", subject_name=model, file=f"{model}.sql",
                   summary=f"accepted_values test on `{column}` cannot fail", detail="", base=2,
                   evidence={"test": f"accepted_values_{model}", "kind": "accepted_values",
                             "column": column, "expression": expression})


def _project(tmp_path, uses_macro=("stg_a", "stg_b", "stg_c"), macro_body=None):
    (tmp_path / "macros").mkdir(exist_ok=True)
    (tmp_path / "macros" / "permit_stg.sql").write_text(macro_body or (
        "{% macro permit_stg() %}\nselect\n  case\n"
        "    when regexp_matches(_cls, 'new.*(building|construction)') then 'New Construction'\n"
        "  end as permit_class\n{% endmacro %}\n"))
    (tmp_path / "macros" / "run_date.sql").write_text("{% macro run_date() %}now(){% endmacro %}\n")
    nodes = {f"model.p.{m}": {"depends_on": {"macros": (
        ["macro.p.permit_stg", "macro.p.run_date"] if m in uses_macro else ["macro.p.run_date"])}}
        for m in ("stg_a", "stg_b", "stg_c", "stg_d", "stg_e")}
    macros = {"macro.p.permit_stg": {"name": "permit_stg", "package_name": "p",
                                     "original_file_path": "macros/permit_stg.sql"},
              "macro.p.run_date": {"name": "run_date", "package_name": "p",
                                   "original_file_path": "macros/run_date.sql"}}
    return SimpleNamespace(raw={"nodes": nodes, "macros": macros}, project_name="p",
                           project_root=tmp_path)


def test_one_construct_in_three_models_is_one_group_credited_to_its_macro(tmp_path):
    fs = [_f("stg_a", CASE), _f("stg_b", CASE), _f("stg_c", CASE),
          _f("stg_d", "CASE WHEN x THEN 'y' END")]
    gs = groups.build(_project(tmp_path), fs)
    assert len(gs) == 1 and gs[0].models == ["stg_a", "stg_b", "stg_c"]
    d = gs[0].as_dict()
    assert d["macro"] == "permit_stg" and d["macro_at"] == "macros/permit_stg.sql:4"


def test_a_shared_macro_that_does_not_carry_the_construct_is_not_credited(tmp_path):
    """Two models on the field share `run_date` and write their CASE inline."""
    fs = [_f("stg_d", CASE), _f("stg_e", CASE)]
    gs = groups.build(_project(tmp_path), fs)
    assert len(gs) == 1 and gs[0].macro == "" and gs[0].as_dict()["macro_at"] == ""


def test_the_findings_own_column_is_masked_and_a_bare_column_is_no_construct(tmp_path):
    fs = [_f("stg_a", CASE.replace("_cls", "a_cls"), column="a_cls"),
          _f("stg_b", CASE.replace("_cls", "b_cls"), column="b_cls")]
    assert len(groups.build(_project(tmp_path), fs)) == 1, "same construct, different column"
    bare = [_f("stg_a", "permit_id", column="permit_id"),
            _f("stg_b", "permit_id", column="permit_id")]
    assert groups.build(_project(tmp_path), bare) == [], \
        "a unique test on a bare column is not a shared construct"


def test_the_plan_collapses_agreed_findings_of_one_group_into_one_edit(tmp_path):
    fs = [_f("stg_a", CASE), _f("stg_b", CASE), _f("stg_c", CASE)]
    gs = groups.build(_project(tmp_path), fs)

    class _Store:
        def ruled_findings(self, verdict):
            return {f.id: ("ryan", None, "yes") for f in fs} if verdict == "agree" else {}

    rows = plan.build(fs, _Store(), gs)
    assert len(rows) == 1, rows
    r = rows[0]
    assert r["fix_shape"] == "one edit in macros/permit_stg.sql:4"
    assert sorted(c["model"] for c in r["call_sites"]) == ["stg_a", "stg_b", "stg_c"]
    # every ruling still names its own finding
    assert sorted(c["finding"] for c in r["call_sites"]) == sorted(f.id for f in fs)


def test_a_group_never_changes_a_findings_identity_or_weight(tmp_path):
    fs = [_f("stg_a", CASE), _f("stg_b", CASE)]
    before = [(f.id, f.weight) for f in fs]
    groups.build(_project(tmp_path), fs)
    assert [(f.id, f.weight) for f in fs] == before


def test_the_review_card_says_the_fix_is_shared(tmp_path):
    from dbt_assay import reviewform
    from dbt_assay.store import Store
    fs = [_f("stg_a", CASE), _f("stg_b", CASE)]
    s = Store(str(tmp_path / "s.duckdb"))
    try:
        cards, _sql = reviewform.cards(fs, s, tmp_path, groups=groups.build(_project(tmp_path), fs))
    finally:
        s.close()
    assert all(c["group"]["size"] == 2 and c["group"]["macro_at"] for c in cards)


def test_a_shared_grain_key_is_not_a_construct(tmp_path):
    """A list's repr has quote marks; the grain `['_dlt_id']` is not a literal anybody wrote."""
    fs = [Finding(check="identifier_outside_grain", subject=f"model.p.{m}", subject_name=m,
                  file="", summary=f"s {m}", detail="", base=1,
                  evidence={"column": f"{m}_id", "grain": ["_dlt_id"], "provenance": "from_source"})
          for m in ("stg_a", "stg_b")]
    assert groups.build(_project(tmp_path), fs) == []
