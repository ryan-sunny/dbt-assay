from dbt_assay import contracts, relate
from dbt_assay.contracts import GrainCandidate
from dbt_assay.infer import Schema, derive_columns
from dbt_assay.manifest import Project
from dbt_assay.parse import digest


def _setup(project_dir):
    p = Project.load(project_dir)
    d = {uid: digest(m.compiled, m.name) for uid, m in p.models.items() if m.readable}
    sch = Schema.load(p, project_dir)
    derive_columns(p, d, sch)
    return p, d, sch, relate.declared_keys(p)


def test_criteria_keys_survive_yaml_boolean_coercion():
    """Bare `true:` in YAML 1.1 is the BOOLEAN True, and every lookup by "true" would raise."""
    assert set(contracts.KEY_Q["criteria"]) == {"true", "false"}


def test_one_noul_per_column_never_one_question_over_the_list():
    qs = contracts.key_questions(GrainCandidate(["a", "b", "c"], "group_by", ""))
    assert list(qs) == ["key__a", "key__b", "key__c"]
    assert all(q["type"] == "noul" for q in qs.values())
    # the column under judgment must be IN the question: ids are for code and are not sent
    assert qs["key__b"]["instructions"]["column"] == "b"


def test_candidates_prefer_the_group_by(project_dir):
    p, d, sch, declared = _setup(project_dir)
    c = contracts.candidates("model.p.int_ok_unique", p, d, sch, {}, declared)
    assert c.route == "group_by"
    assert sorted(c.columns) == ["case_id", "section_id"]


def test_grain_propagation_uses_graph_position_not_folder_names(project_dir):
    """A project with every model in one flat directory must behave identically."""
    p, d, sch, declared = _setup(project_dir)
    for m in p.models.values():
        m.layer = "other"
    props = contracts.propose_all(p, d, sch, declared)
    assert contracts.candidates("model.p.int_ok_unique", p, d, sch, {}, declared).route == "group_by"
    assert props


def test_a_placeholder_description_is_not_sent_as_evidence(project_dir):
    p, d, sch, declared = _setup(project_dir)
    uid = "model.p.int_ok_unique"
    p.models[uid].description = "Mart model."
    cand = contracts.candidates(uid, p, d, sch, {}, declared)
    st = contracts.build_state(uid, p, d, sch, cand, declared)
    assert "description" not in st["model"]
    p.models[uid].description = "One row per section and case, from the resume feed, since 2007."
    st2 = contracts.build_state(uid, p, d, sch, cand, declared)
    assert st2["model"]["description"]


def test_vocabulary_reaches_the_state_when_configured(project_dir):
    p, d, sch, declared = _setup(project_dir)
    uid = "model.p.int_ok_unique"
    cand = contracts.candidates(uid, p, d, sch, {}, declared)
    st = contracts.build_state(uid, p, d, sch, cand, declared, {"division": {"means": "a region"}})
    assert st["vocabulary"]["division"]["means"] == "a region"


def test_code_composes_the_key_from_independent_answers():
    cand = GrainCandidate(["section_id", "district_name", "tax_year"], "group_by", "")
    answers = {
        "key__section_id": {"kind": "noul", "answer": "0.97"},
        "key__district_name": {"kind": "noul", "answer": "0.88"},
        "key__tax_year": {"kind": "noul", "answer": "0.06"},
    }
    g = contracts.key_from_answers(cand, answers)
    assert g.columns == ["section_id", "district_name"]
    assert g.dropped == ["tax_year"]
    assert g.source == "judged"


def test_an_unanswered_column_is_kept_rather_than_silently_dropped():
    """Narrowing a key on missing evidence is worse than leaving it wide."""
    cand = GrainCandidate(["a", "b"], "group_by", "")
    g = contracts.key_from_answers(cand, {"key__a": {"kind": "noul", "answer": "0.9"}})
    assert g.columns == ["a", "b"]


def test_an_uncertain_answer_is_recorded_as_uncertain_not_as_yes():
    """A noul near 0.5 means similar probability either way. A single 0.5 cut turns 'I do not
    know' into 'yes', which is the worst of the three available answers."""
    cand = GrainCandidate(["wdid", "first_year", "years_total"], "group_by", "")
    g = contracts.key_from_answers(cand, {
        "key__wdid": {"kind": "noul", "answer": "0.80"},
        "key__first_year": {"kind": "noul", "answer": "0.53"},
        "key__years_total": {"kind": "noul", "answer": "0.15"},
    })
    assert g.columns == ["wdid", "first_year"]     # kept: narrowing on absent evidence is worse
    assert g.uncertain == ["first_year"]           # but recorded as unresolved
    assert g.dropped == ["years_total"]
