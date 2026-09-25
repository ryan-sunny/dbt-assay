"""Evidence of harm today, as findings (sunny-data, assay-loops.md): a failing dbt test, a lost or
refuted guarantee, and freshness rules nothing checks. Each fires on the broken case and stays
quiet on the healthy one beside it."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from dbt_assay import harm, ledger
from dbt_assay.checks.sources import source_freshness_not_run
from dbt_assay.manifest import Project
from dbt_assay.store import Store


def test_a_failing_test_is_a_finding_and_a_passing_one_is_not(tmp_path, project_dir):
    p = Project.load(project_dir)
    t_fail, t_pass = p.tests[0], p.tests[1]
    s = Store(str(tmp_path / "s.duckdb"))
    ledger.record_test_status(s, {t_fail.unique_id: ("fail", "2026-09-25 06:00:00"),
                                  t_pass.unique_id: ("pass", "2026-09-25 06:00:00")}, "t")
    got = harm.failing_test_findings(p, s)
    assert [f.evidence["test_id"] for f in got] == [t_fail.unique_id]
    assert got[0].check == "test_is_failing" and got[0].subject == t_fail.tests_model
    s.close()


def _proj(tmp_path, freshness=True):
    raw = {"source.p.raw.a": {"freshness": {"warn_after": {"count": 1, "period": "day"}}
                              if freshness else {}}}
    return SimpleNamespace(raw={"sources": raw}, sources={"source.p.raw.a": object()},
                           target_dir=str(tmp_path), project_name="p")


def test_declared_freshness_with_no_check_run_is_a_finding(tmp_path):
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    # never run
    got = source_freshness_not_run(_proj(tmp_path), now=now)
    assert got and got[0].evidence.get("never_run")
    # ran 78 days ago
    (tmp_path / "sources.json").write_text(json.dumps(
        {"metadata": {"generated_at": (now - timedelta(days=78)).isoformat()}, "results": []}))
    got = source_freshness_not_run(_proj(tmp_path), now=now)
    assert got and got[0].evidence["days_since"] == 78.0
    # ran yesterday: quiet
    (tmp_path / "sources.json").write_text(json.dumps(
        {"metadata": {"generated_at": (now - timedelta(days=1)).isoformat()}, "results": []}))
    assert source_freshness_not_run(_proj(tmp_path), now=now) == []
    # nothing declared: nothing to check, quiet
    assert source_freshness_not_run(_proj(tmp_path, freshness=False), now=now) == []


def test_a_lost_guarantee_is_a_finding_and_a_regrouped_one_is_not(project_dir, monkeypatch):
    from dbt_assay import prove
    p = Project.load(project_dir)
    uid = next(iter(p.models))
    rows = [{"model": uid, "model_name": p.models[uid].name, "property": "grain",
             "statement": "one row per id", "rule": "r", "guarantee": g, "premises": [],
             "lost_because": "`raw.x` (id) broke: 3 duplicates", "run_check": {"detail": ""}}
            for g in ("lost", "regrouped", "holding", "refuted")]
    monkeypatch.setattr(prove, "stored", lambda store: rows)
    monkeypatch.setattr(prove, "with_guarantees", lambda r, led, project, store: r)
    got = harm.guarantee_findings(p, object(), object())
    assert sorted(f.check for f in got) == ["guarantee_does_not_hold", "guarantee_lost"]
    lost = next(f for f in got if f.check == "guarantee_lost")
    assert "3 duplicates" in lost.detail and lost.evidence["guarantee"] == "lost"
