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


def default_slots() -> int:
    """Processes a sandboxed step uses: ASSAY_WORKERS, else one fewer than the cores (the box's 4
    cores give 3), at least 1."""
    import os
    try:
        n = int(os.environ.get("ASSAY_WORKERS") or 0)
    except ValueError:
        n = 0
    return max(1, n or (os.cpu_count() or 2) - 1)


class Worker:
    """`with Worker() as w: ok, got = w.run(fn, *args)`: `got` is fn's result, or why it has
    none.

    *** SEVERAL PROCESSES, EACH ITS OWN POOL OF ONE. *** (sunny-data box: `prove` ran every model
    through one process on a 4-core machine.) One pool with several processes is the wrong shape:
    a native crash breaks that whole pool and every task queued in it. So each slot is a pool of
    one, `run` borrows a free slot, and any number of threads may call it; a crash costs the model
    that slot was running and a fresh process takes the slot's next task.
    """

    def __init__(self, timeout: float = TIMEOUT, slots: int | None = None):
        import queue
        self.timeout = timeout
        self.slots = max(1, slots or 1)
        self._ex: list = [None] * self.slots
        self._free: queue.Queue = queue.Queue()
        for i in range(self.slots):
            self._free.put(i)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        for i in range(self.slots):
            self._stop(i)

    def _start(self, i: int):
        if self._ex[i] is None:
            self._ex[i] = ProcessPoolExecutor(max_workers=1,
                                              mp_context=multiprocessing.get_context("spawn"))
        return self._ex[i]

    def _stop(self, i: int, kill: bool = False):
        ex, self._ex[i] = self._ex[i], None
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
        i = self._free.get()
        try:
            return True, self._start(i).submit(fn, *args).result(timeout=self.timeout)
        except BrokenProcessPool:
            self._stop(i, kill=True)
            return False, CRASHED
        except FuturesTimeout:
            self._stop(i, kill=True)
            return False, (f"the engine did not finish this model's SQL on generated inputs in "
                           f"{int(self.timeout)} seconds")
        finally:
            self._free.put(i)

    def map(self, fn, items: list) -> list:
        """`fn(item)` for each item, one thread per slot, results in order. `fn` calls `run`."""
        if self.slots <= 1 or len(items) <= 1:
            return [fn(x) for x in items]
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=self.slots) as ex:
            return list(ex.map(fn, items))
