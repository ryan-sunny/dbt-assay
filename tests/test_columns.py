from dbt_assay import columns, relate
from dbt_assay.infer import Schema, derive_columns
from dbt_assay.manifest import Project
from dbt_assay.parse import digest


def _load(project_dir):
    p = Project.load(project_dir)
    d = {uid: digest(m.compiled, m.name) for uid, m in p.models.items() if m.readable}
    sch = Schema.load(p, project_dir)
    derive_columns(p, d, sch)
    return p, d, sch, relate.declared_keys(p)


def test_both_families_are_asked_about_one_chunk_in_one_call():
    qs = columns.questions_for(["a", "b"], ask_null=True)
    assert sorted(qs) == ["null__a", "null__b", "role__a", "role__b"]
    assert all(q["type"] == "choice" for q in qs.values())


def test_every_choice_carries_a_no_match_option():
    """The 97CW0059 division bug surfaced only because the model could say the answer was not
    on the list."""
    qs = columns.questions_for(["a"], ask_null=True)
    assert "cannot_tell" in qs["null__a"]["criteria"]
    assert "other" in qs["role__a"]["criteria"]


def test_the_column_under_judgment_is_named_inside_the_question():
    """Question ids are for code and are never sent to the model."""
    qs = columns.questions_for(["amount"])
    assert qs["role__amount"]["instructions"]["column"] == "amount"


def test_columns_are_chunked_so_repeated_criteria_stay_inside_the_budget():
    assert columns.chunks(list("abcdefghij"), 4) == [list("abcd"), list("efgh"), list("ij")]
    assert columns.CHUNK <= 12


def test_code_established_provenance_is_sent_as_fact(project_dir):
    p, d, sch, decl = _load(project_dir)
    uid = "model.p.stg_bad_notnull"
    facts = columns.facts_for(uid, p, d, sch, decl)
    st = columns.build_state(uid, p, sch, facts, ["amount"])
    entry = st["columns_under_judgment"][0]
    assert entry["provenance"] == "defaulted"
    assert "coalesce" in entry["provenance_means"].lower() or "literal" in entry["provenance_means"]


def test_the_projects_own_tests_are_harvested_as_labels(project_dir):
    p, _d, _sch, _decl = _load(project_dir)
    lab = columns.free_labels(p)
    assert lab.null_meaning[("model.p.stg_bad_notnull", "amount")] == "impossible"
    assert lab.role[("model.p.int_bad_unique", "section_id")] == "identifier"


def test_a_placeholder_description_is_still_withheld(project_dir):
    p, d, sch, decl = _load(project_dir)
    uid = "model.p.stg_bad_notnull"
    p.models[uid].description = "Staging model."
    facts = columns.facts_for(uid, p, d, sch, decl)
    st = columns.build_state(uid, p, sch, facts, ["amount"])
    assert "description" not in st["model"]


def test_a_column_that_cannot_be_null_is_not_asked_what_a_null_would_mean(project_dir):
    """v1 asked anyway and answered `unknown_value` 25 times out of 25: two judgments were hiding
    in one question."""
    p, d, sch, decl = _load(project_dir)
    uid = "model.p.stg_bad_notnull"
    facts = columns.facts_for(uid, p, d, sch, decl)
    assert facts["amount"].can_be_null is False          # coalesce with a literal tail
    qs = columns.questions_for(["amount", "id"], facts)
    assert "null__amount" not in qs
    assert "role__amount" in qs


def test_impossible_is_no_longer_an_option_in_the_null_family():
    assert "impossible" not in columns.NULL_Q["criteria"]
    assert columns.NULL_VERSION != "null.v1"


def test_the_reason_a_column_can_never_be_null_is_stated_in_the_state(project_dir):
    p, d, sch, decl = _load(project_dir)
    facts = columns.facts_for("model.p.stg_bad_notnull", p, d, sch, decl)
    st = columns.build_state("model.p.stg_bad_notnull", p, sch, facts, ["amount"])
    e = st["columns_under_judgment"][0]
    assert e["can_be_null"] is False and e["never_null_because"]


def test_the_null_family_is_off_unless_asked_for(project_dir):
    """Measured at median confidence 0.49 over six options without a null rate or a known role.
    A question asked without what it needs answers a worse question confidently."""
    p, d, sch, decl = _load(project_dir)
    facts = columns.facts_for("model.p.int_ok_unique", p, d, sch, decl)
    default = columns.questions_for(["section_id"], facts)
    assert not any(q.startswith("null__") for q in default)
    opted_in = columns.questions_for(["section_id"], facts, ask_null=True)
    assert "null__section_id" in opted_in
