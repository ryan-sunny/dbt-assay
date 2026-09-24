"""Item 3: every finding held back on a premise records it, and a broken premise raises it again."""
from types import SimpleNamespace

from dbt_assay import ledger
from dbt_assay.checks.structural import Finding
from dbt_assay.probe import Observation


def _project(tests=(), unique_key=None):
    raw = {"nodes": {}}
    if unique_key:
        raw["nodes"]["model.p.lookup"] = {"resource_type": "model",
                                          "config": {"unique_key": unique_key}}
    models = {"model.p.lookup": SimpleNamespace(name="lookup", unique_id="model.p.lookup",
                                                parents=[]),
              "model.p.child": SimpleNamespace(name="child", unique_id="model.p.child",
                                               parents=["model.p.lookup"])}
    return SimpleNamespace(models=models, sources={}, tests=list(tests), raw=raw,
                           target_dir=None, name_of=lambda u: u.split(".")[-1])


def _unique_test(col="abbrev"):
    return SimpleNamespace(unique_id=f"test.p.unique_lookup_{col}", name=f"unique_lookup_{col}",
                           kind="unique", column=col, tests_model="model.p.lookup", kwargs={})


SCHEMA = SimpleNamespace(relation={"model.p.lookup": "main.lookup", "model.p.child": "main.child"})


def _entry(**kw):
    from dbt_assay.inventory import ModelEntry
    e = ModelEntry(uid="model.p.child", name="child", path="c.sql", layer="marts",
                   materialized="table")
    e.fanout_hops = [("lookup -> child", 0.81)]
    e.join_keys = {"lookup": ["abbrev"]}
    e.unique_key_parents = {"lookup"}
    for k, v in kw.items():
        setattr(e, k, v)
    return e


def _dupes(n=2387, d=2300):
    return {"main.lookup": {"abbrev": Observation("main.lookup", "abbrev", n, n, d,
                                                  "has_duplicates",
                                                  observed_at="2026-09-14 06:00:00")}}


def test_a_held_back_hop_is_recorded_and_stays_held_while_the_key_is_unchecked():
    from dbt_assay.judged import hop_multiplies_rows
    p = _project([_unique_test()])
    led = ledger.build(p, SCHEMA, [], None, tests={}, observed={})
    with ledger.collecting(led):
        assert hop_multiplies_rows(p, [_entry()]) == []
    (u,) = [u for u in led.uses if u.kind == "held_back"]
    assert u.model == "model.p.child" and u.dependent == "hop_multiplies_rows:model.p.child:lookup"
    assert led.premises[u.premise_id].status == ledger.UNCHECKED


def test_a_broken_key_raises_the_hop_with_why_it_is_back_under_the_same_id():
    from dbt_assay.judged import hop_multiplies_rows
    p = _project([_unique_test()])
    free = hop_multiplies_rows(p, [_entry(unique_key_parents=set())])
    led = ledger.build(p, SCHEMA, [], None, tests={"test.p.unique_lookup_abbrev":
                                                   ("pass", "2026-09-01 00:00:00")},
                       observed=_dupes())
    with ledger.collecting(led):
        (f,) = hop_multiplies_rows(p, [_entry()])
    back = f.evidence["why_it_is_back"]
    assert back["status"] == "broken" and back["broke_on"] == "2026-09-14"
    assert "87 duplicate" in back["why"] and "`abbrev` unique in `lookup`" == back["premise"]
    assert f.id == free[0].id, "a ruling on the hop before the key held must still apply"


def test_without_a_ledger_nothing_changes():
    from dbt_assay.judged import hop_multiplies_rows
    assert hop_multiplies_rows(_project(), [_entry()]) == []


def test_a_counted_key_is_the_premise_when_nothing_declares_one():
    """`--verify` counted it; the count is stored under the joined columns."""
    p = _project()
    obs = {"main.lookup": {"abbrev": Observation("main.lookup", "abbrev", 10, 10, 10, "unique")}}
    led = ledger.build(p, SCHEMA, [], None, tests={}, observed=obs)
    prem = ledger.parent_key(led, _entry(), "lookup")
    assert prem.columns == ("abbrev",) and prem.status == ledger.HOLDING


def test_a_composite_count_reads_as_the_composite_premise():
    p = _project()
    obs = {"main.lookup": {"b, a": Observation("main.lookup", "b, a", 10, 10, 9,
                                               "has_duplicates")}}
    led = ledger.build(p, SCHEMA, [], None, tests={}, observed=obs)
    assert ledger.unique(led, "model.p.lookup", ["a", "b"]).status == ledger.BROKEN


def test_an_arbitrary_pick_exempted_by_a_broken_unique_column_is_raised():
    from dbt_assay.checks.structural import arbitrary_pick
    from dbt_assay.parse import digest
    sql = ("select * from (select k, abbrev, row_number() over (partition by k order by abbrev)"
           " rn from lookup) where rn = 1")
    p = _project([_unique_test()])
    p.models["model.p.child"].path = "c.sql"
    d = {"model.p.child": digest(sql, "child")}
    assert arbitrary_pick(p, d) == []
    led = ledger.build(p, SCHEMA, [], None, tests={}, observed={})
    with ledger.collecting(led):
        assert arbitrary_pick(p, d) == []
    assert any(u.dependent.startswith("arbitrary_pick:model.p.child:") for u in led.uses)
    led = ledger.build(p, SCHEMA, [], None, tests={}, observed=_dupes())
    with ledger.collecting(led):
        (f,) = arbitrary_pick(p, d)
    assert f.evidence["why_it_is_back"]["premise"] == "`abbrev` unique in `lookup`"


def test_why_it_is_back_is_not_part_of_the_id():
    a = Finding("hop_multiplies_rows", "m", "m", "", "s", "", evidence={"hop": "x"})
    b = Finding("hop_multiplies_rows", "m", "m", "", "s", "",
                evidence={"hop": "x", "why_it_is_back": {"premise": "p"}})
    assert a.id == b.id


def test_a_unique_test_on_an_unrelated_table_does_not_excuse_a_pick():
    """10 of 12 excused picks on a real project leaned on a same-named column elsewhere."""
    from dbt_assay.checks.structural import arbitrary_pick
    from dbt_assay.parse import digest
    sql = ("select * from (select k, v, abbrev, row_number() over (partition by k "
           "order by abbrev) rn from other) where rn = 1")
    p = _project([_unique_test()])
    p.models["model.p.child"].parents = ["model.p.other"]
    p.models["model.p.child"].path = "c.sql"
    (f,) = arbitrary_pick(p, {"model.p.child": digest(sql, "child")})
    assert "why_it_is_back" not in f.evidence
