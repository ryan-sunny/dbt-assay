from dbt_assay import diff
from dbt_assay.inventory import ColumnEntry, Fact, ModelEntry


def _m(name, grain=None, derived=None, cols=(), uid=None, marts=0):
    e = ModelEntry(uid=uid or f"model.p.{name}", name=name, path=f"{name}.sql",
                   layer="marts", materialized="table", marts=marts)
    e.grain = Fact(grain, "declared") if grain else None
    e.derived_grain = derived
    e.columns = [ColumnEntry(name=c, provenance=Fact(p, "derived"),
                             role=Fact(r, "judged", 0.9) if r else None)
                 for c, p, r in cols]
    return e


def test_a_rewrite_that_changes_no_meaning_produces_nothing():
    """A refactor whose contract is unchanged needs no semantic review."""
    a = _m("m", ["id"], ["id"], [("id", "carried", "identifier")])
    b = _m("m", ["id"], ["id"], [("id", "carried", "identifier")])
    assert diff.compare([a], [b]) == []


def test_a_grain_change_hidden_behind_a_declared_key_is_still_caught():
    """The declaration outranks the SQL in the inventory, so without derived_grain a GROUP BY
    change on a model that declares a key is invisible -- and that is the change worth seeing."""
    before = _m("m", ["section_id"], ["section_id", "year"])
    after = _m("m", ["section_id"], ["section_id"])
    c = diff.compare([before], [after])
    assert [x.kind for x in c] == ["grain_in_sql"]
    assert "moves from" in c[0].detail


def test_a_sql_grain_that_contradicts_a_live_test_says_one_of_them_is_wrong():
    before = _m("m", ["section_key"], ["section_key"])
    after = _m("m", ["section_key"], ["transaction_id"])
    c = diff.compare([before], [after])[0]
    assert "still declares one row per section_key" in c.detail
    assert "One of the two is now wrong" in c.detail


def test_a_grain_change_with_aggregating_consumers_outranks_one_without():
    a = diff.Change("m", "grain", aggregating_consumers=["x"])
    b = diff.Change("m", "grain")
    assert a.severity > b.severity


def test_a_removed_column_reports_who_read_it():
    before = _m("m", ["id"], ["id"], [("id", "carried", None), ("amount", "computed", None)])
    after = _m("m", ["id"], ["id"], [("id", "carried", None)])
    c = [x for x in diff.compare([before], [after]) if x.kind == "column_removed"]
    assert c and c[0].column == "amount"


def test_a_provenance_change_is_reported_because_it_changes_how_a_column_reads():
    before = _m("m", ["id"], ["id"], [("amount", "from_source", None)])
    after = _m("m", ["id"], ["id"], [("amount", "defaulted", None)])
    c = [x for x in diff.compare([before], [after]) if x.kind == "provenance"]
    assert c and c[0].before == "from_source" and c[0].after == "defaulted"


def test_added_and_removed_models_are_reported():
    c = diff.compare([_m("gone", ["id"], ["id"])], [_m("fresh", ["id"], ["id"])])
    kinds = {x.kind: x.model for x in c}
    assert kinds["model_added"] == "fresh" and kinds["model_removed"] == "gone"


def test_the_summary_says_what_a_reviewer_needs_not_a_data_structure():
    before = _m("m", None, ["section", "case"], marts=3)
    after = _m("m", None, ["section"], marts=3)
    c = diff.compare([before], [after])
    c[0].consumers = ["a", "b"]
    c[0].aggregating_consumers = ["b"]
    text = diff.summarize(c)
    assert "one row per" not in text.lower() or "moves from" in text
    assert "2 models consume it" in text
    assert "aggregates over it" in text          # one aggregating consumer, so singular
    assert "Nothing in the SQL diff says this" in text


def test_the_summary_reads_as_english_for_one_and_for_many():
    """This paragraph is the headline output; it should not say '1 models consume it'."""
    c = diff.Change("m", "grain", detail="d", consumers=["a"], aggregating_consumers=["a"], marts=1)
    one = diff.summarize([c])
    assert "1 model consumes it" in one and "1 of them aggregates over it" in one
    assert "1 mart downstream" in one
    c2 = diff.Change("m", "grain", detail="d", consumers=["a", "b"],
                     aggregating_consumers=["a", "b"], marts=3)
    many = diff.summarize([c2])
    assert "2 models consume it" in many and "2 of them aggregate over it" in many
    assert "3 marts downstream" in many
