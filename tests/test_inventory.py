from dbt_assay import inventory, relate
from dbt_assay.infer import Schema, derive_columns
from dbt_assay.inventory import Fact
from dbt_assay.manifest import Project
from dbt_assay.parse import digest


def _load(project_dir):
    p = Project.load(project_dir)
    d = {uid: digest(m.compiled, m.name) for uid, m in p.models.items() if m.readable}
    sch = Schema.load(p, project_dir)
    derive_columns(p, d, sch)
    return p, d, sch


def test_a_cell_never_renders_without_where_it_came_from():
    """Square brackets get eaten by rich's markup parser, and the provenance is the one part of a
    cell that must never silently disappear."""
    r = Fact(["a", "b"], "judged", 0.82).render()
    assert "judged" in r and "0.82" in r and "[" not in r


def test_a_fact_resting_on_an_unresolved_premise_says_so():
    f = Fact(["a"], "judged", 0.9, resting_on=["unresolved: b"])
    assert "unresolved premise" in f.render()
    assert not f.firm


def test_only_a_human_or_a_count_makes_a_fact_firm():
    assert Fact("x", "declared").firm
    assert Fact("x", "observed").firm
    assert not Fact("x", "derived").firm
    assert not Fact("x", "judged", 0.99).firm


def test_the_inventory_works_with_no_judgments_at_all(project_dir):
    """No API key still yields a grain where code can settle it and provenance for every column."""
    p, d, sch = _load(project_dir)
    entries = inventory.build(p, d, sch, store=None)
    assert entries
    assert all(e.columns for e in entries if not e.unreadable)
    assert all(c.provenance.value for e in entries for c in e.columns)
    assert any(e.grain for e in entries)
    assert all(c.role is None for e in entries for c in e.columns)


def test_a_declared_key_outranks_a_derived_one(project_dir):
    p, d, sch = _load(project_dir)
    entries = {e.uid: e for e in inventory.build(p, d, sch, store=None)}
    g = entries["model.p.int_bad_unique"].grain
    assert g.source == "declared" and g.value == ["section_id"]


def test_the_description_is_assembled_from_facts_not_generated(project_dir):
    p, d, sch = _load(project_dir)
    e = next(x for x in inventory.build(p, d, sch, store=None)
             if x.uid == "model.p.stg_bad_notnull")
    text = inventory.describe(e)
    assert "cannot fire" in text          # it knows `amount` is coalesce-defaulted
    assert "amount" in text


def test_an_unsettled_grain_says_so_rather_than_guessing(project_dir):
    p, d, sch = _load(project_dir)
    for uid in d:
        d[uid].group_by_columns = []
        d[uid].from_relations = []
    entries = inventory.build(p, d, sch, store=None)
    undeclared = [e for e in entries if e.uid not in relate.declared_keys(p)]
    assert all(e.grain is None for e in undeclared)
    assert any("has not been settled" in inventory.describe(e) for e in undeclared)


def test_write_out_is_a_separate_file_and_defaults_to_adjudicated_only(project_dir):
    """Inferences written into files a human maintains produce a diff on every run, and a stream
    of machine edits is how people stop reading their own pull requests."""
    p, d, sch = _load(project_dir)
    entries = inventory.build(p, d, sch, store=None)
    strict = inventory.to_yaml_dict(entries, adjudicated_only=True, adjudicated=set())
    # declared and observed facts are already human or counted, so they may pass
    assert all(v["grain_source"] in ("declared", "observed")
               for v in strict["models"].values())
    loose = inventory.to_yaml_dict(entries, adjudicated_only=False)
    assert len(loose["models"]) >= len(strict["models"])
