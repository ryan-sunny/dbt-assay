"""One order for everything a person might act on (assay-loops.md gap 1): customer-facing, then
happening now, then reach, then how sure. Every surface uses it, so the most important finding is
first on all of them."""
from types import SimpleNamespace

from dbt_assay import priority


def _f(check, subject="model.p.a", exposures=(), marts=0, desc=0, ev=None, fid="x"):
    return SimpleNamespace(check=check, subject=subject, exposures=list(exposures), marts=marts,
                           descendants=desc, evidence=ev or {}, base=2, id=fid)


def test_the_order_is_customer_then_harm_then_reach_then_confidence():
    ctx = priority.Context(customer={"model.p.paid": ["Water report"]},
                           rests_on={"model.p.hub": 40})
    items = [
        _f("column_has_no_description", marts=30, fid="wide"),
        _f("column_has_no_description", subject="model.p.paid", fid="paid"),
        _f("test_is_failing", fid="failing"),
        _f("hop_multiplies_rows", ev={"probability": 0.95}, fid="sure"),
        _f("arbitrary_pick", subject="model.p.hub", fid="hub"),
        _f("arbitrary_pick", fid="quiet"),
    ]
    got = [f.id for f in priority.sort(items, ctx)]
    assert got[:3] == ["paid", "failing", "wide"], got
    assert got.index("hub") < got.index("quiet") and got.index("sure") < got.index("quiet")
    p = priority.of(items[1], ctx)
    assert p["tier"] == "customer-facing" and p["why"][0] == "reaches Water report"
    assert priority.of(items[2], ctx)["why"] == ["a dbt test on it fails"]


def test_a_broken_premise_it_was_held_on_counts_as_happening_now():
    f = _f("hop_multiplies_rows", ev={"why_it_is_back": {"premise": "k"}})
    assert priority.of(f)["tier"] == "happening now"


def test_paid_exposures_come_from_the_projects_own_meta(project_dir):
    from dbt_assay.manifest import Exposure
    e = Exposure(unique_id="exposure.p.r", name="r", label="Report", type="application",
                 owner="", url="", maturity="", depends_on=[], path="", meta={"paid": True})
    assert e.customer_facing
    assert not Exposure(unique_id="x", name="x", label="", type="", owner="", url="",
                        maturity="high", depends_on=[], path="").customer_facing
