"""L1: the rules assay enforces are proven in Lean, and the two sides cannot drift apart."""
import os

import pytest

from dbt_assay import proofs
from dbt_assay.config import known_checks


def test_every_mapped_theorem_exists_in_the_lean_library():
    have = proofs.theorems_in_source()
    assert len(have) >= 6, "the theorem reader found almost nothing; it is broken"
    wanted = {t for rows in (*proofs.RULES_PROVEN.values(), *proofs.DERIVATIONS_PROVEN.values())
              for t, _why in rows}
    missing = sorted(wanted - have)
    assert not missing, f"RULES_PROVEN names theorems Rules.lean does not have: {missing}"


def test_the_six_l1_theorems_are_all_there():
    assert {"inner_join_no_fanout", "left_join_preserves_rows", "grain_through_join",
            "filter_preserves_unique", "pick_is_order_independent",
            "pick_total_on_unique_key"} <= proofs.theorems_in_source()


def test_every_mapped_rule_is_a_real_check():
    real = set(known_checks())
    assert not sorted(set(proofs.RULES_PROVEN) - real)


def test_a_finding_from_a_proven_rule_carries_its_theorem():
    from types import SimpleNamespace

    from dbt_assay.inventory import ModelEntry
    from dbt_assay.judged import hop_multiplies_rows
    e = ModelEntry(uid="model.p.c", name="c", path="c.sql", layer="marts", materialized="table")
    e.fanout_hops = [("lookup -> c", 0.8)]
    (f,) = proofs.stamp(hop_multiplies_rows(SimpleNamespace(), [e]))
    assert f.evidence["proven_rule"] == "inner_join_no_fanout"
    import inspect

    from dbt_assay import live
    assert "stamp(fs)" in inspect.getsource(live._reach), "the stream no longer stamps"


@pytest.mark.skipif(proofs.lake_bin() is None and not os.environ.get("ASSAY_REQUIRE_LEAN"),
                    reason="no Lean toolchain here; CI's lean job runs this with one")
def test_the_library_builds_with_no_sorry_and_only_standard_axioms():
    got = proofs.check_library()
    assert got["built"], got.get("why")
    assert not got["sorry"], got["sorry"]
    assert not got["bad"], got["bad"]
    assert set(got["axioms"]) == set(got["theorems"]), "a theorem's axioms were not read"
    assert got["ok"]


@pytest.mark.skipif(proofs.lake_bin() is None, reason="no Lean toolchain here")
def test_a_sorry_is_refused(tmp_path):
    import shutil
    lib = tmp_path / "lean"
    shutil.copytree(proofs.LEAN_DIR, lib, ignore=shutil.ignore_patterns(".lake"))
    (lib / "Assay" / "Rules.lean").write_text(
        (lib / "Assay" / "Rules.lean").read_text().replace(
            "end Assay", "theorem cheat : 1 = 2 := by sorry\n\nend Assay"))
    got = proofs.check_library(lib)
    assert not got["ok"] and got["sorry"]
