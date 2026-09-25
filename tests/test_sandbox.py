"""B3 (sunny-data feedback): DuckDB's spatial extension segfaulted on generated geometry on Linux
and took the whole `prove` with it. Model SQL on generated inputs now runs in a worker process."""
import os
import signal
import time

from test_prove import _load, build

from dbt_assay import claimcheck, ledger, parsecheck, sandbox


def _segfault():
    os.kill(os.getpid(), signal.SIGSEGV)


def _sleep():
    time.sleep(30)


def _add(a, b):
    return a + b


def test_a_crashed_or_hung_worker_is_a_result_and_the_next_task_runs():
    with sandbox.Worker(timeout=5) as w:
        assert w.run(_add, 1, 2) == (True, 3)
        ok, why = w.run(_segfault)
        assert not ok and "engine crashed" in why
        assert w.run(_add, 2, 2) == (True, 4)
        ok, why = w.run(_sleep)
        assert not ok and "did not finish" in why
        assert w.run(_add, 3, 3) == (True, 6)


class _Crashing:
    def run(self, *_a):
        return False, sandbox.CRASHED


MODELS = {"stg_parent": "select id, name from raw.parent",
          "renamed": "select p.id as rid, p.name from main.stg_parent p"}


def test_a_crash_reads_unchecked_in_the_round_trip_and_the_run_check(tmp_path):
    p, _d, sch = _load(build(tmp_path, MODELS))
    sql = p.models["model.p.renamed"].compiled
    st, detail, _n = parsecheck.check_duckdb(p, sch, sql, "duckdb", _Crashing())
    assert st == ledger.UNCHECKED and "engine crashed" in detail
    got = claimcheck.check_model(p, sch, "model.p.renamed", sql, "duckdb", [
        {"property": "g", "claim": {"kind": "unique", "cols": ["rid"]},
         "premises": [{"id": "x", "relation": "model.p.stg_parent", "columns": ["id"],
                       "property": "unique"}]}], worker=_Crashing())
    assert got["g"][0] == claimcheck.UNCHECKED and "engine crashed" in got["g"][1]
    # and through a real worker process, it still runs
    with sandbox.Worker() as w:
        assert parsecheck.check_duckdb(p, sch, sql, "duckdb", w)[0] == ledger.HOLDING


def test_an_unwritable_cache_is_one_line_naming_the_folder(tmp_path, monkeypatch):
    """B1: three stacked mkdir tracebacks, the cause on the last line of ~60."""
    import pytest

    from dbt_assay import toolchain
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    monkeypatch.setenv("ASSAY_CACHE", str(locked))
    try:
        with pytest.raises(RuntimeError) as e:
            toolchain.ensure_dir(toolchain.cache_root() / "4.34.1")
        msg = str(e.value)
        assert str(locked) in msg and "ASSAY_CACHE" in msg and "\n" not in msg
        # B2: ASSAY_CACHE is the parent of assay/lean
        assert toolchain.cache_root() == locked / "assay" / "lean"
    finally:
        locked.chmod(0o700)
