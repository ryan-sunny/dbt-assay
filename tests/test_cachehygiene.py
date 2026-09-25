"""The cache never pruned (sunny-data K1: 3.2 GB of Lean, and a new set of parses per release).
Everything unused goes; nothing the running assay uses does."""
import os
import time

from typer.testing import CliRunner

from dbt_assay import __version__, cachehygiene, toolchain
from dbt_assay.cli import app
from dbt_assay.digestcache import code_hash


def _fill(root):
    lean = root / "assay" / "lean"
    (lean / toolchain.VERSION / "toolchain" / "bin").mkdir(parents=True)
    (lean / toolchain.VERSION / "toolchain" / "bin" / "lake").write_bytes(b"x" * 100)
    (lean / toolchain.VERSION / f"library-{__version__}").mkdir()
    (lean / toolchain.VERSION / f"library-{__version__}" / "a").write_bytes(b"x" * 10)
    (lean / toolchain.VERSION / "library-0.0.1").mkdir()
    (lean / toolchain.VERSION / "library-0.0.1" / "a").write_bytes(b"x" * 20)
    (lean / "4.0.0" / "toolchain").mkdir(parents=True)
    (lean / "4.0.0" / "toolchain" / "big").write_bytes(b"x" * 1000)
    dg = root / "assay" / "digests"
    dg.mkdir(parents=True)
    (dg / f"abc-{code_hash()}.pickle").write_bytes(b"x" * 5)
    (dg / "abc-oldhash.pickle").write_bytes(b"x" * 7)
    t = root / "assay" / "dbt-target"
    (t / "fresh").mkdir(parents=True)
    (t / "old").mkdir()
    (t / "old" / "m").write_bytes(b"x" * 3)
    past = time.time() - 40 * 86400
    os.utime(t / "old", (past, past))
    os.utime(lean / toolchain.VERSION / "library-0.0.1", (past, past))


def test_unused_parts_go_and_the_running_assays_stay(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSAY_CACHE", str(tmp_path))
    _fill(tmp_path)
    used = {p["part"] for p in cachehygiene.parts() if p["in_use"]}
    assert f"Lean {toolchain.VERSION} toolchain" in used
    dry = cachehygiene.prune(dry_run=True)
    assert dry["freed"] == 1000 + 20 + 7 + 3
    assert (tmp_path / "assay/lean/4.0.0").exists()          # a dry run deletes nothing
    cachehygiene.prune()
    lean = tmp_path / "assay/lean"
    assert not (lean / "4.0.0").exists() and not (lean / toolchain.VERSION / "library-0.0.1").exists()
    assert (lean / toolchain.VERSION / "toolchain/bin/lake").exists()
    assert (lean / toolchain.VERSION / f"library-{__version__}").exists()
    assert (tmp_path / f"assay/digests/abc-{code_hash()}.pickle").exists()
    assert not (tmp_path / "assay/digests/abc-oldhash.pickle").exists()
    assert (tmp_path / "assay/dbt-target/fresh").exists()
    assert not (tmp_path / "assay/dbt-target/old").exists()


def test_installing_lean_removes_the_other_versions(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSAY_CACHE", str(tmp_path))
    _fill(tmp_path)
    recent = tmp_path / "assay/lean" / toolchain.VERSION / "library-0.0.2"
    recent.mkdir()                     # another assay on this machine, used today: kept
    said = cachehygiene.after_install()
    assert any("Lean 4.0.0" in s for s in said) and any("0.0.1" in s for s in said)
    assert (tmp_path / "assay/digests/abc-oldhash.pickle").exists()   # not Lean: left alone
    assert recent.exists()


def test_prune_cache_says_what_it_freed(tmp_path):
    _fill(tmp_path)
    r = CliRunner().invoke(app, ["prune", "--cache", "--dry-run"],
                           env={"ASSAY_CACHE": str(tmp_path), "COLUMNS": "200"})
    assert r.exit_code == 0, r.output
    assert "Lean 4.0.0 toolchain" in r.output and "would free" in r.output
    assert (tmp_path / "assay/lean/4.0.0").exists()
