from dbt_assay import provenance
from dbt_assay.infer import Schema, derive_columns
from dbt_assay.manifest import Project
from dbt_assay.parse import digest


def _load(project_dir):
    p = Project.load(project_dir)
    d = {uid: digest(m.compiled, m.name) for uid, m in p.models.items() if m.readable}
    sch = Schema.load(p, project_dir)
    derive_columns(p, d, sch)
    return p, d, sch


def test_a_defaulted_column_is_named_as_defaulted_not_merely_computed(project_dir):
    """`coalesce(x, 0)` is why a not_null test on it can never fire; that has to be visible."""
    p, d, sch = _load(project_dir)
    got = provenance.classify("model.p.stg_bad_notnull", p, d, sch)
    assert got["amount"].kind == "defaulted"
    assert got["id"].kind in ("carried", "from_source", "unknown")


def test_an_aggregate_is_distinguished_from_a_passed_through_column(project_dir):
    p, d, sch = _load(project_dir)
    got = provenance.classify("model.p.int_bad_unique", p, d, sch)
    assert got["n"].kind == "aggregated"
    assert got["section_id"].kind != "aggregated"


def test_a_window_assigned_column_is_ranked():
    from types import SimpleNamespace

    from dbt_assay.parse import digest as dg

    cols = SimpleNamespace(names=["id", "rn"], roots={})
    sch = SimpleNamespace(relation={}, uid_of={}, columns=lambda _uid: cols)
    proj = SimpleNamespace(sources={}, name_of=lambda u: u)

    d = {"m": dg("select id, row_number() over (partition by id order by ts) as rn from t")}
    got = provenance.classify("m", proj, d, sch)
    assert got["rn"].kind == "ranked"


def test_the_class_order_keeps_the_part_that_changes_how_it_reads():
    """A coalesce over an aggregate is a DEFAULTED aggregate; calling it merely aggregated
    loses the reason a null test on it cannot fire."""
    assert provenance.CLASSES.index("defaulted") < provenance.CLASSES.index("aggregated")
    assert provenance.CLASSES.index("constant") < provenance.CLASSES.index("defaulted")


def test_every_class_has_a_plain_english_explanation():
    assert set(provenance.EXPLAIN) == set(provenance.CLASSES)


def test_a_trace_stops_at_the_first_hop_that_did_something(project_dir):
    p, d, sch = _load(project_dir)
    hops = provenance.trace("amount", "model.p.stg_bad_notnull", p, d, sch)
    assert hops and hops[-1][2] == "defaulted"
