"""`docs/SCHEMA.md` describes the store. A schema doc that drifts is worse than none.

*** IT IS THE ONE DOCUMENT SOMEBODY READS INSTEAD OF THE DDL. ***
Every other doc here can be checked against the thing it describes by running the thing. This one
gets read precisely so nobody has to, which makes a stale entry a wrong answer delivered with
confidence -- the defect this whole project is about.
"""
import re
from pathlib import Path

import pytest

import dbt_assay
from dbt_assay.store import Store

ROOT = Path(dbt_assay.__file__).parent.parent.parent
DOC = ROOT / "docs" / "SCHEMA.md"


def _live_schema(tmp_path) -> dict:
    """{table: {columns}} from a store the code just created. The DDL is the authority."""
    s = Store(str(tmp_path / "s.duckdb"))
    # the probe and jev tables are created lazily by their own modules
    from dbt_assay import probe
    s.con.execute(probe.DDL)
    out = {}
    for (t,) in s.con.execute(
            "select table_name from information_schema.tables order by 1").fetchall():
        out[t] = {c[0] for c in s.con.execute(f'describe "{t}"').fetchall()}
    return out


def _doc_entities() -> dict:
    """{TABLE: {columns}} from the mermaid block."""
    body = DOC.read_text()
    block = body[body.index("```mermaid"):body.index("```", body.index("```mermaid") + 10)]
    out = {}
    for m in re.finditer(r"^    ([A-Z_]+) \{\n(.*?)^    \}", block, re.DOTALL | re.MULTILINE):
        cols = set()
        for line in m.group(2).splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and not parts[0].startswith('"'):
                cols.add(parts[1])
        out[m.group(1)] = cols
    return out


@pytest.fixture
def live(tmp_path):
    return _live_schema(tmp_path)


def test_every_table_in_the_store_is_in_the_diagram(live):
    if not DOC.exists():
        pytest.skip("no docs in a wheel install")
    doc = _doc_entities()
    assert len(doc) > 5, "the diagram reader found almost nothing; it is broken"
    missing = sorted(t for t in live if t.upper() not in doc)
    assert not missing, f"tables the store creates and the diagram omits: {missing}"


def test_every_entity_in_the_diagram_is_a_real_table(live):
    if not DOC.exists():
        pytest.skip("no docs in a wheel install")
    extra = sorted(e for e in _doc_entities() if e.lower() not in live)
    assert not extra, f"the diagram invents tables that do not exist: {extra}"


def test_every_column_in_the_diagram_exists(live):
    """A column renamed in the DDL and not here is a query somebody writes that does not run."""
    if not DOC.exists():
        pytest.skip("no docs in a wheel install")
    wrong = []
    for ent, cols in _doc_entities().items():
        real = live.get(ent.lower(), set())
        wrong += [f"{ent}.{c}" for c in sorted(cols - real)]
    assert not wrong, f"columns in the diagram that the store does not have: {wrong}"


def test_the_prune_split_in_the_doc_is_the_one_in_the_code():
    """The doc quotes both tuples, and the split is the whole design. It must not be a paraphrase."""
    if not DOC.exists():
        pytest.skip("no docs in a wheel install")
    from dbt_assay.store import NEVER_PRUNED, PRUNABLE
    body = DOC.read_text()
    for name, vals in (("PRUNABLE", PRUNABLE), ("NEVER_PRUNED", NEVER_PRUNED)):
        assert name in body, f"{name} is not quoted in the schema doc"
        for t in vals:
            assert f'"{t}"' in body, f"{name} is missing {t} in the doc"


def test_the_decision_key_grammar_is_documented():
    """*** THE JOINS ARE STRING GRAMMAR, NOT CONSTRAINTS. ***

    `decision_key` and `adjudications.subject` carry `::claim::`, `::edge::`, `::finding::` and
    friends. Nothing in the DDL says so, so anybody writing a query against this store either
    reads it here or discovers it by getting an empty join.
    """
    if not DOC.exists():
        pytest.skip("no docs in a wheel install")
    body = DOC.read_text()
    for shape in ("::claim::", "::edge::", "::finding::", "::pred::"):
        assert shape in body, f"{shape} is not in the schema doc"


def test_every_table_has_a_prune_policy(live):
    """*** `states` WAS IN NEITHER LIST, WHICH IS THE ONE THING THE SPLIT FORBIDS. ***

    A table carrying `run_id` is one `assay check` rebuilds for free. One without it cost a model
    call or somebody's afternoon. `prune` deletes the first kind, and a table in neither list is a
    table whose cost nobody decided -- it is simply never considered, which is silence rather than
    a decision.

    `SCHEMA.md` claimed "a new table belongs to one list or the other and a test fails until it
    does" while no such test existed: a documentation claim the code did not support, in the tool
    whose largest check family is `code_contradicts_a_claim`.
    """
    from dbt_assay.store import NEVER_PRUNED, PRUNABLE
    declared = set(PRUNABLE) | set(NEVER_PRUNED)
    assert len(live) > 5, "the table reader found almost nothing; it is broken"
    unassigned = sorted(set(live) - declared)
    assert not unassigned, (
        f"no prune policy for: {unassigned}. Every table is either free to rebuild (PRUNABLE) or "
        f"cost something (NEVER_PRUNED). There is no third answer.")
    phantom = sorted(declared - set(live))
    assert not phantom, f"a prune list names tables that do not exist: {phantom}"


def test_the_two_prune_lists_do_not_overlap():
    """A table cannot be both free to rebuild and expensive to lose."""
    from dbt_assay.store import NEVER_PRUNED, PRUNABLE
    both = set(PRUNABLE) & set(NEVER_PRUNED)
    assert not both, both
