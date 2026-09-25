"""What assay keeps in its cache folder, how big each part is, and removing what nothing uses.

*** 3.2 GB OF LEAN, AND NOTHING EVER LEFT. *** (sunny-data box, 0.53.1) Everything under
`$ASSAY_CACHE/assay` is rebuilt on demand, and nothing removed any of it: a Lean bump installs the
new toolchain beside the old one, each assay release compiles its own copy of the Lean library,
and each release reads digests under a new code hash.

Everything here can be rebuilt, so removing it costs time and never a result. Nothing is removed
that the running assay uses: its pinned Lean version, its own library build, its current digests.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

STALE_DAYS = 14


def root() -> Path:
    base = os.environ.get("ASSAY_CACHE") or os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "assay"


def size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    n = 0
    for dirpath, _dirs, files in os.walk(p):
        for f in files:
            try:
                n += os.lstat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
    return n


def human(n: float) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit != "MB" else f"{n:.1f} MB"
        n /= 1024
    return f"{n:.1f} GB"


def _age_days(p: Path) -> float:
    try:
        return (time.time() - p.stat().st_mtime) / 86400
    except OSError:
        return 0.0


def parts() -> list[dict]:
    """Each thing in the cache: {part, path, bytes, in_use, why} for a person to read."""
    from . import __version__, toolchain
    from .digestcache import code_hash
    out: list[dict] = []
    lean = root() / "lean"
    if lean.is_dir():
        for v in sorted(p for p in lean.iterdir() if p.is_dir()):
            tc = v / "toolchain"
            if tc.exists():
                pinned = v.name == toolchain.VERSION
                out.append({"part": f"Lean {v.name} toolchain", "path": str(tc),
                            "bytes": size(tc), "in_use": pinned,
                            "why": "the pinned version" if pinned else
                                   f"this assay pins Lean {toolchain.VERSION}"})
            for lib in sorted(v.glob("library-*")):
                mine = (v.name == toolchain.VERSION and lib.name == f"library-{__version__}")
                out.append({"part": f"Lean library, assay {lib.name.removeprefix('library-')}",
                            "path": str(lib), "bytes": size(lib), "in_use": mine,
                            "why": "this assay's build" if mine else
                                   "built by another assay version"})
    dg = root() / "digests"
    if dg.is_dir():
        cur = [p for p in dg.glob("*.pickle") if p.stem.endswith(code_hash())]
        old = [p for p in dg.glob("*.pickle") if p not in cur]
        if cur:
            out.append({"part": "parse cache, this assay", "path": str(dg),
                        "bytes": sum(size(p) for p in cur), "in_use": True, "why": "current",
                        "files": [str(p) for p in cur]})
        if old:
            out.append({"part": "parse cache, other assay versions", "path": str(dg),
                        "bytes": sum(size(p) for p in old), "in_use": False,
                        "why": "written by another assay version", "files": [str(p) for p in old]})
    tgt = root() / "dbt-target"
    if tgt.is_dir():
        for p in sorted(x for x in tgt.iterdir() if x.is_dir()):
            age = _age_days(p)
            out.append({"part": f"dbt target {p.name}", "path": str(p), "bytes": size(p),
                        "in_use": age < STALE_DAYS,
                        "why": (f"used {age:.0f} day(s) ago" if age < STALE_DAYS else
                                f"unused for {age:.0f} days")})
    return out


def prune(dry_run: bool = False) -> dict:
    """Remove every part nothing uses. Returns {removed: [...], freed: bytes, kept: bytes}."""
    removed, freed, kept = [], 0, 0
    for p in parts():
        if p["in_use"]:
            kept += p["bytes"]
            continue
        removed.append(p)
        freed += p["bytes"]
        if dry_run:
            continue
        for f in p.get("files") or [p["path"]]:
            fp = Path(f)
            if fp.is_dir():
                shutil.rmtree(fp, ignore_errors=True)
            else:
                fp.unlink(missing_ok=True)
    if not dry_run:
        lean = root() / "lean"
        if lean.is_dir():
            for v in lean.iterdir():
                if v.is_dir() and not any(v.iterdir()):
                    v.rmdir()
    return {"removed": removed, "freed": freed, "kept": kept}


def after_install() -> list[str]:
    """Called once a Lean toolchain or library was installed: remove other Lean versions and
    other assays' library builds, which that install just made unused. Said, never silent."""
    got = prune_lean()
    return [f"removed {p['part']} ({human(p['bytes'])}): {p['why']}" for p in got]


def prune_lean() -> list[dict]:
    """Other Lean versions' toolchains at once (the 3.2 GB), and other assay versions' library
    builds once unused for STALE_DAYS: two assays sharing one cache (a checkout and an install)
    would otherwise delete each other's build on every run."""
    out = []
    for p in parts():
        if not p["part"].startswith("Lean") or p["in_use"]:
            continue
        if "library" in p["part"] and _age_days(Path(p["path"])) < STALE_DAYS:
            continue
        shutil.rmtree(p["path"], ignore_errors=True)
        out.append(p)
    lean = root() / "lean"
    if lean.is_dir():
        for v in lean.iterdir():
            if v.is_dir() and not any(v.iterdir()):
                v.rmdir()
    return out
