"""Run a model's SQL on generated inputs in a worker process, so a crash is a result.

*** A SEGFAULT IN AN EXTENSION TOOK THE WHOLE `prove` WITH IT. *** (sunny-data feedback B3) On
Linux, DuckDB's spatial extension crashed on generated geometry in `az_section_parcels`; the
process died with signal 11 in the parse check, and nothing after it ran. The round trip and the
run check execute arbitrary model SQL on inputs no warehouse ever held, which is exactly where an
engine's untested paths are. So that SQL runs in ONE long-lived worker process: a crash or a hang
costs the model it was running ("unchecked: the engine crashed ..."), a fresh worker takes the
next, and the command carries on.

`spawn`, never `fork`: the parent has DuckDB and its threads loaded, and a forked copy of a
process with threads is undefined behaviour of its own.
"""
from __future__ import annotations

import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from concurrent.futures.process import BrokenProcessPool

TIMEOUT = 180            # seconds for one model; generated inputs are a handful of rows


def _duckdb_version() -> str:
    try:
        import duckdb
        return duckdb.__version__
    except Exception:                                            # noqa: BLE001
        return "?"


CRASHED = (f"the engine crashed running this model's SQL on generated inputs (a fault in DuckDB "
           f"{_duckdb_version()} or one of its extensions, not in the model; the spatial "
           f"extension in DuckDB 1.5.4 on Linux is one known case); nothing else was affected")


class Worker:
    """`with Worker() as w: ok, got = w.run(fn, *args)`: `got` is fn's result, or why it has
    none."""

    def __init__(self, timeout: float = TIMEOUT):
        self.timeout = timeout
        self._ex = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self._stop()

    def _start(self):
        if self._ex is None:
            self._ex = ProcessPoolExecutor(max_workers=1,
                                           mp_context=multiprocessing.get_context("spawn"))
        return self._ex

    def _stop(self, kill: bool = False):
        ex, self._ex = self._ex, None
        if ex is None:
            return
        if kill:
            for p in list(getattr(ex, "_processes", {}).values()):
                try:
                    p.kill()
                except Exception:                                # noqa: BLE001, S110
                    pass
        ex.shutdown(wait=not kill, cancel_futures=True)

    def run(self, fn, *args) -> tuple[bool, object]:
        try:
            return True, self._start().submit(fn, *args).result(timeout=self.timeout)
        except BrokenProcessPool:
            self._stop(kill=True)
            return False, CRASHED
        except FuturesTimeout:
            self._stop(kill=True)
            return False, (f"the engine did not finish this model's SQL on generated inputs in "
                           f"{int(self.timeout)} seconds")
