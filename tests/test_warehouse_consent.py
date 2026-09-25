"""*** NOTHING RUNS ON SOMEBODY'S WAREHOUSE UNTIL THEY SAID SO. *** (Ryan, 2026-09-25)"""
import subprocess
from types import SimpleNamespace

import pytest

from dbt_assay import probe


def _project(tmp_path, output: dict):
    import yaml
    (tmp_path / "dbt_project.yml").write_text("name: p\nprofile: p\n")
    (tmp_path / "profiles.yml").write_text(yaml.safe_dump(
        {"p": {"target": "dev", "outputs": {"dev": output,
                                            "assay": {**output, "role": "ASSAY_RO"}}}}))
    return str(tmp_path)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(probe, "_ALLOWED", {})
    monkeypatch.setattr(probe, "_REACHED", {})
    monkeypatch.setattr(probe, "_SPENT", {"queries": 0, "usd": 0.0})
    monkeypatch.delenv("ASSAY_ALLOW_WAREHOUSE", raising=False)
    probe.set_policy({})
    yield
    probe.set_policy({})


def _no_dbt(monkeypatch):
    sent = []

    def run(cmd, **_k):
        sent.append(cmd)
        return SimpleNamespace(returncode=0, stderr="",
                               stdout='{"show": [{"assay_reachable": 1, "n": 1}]}')
    monkeypatch.setattr(subprocess, "run", run)
    return sent


SNOW = {"type": "snowflake", "account": "xy123", "user": "ASSAY", "password": "hunter2",
        "warehouse": "ASSAY_XS", "database": "ANALYTICS"}


def test_a_cloud_warehouse_gets_nothing_until_allowed(tmp_path, monkeypatch):
    pdir = _project(tmp_path, SNOW)
    sent = _no_dbt(monkeypatch)
    with pytest.raises(probe.WarehouseNotAllowed) as e:
        probe.run_sql("select 1 as n", pdir, profiles_dir=pdir)
    assert sent == []                                    # not even `select 1`
    msg = str(e.value)
    assert "snowflake xy123 as ASSAY" in msg and "allow_queries" in msg
    assert "hunter2" not in msg
    assert isinstance(e.value, probe.WarehouseUnreachable)   # every handler already stops on it


def test_allowed_by_config_or_env_and_a_local_duckdb_file_needs_nothing(tmp_path, monkeypatch):
    sent = _no_dbt(monkeypatch)
    probe.set_policy({"allow_queries": True, "target": "assay"})
    pdir = _project(tmp_path, SNOW)
    probe.run_sql("select 1 as n", pdir, profiles_dir=pdir)
    assert sent and all(c[c.index("--target") + 1] == "assay" for c in sent)
    assert probe.profile_output(pdir, pdir)["role"] == "ASSAY_RO"
    probe.set_policy({})
    for name, out, ok in (("b", {"type": "duckdb", "path": "w.duckdb"}, True),
                          ("c", {"type": "duckdb", "path": "md:prod"}, False)):
        d = tmp_path / name
        d.mkdir()
        pdir = _project(d, out)
        if ok:
            probe.run_sql("select 1 as n", pdir, profiles_dir=pdir)
        else:
            with pytest.raises(probe.WarehouseNotAllowed):
                probe.run_sql("select 1 as n", pdir, profiles_dir=pdir)
    monkeypatch.setenv("ASSAY_ALLOW_WAREHOUSE", "1")
    d = tmp_path / "e"
    d.mkdir()
    pdir = _project(d, SNOW)
    probe.run_sql("select 1 as n", pdir, profiles_dir=pdir)


def test_a_command_stops_before_the_query_past_its_limit(tmp_path, monkeypatch):
    sent = _no_dbt(monkeypatch)
    probe.set_policy({"allow_queries": True, "max_queries": 2})
    pdir = _project(tmp_path, SNOW)
    probe.run_sql("select 1 as n", pdir, profiles_dir=pdir)
    probe.run_sql("select 2 as n", pdir, profiles_dir=pdir)
    n = len(sent)
    with pytest.raises(probe.WarehouseBudget, match="max_queries"):
        probe.run_sql("select 3 as n", pdir, profiles_dir=pdir)
    assert len(sent) == n                                # the third never went
