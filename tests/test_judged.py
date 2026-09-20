from dbt_assay import judged
from dbt_assay.inventory import ColumnEntry, Fact, ModelEntry


def _entry(grain, cols):
    e = ModelEntry(uid="model.p.m", name="m", path="m.sql", layer="marts", materialized="table")
    e.grain = grain
    e.columns = cols
    return e


class _Proj:
    def blast_radius(self, _uid):
        return {"descendants": 3, "marts": 1}


def _col(name, role=None, conf=None, in_key=False, prov="carried"):
    return ColumnEntry(name=name, provenance=Fact(prov, "derived"),
                       role=Fact(role, "judged", conf) if role else None, in_key=in_key)


def test_a_judged_grain_contradicting_a_declared_key_is_a_finding():
    e = _entry(Fact(["a"], "judged", 0.9), [])
    fs = judged.run_all(_Proj(), [e], {"model.p.m": ["a", "b"]})
    assert [f.check for f in fs] == ["grain_contradicts_test"]
    assert fs[0].base == 3                      # confident enough to contradict a human


def test_a_weak_judgment_contradicting_a_test_is_softer():
    e = _entry(Fact(["a"], "judged", 0.55), [])
    fs = judged.run_all(_Proj(), [e], {"model.p.m": ["a", "b"]})
    assert fs[0].base == 2


def test_a_derived_grain_never_contradicts_a_declared_key():
    """Only a judgment gets to argue with a human. Code disagreeing is a code bug to fix, not a
    finding to file against the team."""
    e = _entry(Fact(["a"], "derived"), [])
    assert not judged.run_all(_Proj(), [e], {"model.p.m": ["a", "b"]})


def test_an_identifier_outside_the_grain_is_reported():
    e = _entry(Fact(["pk"], "declared"), [_col("natural_id", "identifier", 0.93)])
    fs = judged.run_all(_Proj(), [e], {})
    assert any(f.check == "identifier_outside_grain" for f in fs)


def test_a_low_confidence_role_does_not_argue_with_the_grain():
    e = _entry(Fact(["pk"], "declared"), [_col("maybe", "identifier", 0.55)])
    assert not judged.run_all(_Proj(), [e], {})


def test_a_measure_inside_the_grain_is_reported_at_full_severity():
    e = _entry(Fact(["wdid", "years"], "declared"),
               [_col("years", "measure", 0.91, in_key=True)])
    fs = judged.run_all(_Proj(), [e], {})
    hit = [f for f in fs if f.check == "measure_inside_grain"]
    assert hit and hit[0].base == 3


def test_an_unresolved_grain_surfaces_as_a_queue_item_not_a_silent_default():
    e = _entry(Fact(["a", "b"], "judged", 0.6, resting_on=["unresolved: b"]), [])
    fs = judged.run_all(_Proj(), [e], {})
    assert any(f.check == "grain_unresolved" for f in fs)


def test_severity_is_lifted_by_reach():
    e = _entry(Fact(["wdid", "years"], "declared"),
               [_col("years", "measure", 0.91, in_key=True)])
    f = judged.run_all(_Proj(), [e], {})[0]
    assert f.descendants == 3 and f.weight > f.base


def test_a_namespaced_alias_of_the_key_is_not_a_second_identifier():
    """`parcel_pk` as `'denver-' || schednum` beside `parcel_id` as `schednum` identifies exactly
    the same row. Reporting it as an identifier outside the grain is true and useless."""
    from types import SimpleNamespace

    e = _entry(Fact(["parcel_id"], "declared"), [_col("parcel_pk", "identifier", 0.95)])
    d = {"model.p.m": SimpleNamespace(output_exprs={
        "parcel_pk": "'denver-' || CAST(p.schednum AS VARCHAR)",
        "parcel_id": "CAST(p.schednum AS VARCHAR)"})}
    assert not judged.run_all(_Proj(), [e], {}, d)

    # a genuinely different entity still fires
    e2 = _entry(Fact(["parcel_pk"], "declared"), [_col("section_id", "identifier", 0.94)])
    d2 = {"model.p.m": SimpleNamespace(output_exprs={
        "section_id": "s.section_id", "parcel_pk": "p.parcel_pk"})}
    assert any(f.check == "identifier_outside_grain" for f in judged.run_all(_Proj(), [e2], {}, d2))
