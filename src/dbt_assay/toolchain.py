"""The Lean toolchain assay runs its proofs with, managed by assay.

*** LEAN IS NOT A PYTHON PACKAGE, AND IT IS PART OF ASSAY. ***
Its toolchain is far over PyPI's size limit, so it cannot ship in the wheel. Instead each assay
release pins exactly one Lean version (`lean/lean-toolchain`), and on first use -- or ahead of
time, with `assay prove --setup` in a Docker image or CI -- downloads that version from Lean's
official releases, checks its SHA-256 against the digest pinned below, and unpacks it into
`~/.cache/assay/lean/<version>/`. It is never put on PATH and never touches an elan install.

The Lean library that ships in the package is copied beside it and compiled there once per assay
version, so the installed package directory is never written to.

`--offline` refuses to download and says what is missing.
"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import stat
import subprocess
import urllib.request
import zipfile
from pathlib import Path

VERSION = "4.34.1"
# SHA-256 of each official release archive, as GitHub publishes it for v4.34.1.
DIGESTS = {
    "darwin_aarch64": "9019fcd34e93fddf0ecf9d40d28f05a86ba563ac3b701a9c5116ce1c96379360",
    "darwin": "60b45c76003a4561fb66d7ec2003569006d255d2860179445e56d3e2a029090e",
    "linux": "3013aba02bb8bf31b1cf8c9d956d60fb95bb1c10920dfe91abcaa59cb461ddac",
    "linux_aarch64": "b4bcf1d79f1b5859c04625058d3707701d87a65815f7f0686a1f14df821954c5",
    "windows": "457698d4e2132243e899ff32178d7ae63c16431d09ed9ad524d859c3581992af",
}
URL = "https://github.com/leanprover/lean4/releases/download/v{v}/lean-{v}-{p}.zip"


def cache_root() -> Path:
    base = os.environ.get("ASSAY_CACHE") or os.environ.get("XDG_CACHE_HOME")
    return Path(base) / "assay" / "lean" if base else Path.home() / ".cache" / "assay" / "lean"


def platform_key() -> str:
    sysname = platform.system().lower()
    arch = platform.machine().lower()
    arm = arch in ("arm64", "aarch64")
    if sysname == "darwin":
        return "darwin_aarch64" if arm else "darwin"
    if sysname == "linux":
        return "linux_aarch64" if arm else "linux"
    if sysname == "windows":
        return "windows"
    raise RuntimeError(f"Lean publishes no toolchain for {sysname}/{arch}")


def home(version: str = VERSION) -> Path:
    """Where this version's toolchain is unpacked."""
    return cache_root() / version / "toolchain"


def lake_path(version: str = VERSION) -> Path | None:
    h = home(version)
    for p in (h / "bin" / "lake", h / "bin" / "lake.exe"):
        if p.exists():
            return p
    return None


def library(version: str = VERSION) -> Path:
    """The compiled copy of the shipped Lean library, per assay version."""
    from . import __version__
    return cache_root() / version / f"library-{__version__}"


def status() -> dict:
    lake = lake_for_build()
    lib = library()
    return {"lean": VERSION, "toolchain": str(home()), "installed": lake is not None,
            "library": str(lib), "library_built": (lib / ".lake" / "build").exists()}


def setup(offline: bool = False, say=print) -> dict:
    """Install the pinned toolchain if absent, then compile the library. Idempotent."""
    if lake_for_build() is None:
        if offline:
            raise RuntimeError(f"Lean {VERSION} is not installed at {home()} and --offline "
                               f"refuses to download it. Run `assay prove --setup` once where "
                               f"the network is reachable (a Docker build, CI), or copy "
                               f"{cache_root()} from a machine that has it.")
        _install(say)
    return build_library(say)


def _install(say) -> None:
    key = platform_key()
    url = URL.format(v=VERSION, p=key)
    want = DIGESTS[key]
    dest = cache_root() / VERSION
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / f"lean-{VERSION}-{key}.zip"
    if not archive.exists() or _sha256(archive) != want:
        say(f"downloading Lean {VERSION} for {key} (~800 MB, once)")
        tmp = archive.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, length=1 << 20)
        tmp.rename(archive)
    got = _sha256(archive)
    if got != want:
        archive.unlink(missing_ok=True)
        raise RuntimeError(f"the Lean archive's SHA-256 is {got}, not the pinned {want}. "
                           f"Nothing was installed.")
    say("unpacking")
    staging = dest / "staging"
    shutil.rmtree(staging, ignore_errors=True)
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            out = z.extract(info, staging)
            mode = (info.external_attr >> 16) & 0o777
            if mode:
                os.chmod(out, mode)
    top = next(staging.iterdir())
    shutil.rmtree(home(), ignore_errors=True)
    top.rename(home())
    shutil.rmtree(staging, ignore_errors=True)
    for exe in (home() / "bin").iterdir():
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    archive.unlink(missing_ok=True)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def version_of(lake: str) -> str:
    """The Lean version a `lake` binary belongs to, or ''."""
    try:
        out = subprocess.run([lake, "--version"], capture_output=True, text=True, timeout=60,
                             check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    import re
    m = re.search(r"Lean version (\d+\.\d+\.\d+)", out)
    return m.group(1) if m else ""


def lake_for_build() -> str | None:
    """The Lake to prove with: assay's own, else one already installed at EXACTLY the pinned
    version ($ASSAY_LAKE, PATH, elan). A different version is never used: the same model must
    prove the same way on every machine."""
    own = lake_path()
    if own is not None:
        return str(own)
    from .proofs import lake_bin
    other = lake_bin()
    if other and version_of(other) == VERSION:
        return other
    return None


def build_library(say=print) -> dict:
    """Copy the shipped Lean library into the cache and compile it."""
    from .proofs import LEAN_DIR
    lake = lake_for_build()
    if lake is None:
        raise RuntimeError("no Lean toolchain to build with")
    lib = library()
    lib.mkdir(parents=True, exist_ok=True)
    for p in LEAN_DIR.rglob("*"):
        if ".lake" in p.parts or p.is_dir():
            continue
        rel = p.relative_to(LEAN_DIR)
        (lib / rel).parent.mkdir(parents=True, exist_ok=True)
        if not (lib / rel).exists() or (lib / rel).read_bytes() != p.read_bytes():
            shutil.copy2(p, lib / rel)
    env = dict(os.environ)
    env["PATH"] = str(Path(lake).parent) + os.pathsep + env.get("PATH", "")
    # the toolchain file names elan's toolchain; assay's own Lean is already first on PATH
    r = subprocess.run([lake, "build"], cwd=lib, capture_output=True, text=True, env=env,
                       timeout=1800, check=False)
    if r.returncode != 0:
        raise RuntimeError("the Lean library did not build:\n" + (r.stdout + r.stderr)[-2000:])
    return {**status(), "lake": lake}
