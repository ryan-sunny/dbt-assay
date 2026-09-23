"""The sentence somebody wrote about a column, which was the one thing nothing read.

*** A ROLE WAS JUDGED FROM THE NAME AND THE SQL AND NEVER FROM THE DESCRIPTION. ***
`schema.yml` carries a sentence per column. The column subject state held the name, the
expression, the roots and the sibling names -- and not the sentence. On a real warehouse
`decreed_use_codes` came back at 0.52 confidence, which is a model saying it cannot tell, beside
a description that says exactly.

*** AND THE DESCRIPTIONS THEMSELVES WERE NEVER CHECKED. ***
Three facts, all free, all from the manifest: a column nobody wrote a sentence for, one name
described two different ways in two models, and a sentence the SQL contradicts.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from dbt_assay.checks import columns as cc
from dbt_assay.subjects import described


def _model(name, cols, layer="marts", path=None, package=""):
    return NS(unique_id=f"model.p.{name}", name=name, layer=layer,
              path=path or f"models/{layer}/{name}.sql", columns=cols, package=package,
              project="", description="")


def _project(*models, marts=1):
    by_uid = {m.unique_id: m for m in models}
    return NS(models=by_uid, sources={},
              blast_radius=lambda uid: {"descendants": marts, "marts": marts})


def _schema(mapping):
    return NS(columns=lambda uid: NS(names=list(mapping.get(uid, []))))


# ------------------------------------------------------------------ the sentence reaches the state

def test_a_column_description_is_read_off_the_model():
    m = _model("orders", {"id": {"description": "the order id"},
                          "amount": {"description": "  in cents  "},
                          "note": {"description": ""},
                          "other": {}})
    assert described(m) == {"id": "the order id", "amount": "in cents"}


def test_the_description_travels_in_the_column_subject_state():
    """*** IT IS THE ONLY PART OF THAT STATE A PERSON WROTE ON PURPOSE. ***
    Everything else is derived from the name and the SQL, which is what the judgment was
    struggling with."""
    from dbt_assay import subjects

    m = _model("orders", {"status": {"description": "one of new, shipped, cancelled"}})
    project = _project(m)
    digests = {m.unique_id: NS(output_exprs={"status": "s.status"}, output_roots={},
                               resolved_roots={}, ok=True)}
    subs = subjects._columns(project, digests, _schema({m.unique_id: ["status"]}))
    assert len(subs) == 1
    assert subs[0].state["description"] == "one of new, shipped, cancelled"


# ------------------------------------------------------------------------ what is not written down

def test_undocumented_columns_are_one_finding_per_model_not_one_per_column():
    """*** 358 MODELS x 40 COLUMNS WOULD SWAMP EVERY OTHER FAMILY. ***
    Nobody rules on "write a description" four hundred times; it is one decision per model."""
    m = _model("orders", {"id": {"description": "the order id"}})
    got = cc.column_has_no_description(
        _project(m), None, _schema({m.unique_id: ["id", "amount", "status", "note"]}))
    assert len(got) == 1
    assert got[0].evidence["missing"] == ["amount", "note", "status"]
    assert got[0].evidence["missing_total"] == 3


def test_a_fully_documented_model_raises_nothing():
    m = _model("orders", {"id": {"description": "a"}, "amount": {"description": "b"}})
    assert cc.column_has_no_description(
        _project(m), None, _schema({m.unique_id: ["id", "amount"]})) == []


def test_an_ingestion_model_is_the_loud_one():
    """*** THERE IS NO UPSTREAM TO ASK. ***
    Everywhere else provenance answers "where did this come from" without prose. At the edge of
    the warehouse, a column undocumented here is undocumented everywhere."""
    stg = _model("stg_orders", {}, layer="staging")
    mart = _model("fct_orders", {}, layer="marts")
    sch = _schema({stg.unique_id: ["id"], mart.unique_id: ["id"]})
    got = {f.subject_name: f for f in cc.column_has_no_description(
        _project(stg, mart), None, sch)}
    assert got["stg_orders"].base > got["fct_orders"].base
    assert got["stg_orders"].evidence["ingestion"] is True
    assert "ingestion model" in got["stg_orders"].summary
    assert "ingestion model" not in got["fct_orders"].summary


def test_a_model_whose_columns_nothing_knows_is_not_reported_as_documented():
    """An empty column list is a gap in what assay can see, not a clean bill."""
    m = _model("orders", {})
    assert cc.column_has_no_description(_project(m), None, _schema({})) == []


# --------------------------------------------------------------- one name, two different sentences

def test_two_models_describing_one_column_differently_is_a_finding():
    """*** THE `section_id` PROBLEM, GENERALISED. ***
    Invisible to every check that reads one model at a time, and it is the one that cost real
    money."""
    a = _model("stg_parcels", {"section_id": {"description": "the PLSS section"}})
    b = _model("fct_parcels", {"section_id": {"description": "our internal parcel grouping"}})
    got = cc.models_disagree_about_a_column(_project(a, b))
    assert len(got) == 1
    assert got[0].evidence["column"] == "section_id"
    assert got[0].evidence["variants"] == 2
    assert "2 different ways" in got[0].summary


def test_the_same_sentence_in_two_models_is_agreement_not_a_finding():
    a = _model("stg_parcels", {"section_id": {"description": "The PLSS section"}})
    b = _model("fct_parcels", {"section_id": {"description": "  the plss section  "}})
    assert cc.models_disagree_about_a_column(_project(a, b)) == [], \
        "whitespace and case are not a disagreement"


# ------------------------------------------------------ a promise the column is keeping falsely

def test_always_populated_over_a_coalesce_default_is_two_true_statements():
    """*** THE COLUMN IS NEVER NULL BECAUSE A LITERAL WAS PUT THERE. ***
    So "always populated" is a fact about the column and not about the data, and everything
    downstream reads the second meaning."""
    m = _model("orders", {"status": {"description": "always populated for every order"}})
    digests = {m.unique_id: NS(output_exprs={"status": "COALESCE(s.status, 'unknown')"})}
    got = cc.description_promises_what_the_column_cannot_keep(_project(m), digests)
    assert len(got) == 1
    assert got[0].evidence["default"] == "'unknown'"
    assert got[0].evidence["column"] == "status"


def test_a_description_making_no_promise_is_left_alone():
    m = _model("orders", {"status": {"description": "the order status"}})
    digests = {m.unique_id: NS(output_exprs={"status": "COALESCE(s.status, 'unknown')"})}
    assert cc.description_promises_what_the_column_cannot_keep(_project(m), digests) == []


# --------------------------------------------------------------- seeding the vocabulary from them

def test_a_word_the_project_already_defined_is_proposed_with_the_sentence_filled_in():
    """*** EVERY OTHER VOCAB RULE RETURNS `means:` EMPTY, ON PURPOSE. ***

    This one does not, and the difference is the whole justification: a person wrote this
    sentence, about this warehouse, and it is quoted verbatim with the model it came from rather
    than composed from a column name.
    """
    from dbt_assay.config import Config
    from dbt_assay.suggest import _vocab_from_descriptions

    a = _model("stg_wells", {"diversion_point": {"description": "where water leaves the stream"}})
    b = _model("fct_wells", {"diversion_point": {"description": "where water leaves the stream"}})
    got = _vocab_from_descriptions(Config(), _project(a, b))
    assert len(got) == 1
    assert got[0].key == "diversion_point"
    assert 'means: "where water leaves the stream"' in got[0].draft
    assert "not written by assay" in got[0].draft


def test_a_word_described_two_ways_is_never_lifted():
    """That disagreement is a FINDING. Picking one of the two sentences here would be the
    arbitrary pick this tool checks other people's SQL for."""
    from dbt_assay.config import Config
    from dbt_assay.suggest import _vocab_from_descriptions

    a = _model("stg_wells", {"diversion_point": {"description": "where water leaves"}})
    b = _model("fct_wells", {"diversion_point": {"description": "a headgate"}})
    assert _vocab_from_descriptions(Config(), _project(a, b)) == []


