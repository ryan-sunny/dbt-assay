"""Drafts of the hand-written surfaces: proposals keyed to evidence, never text assay wrote."""
from dbt_assay import reviewform, suggest
from dbt_assay.cli import _load
from dbt_assay.inventory import build as build_entries


def _setup(project_dir):
    project, digests, _f, schema, _s = _load(project_dir)
    return project, digests, schema, build_entries(project, digests, schema, None, {})


def test_every_draft_line_says_where_its_words_came_from(project_dir):
    project, digests, schema, entries = _setup(project_dir)
    out = suggest._descriptions(project, entries, schema, digests)
    assert out, "the fixture has undescribed columns"
    for sug in out:
        assert sug.section == "descriptions"
        assert sug.draft.startswith("# for ") and "DRAFT" in sug.draft
        assert all(("quoted" in m) or ("composed from recorded facts" in m) or
                   m.startswith("...") for m in sug.measured)


def test_a_composed_draft_quotes_the_expression(project_dir):
    project, digests, schema, entries = _setup(project_dir)
    out = {s.key: s for s in suggest._descriptions(project, entries, schema, digests)}
    d = out.get("stg_bad_notnull")
    assert d and "COALESCE" in d.draft.upper(), d and d.draft


def test_a_description_somebody_wrote_upstream_is_quoted_not_composed(project_dir):
    import json
    m = json.loads((project_dir / "manifest.json").read_text())
    m["nodes"]["model.p.stg_bad_notnull"]["columns"] = {
        "amount": {"name": "amount", "description": "What the customer paid, in cents."}}
    (project_dir / "manifest.json").write_text(json.dumps(m))
    project, digests, schema, entries = _setup(project_dir)
    for e in entries:
        if e.name == "int_bad_unique":
            for c in e.columns:
                c.provenance.value, c.provenance.origin = "carried", \
                    schema.relation["model.p.stg_bad_notnull"]
                c.name = "amount"
    out = {s.key: s for s in suggest._descriptions(project, entries, schema, digests)}
    assert "What the customer paid, in cents." in out["int_bad_unique"].draft
    assert any("quoted from stg_bad_notnull" in x for x in out["int_bad_unique"].measured)


def test_a_vocab_meaning_is_selected_only_from_a_majority(project_dir):
    project, *_ = _setup(project_dir)

    class M:
        def __init__(self, name, text):
            self.name, self.columns = name, {"k": {"description": text}}
            self.description, self.path = "", "models/x.sql"
    project.models = {f"u{i}": M(f"m{i}", t) for i, t in
                      enumerate(["one row per parcel", "one row per parcel", "a parcel id"])}
    from unittest.mock import patch
    with patch("dbt_assay.subjects.described",
               side_effect=lambda m: {c: v["description"] for c, v in m.columns.items()}):
        sentence, where, _o = suggest._majority_sentence(project, "k")
        assert sentence == "one row per parcel" and where.startswith("2 of 3")
        project.models["u3"] = M("m3", "a parcel id")
        assert suggest._majority_sentence(project, "k")[0] == "", "a tie is not a majority"


def test_an_unfinished_accept_reason_is_not_recorded():
    ok, bad = reviewform.load({"verdicts": [
        {"subject": "a", "question": "q", "verdict": "accept",
         "note": "x is described 3 ways. It stays because "}]})
    assert not ok and "draft" in bad[0]
