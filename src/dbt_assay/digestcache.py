"""Each model's parse, kept between commands, keyed by exactly what produced it.

*** NINE COMMANDS A DAY PARSED THE SAME 358 MODELS NINE TIMES. *** (sunny-data, 0.52.4) Every
command loads the project, and loading it parses every model with sqlglot: 3s on the laptop, 11s
on the box, every command of the daily run, on SQL that had not changed since the one before.

A digest is plain data (no sqlglot tree), a function of the model's compiled SQL, its name, the
dialect, and the code that read it. So is a starred model's expanded column list, given its
parents' columns, which `derive_columns` keeps here too (`memo`). The key is all four: the SQL's own hash, and a hash of assay's
source files and sqlglot's version rather than assay's version number, which stays the same while
the code under it changes. A changed model misses and is parsed; nothing is ever read stale.

The file is written with only the digests the load used, so an edited model's old parse leaves
with the next run, and a file under a key no build produces any more is removed after 14 days unused.
Set ASSAY_NO_DIGEST_CACHE=1 to parse everything every time.
"""
from __future__ import annotations

import functools
import hashlib
import os
import pickle
import time
from pathlib import Path


def cache_dir() -> Path:
    """`$ASSAY_CACHE/assay/digests`, beside the Lean toolchain."""
    return _real_cache_dir()


def _real_cache_dir() -> Path:
    base = os.environ.get("ASSAY_CACHE") or os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "assay" / "digests"


@functools.lru_cache(maxsize=1)
def code_hash() -> str:
    """assay's own source and sqlglot's version: what reads the SQL."""
    import sqlglot
    h = hashlib.sha256(str(getattr(sqlglot, "__version__", "")).encode())
    here = Path(__file__).resolve().parent
    for p in sorted(here.rglob("*.py")):
        h.update(p.relative_to(here).as_posix().encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:20]


def _key(sql: str, name: str) -> str:
    return hashlib.sha256(f"{name}\0{sql}".encode()).hexdigest()


class DigestCache:
    def __init__(self, project_name: str, dialect: str):
        self.on = not os.environ.get("ASSAY_NO_DIGEST_CACHE")
        tag = hashlib.sha256(f"{project_name}\0{dialect}".encode()).hexdigest()[:12]
        self.path = cache_dir() / f"{tag}-{code_hash()}.pickle"
        self.old: dict = {}
        self.new: dict = {}
        self.hits = 0
        if self.on:
            try:
                with open(self.path, "rb") as fh:
                    self.old = pickle.load(fh)
            except Exception:                                    # noqa: BLE001
                self.old = {}

    def get(self, sql: str, name: str, make):
        """The digest for this SQL, from the file when it is there, else `make()`."""
        k = _key(sql, name)
        kept = self.old.get(k) if self.on else None
        if kept is not None:
            self.hits += 1
            self.new[k] = kept
            return pickle.loads(kept)             # a fresh object each time it is asked for
        d = make()
        # Pickled now, before anything downstream (derive_columns) adds to the object.
        self.new[k] = pickle.dumps(d)
        return d

    def memo(self, kind: str, parts: str, make):
        """Any other plain result that is a function of `parts` alone, kept the same way."""
        k = kind + ":" + hashlib.sha256(parts.encode()).hexdigest()
        kept = self.old.get(k) if self.on else None
        if kept is not None:
            self.hits += 1
            self.new[k] = kept
            return pickle.loads(kept)
        v = make()
        self.new[k] = pickle.dumps(v)
        return v

    def save(self) -> None:
        """Write the digests this load used. A failure to write costs speed, never a result."""
        if not self.on:
            return
        if self.hits == len(self.new) == len(self.old):
            try:
                os.utime(self.path)          # in use: the 14-day sweep goes by this
            except OSError:
                pass
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
            with open(tmp, "wb") as fh:
                pickle.dump(self.new, fh)
            os.replace(tmp, self.path)
            # Each release reads under a new code hash, so the old one's file is unused from
            # then on: gone after 14 days unused (not at once, so a checkout and an installed
            # assay sharing this folder do not delete each other's on every run).
            cutoff = time.time() - 14 * 86400
            for p in self.path.parent.glob("*.pickle"):
                if p != self.path and p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
        except Exception:                                        # noqa: BLE001, S110
            pass