def test_a_word_described_in_one_model_is_a_comment_not_a_term():
    from dbt_assay.config import Config
    from dbt_assay.suggest import _vocab_from_descriptions

    a = _model("stg_wells", {"diversion_point": {"description": "where water leaves"}})
    assert _vocab_from_descriptions(Config(), _project(a)) == []


def test_a_word_already_in_the_vocab_is_not_proposed_again():
    from dbt_assay.config import Config
    from dbt_assay.suggest import _vocab_from_descriptions

    cfg = Config(vocab={"diversion_point": {"means": "already said"}})
    a = _model("stg_wells", {"diversion_point": {"description": "where water leaves"}})
    b = _model("fct_wells", {"diversion_point": {"description": "where water leaves"}})
    assert _vocab_from_descriptions(cfg, _project(a, b)) == []


# ------------------------------------------- a question can only ask what its subject can answer

def test_the_declared_state_fields_are_what_the_builders_actually_produce(project_dir):
    """*** DECLARED, AND A TEST FAILS UNTIL THE DECLARATION IS TRUE. ***

    `STATE_FIELDS` is what the lint checks a question against, so a key added to a builder and
    not to the map means a legitimate question gets flagged, and a key removed from a builder and
    left in the map means the broken one does not. Either way somebody is misled by a rule that
    exists to stop them being misled.
    """
    from dbt_assay import subjects
    from dbt_assay.infer import Schema
    from dbt_assay.manifest import Project
    from dbt_assay.parse import digest

    project = Project.load(str(project_dir))
    digests = {uid: digest(m.compiled or "", dialect="duckdb")
               for uid, m in project.models.items()}
    schema = Schema(project)

    seen: dict = {}
    for kind in ("model", "edge", "column", "predicate", "expression", "window", "finding"):
        for s in subjects.build(kind, subjects.SubjectSource(
                project=project, digests=digests, schema=schema)):
            seen.setdefault(kind, set()).update(s.state or {})
    assert seen, "the builders produced no subjects at all; this test is not testing anything"

    for kind, produced in sorted(seen.items()):
        declared = subjects.STATE_FIELDS[kind]
        undeclared = sorted(produced - declared)
        assert not undeclared, (
            f"`{kind}` state carries {undeclared}, which STATE_FIELDS does not declare. A "
            f"question asking about one of those would be flagged as impossible when it is not.")
