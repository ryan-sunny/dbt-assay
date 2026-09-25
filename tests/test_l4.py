"""L4: the fragment's meaning in Lean, each parse proven, and the engine measured."""
import subprocess
import sys
from pathlib import Path

import pytest

from dbt_assay import conformance, ledger, sqlfrag, toolchain

ROOT = Path(__file__).resolve().parent.parent
needs_lean = pytest.mark.skipif(toolchain.lake_for_build() is None,
                                reason="no Lean toolchain at the pinned version here")


def test_the_generated_lean_is_not_stale(tmp_path):
    """The templates are the source; Sql/*.lean must be what the generator writes from them."""
    lean = ROOT / "src" / "dbt_assay" / "lean"
    before = {p.name: p.read_text() for p in (lean / "Sql").glob("*.lean")}
    subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_lean_sql.py")], check=True)
    after = {p.name: p.read_text() for p in (lean / "Sql").glob("*.lean")}
    assert before == after, "run scripts/gen_lean_sql.py after editing a template"


def test_the_converter_writes_the_fragment_or_says_what_is_outside():
    q = sqlfrag.query("with a as (select k, count(*) as n from t group by k) "
                      "select a.k from a left join u on a.k = u.k where a.n > 1", "duckdb")
    s = sqlfrag.s_query(q)
    assert '(fn "count" [(star []) ])' in s and '(join "LEFT" ["u"]' in s
    with pytest.raises(sqlfrag.Outside):
        sqlfrag.query("select * from (select 1) x", "duckdb")
    assert sqlfrag.lean_codes("ab") == "[97, 98]"


@needs_lean
def test_lean_reads_the_same_tree_and_the_kernel_proves_it(tmp_path):
    from dbt_assay import parseproof
    toolchain.build_library(say=lambda *_: None)
    sql = ("with a as (select k, sum(v) filter (where v > 0) as s from t group by k) "
           "select a.k, try_cast(a.s as int) as s from a union all select k, 0 from t")
    q = sqlfrag.query(sql, "duckdb")
    assert parseproof.lean_parse(sql) == sqlfrag.s_query(q)
    f = tmp_path / "P.lean"
    f.write_text(parseproof.theorem_file("m", sql, q))
    lib = toolchain.library()
    r = subprocess.run([toolchain.lake_for_build(), "env", "lean", str(f)], cwd=lib,
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0 and "error" not in r.stdout, r.stdout
    assert "depends on axioms" in r.stdout and "sorryAx" not in r.stdout
    bad = dict(q)
    bad["body"]["first"]["items"][0] = (("col", ["a"], "kk"), None)
    f.write_text(parseproof.theorem_file("m", sql, bad))
    r = subprocess.run([toolchain.lake_for_build(), "env", "lean", str(f)], cwd=lib,
                       capture_output=True, text=True, check=False)
    assert "error" in r.stdout, "a corrupted tree was accepted"


@needs_lean
def test_the_parse_proof_outranks_the_round_trip(tmp_path):
    from dbt_assay import parseproof
    from dbt_assay.inventory import build as _b  # noqa: F401
    spec = __import__("importlib.util").util.spec_from_file_location(
        "tp", Path(__file__).with_name("test_prove.py"))
    tp = __import__("importlib.util").util.module_from_spec(spec)
    spec.loader.exec_module(tp)
    target = tp.build(tmp_path)
    p, _d, sch = tp._load(target)
    from dbt_assay.store import Store
    s = Store(str(tmp_path / "s.duckdb"))
    rep = parseproof.run(p, s, target, say=lambda *_: None)
    assert rep["proven"] >= 3, rep
    led = ledger.build(p, sch, [], s, tests={}, observed={})
    pf = ledger.parse_faithful(led, "model.p.covered", s)
    assert pf.status == ledger.HOLDING and ledger.label(pf) == "parse proven"


@needs_lean
def test_the_suite_agrees_on_what_it_should_and_names_what_duckdb_does_differently(tmp_path):
    from dbt_assay.store import Store
    toolchain.build_library(say=lambda *_: None)
    s = Store(str(tmp_path / "s.duckdb"))
    rep = conformance.run(s, n_random=150, seed=3)
    bad = {k for k, v in rep["constructs"].items() if v["status"] != "holding"}
    # DuckDB's `/` on integers returns a double, and it sorts NULLs last under DESC
    assert bad == {"integer_division", "row_number_desc_nulls"}, rep["constructs"]
    assert rep["random"]["differ"] == 0
    assert conformance.status_of(s, "left_join_null_key", "duckdb")[0] == "holding"
    assert conformance.status_of(s, "left_join_null_key", "snowflake")[0] == "unchecked"


def test_the_warehouse_statement_carries_its_tables_as_ctes():
    sql = conformance.warehouse_sql({"t": (["k", "s"], [[1, "a'b"], [None, None]])},
                                    "select k from t")
    assert "t AS (SELECT 1 AS k, 'a''b' AS s UNION ALL SELECT NULL AS k, NULL AS s)" in sql
    assert sql.endswith("SELECT * FROM __assay_q")
