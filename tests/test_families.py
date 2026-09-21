"""The four families added last: feeds, alignment, test quality, row adjudication."""
from dbt_assay import align, feeds, probe, rows, testing
from dbt_assay.inventory import ColumnEntry, Fact, ModelEntry

# --------------------------------------------------------------------------- feeds

def test_a_sample_query_carries_no_limit_because_dbt_show_adds_one():
    """`limit 8 limit 5` dies on a parser error, and a failed sample just looks like an empty
    table, so the failure is silent."""
    sql = probe.sample_sql("db.s.t", ["a", "b"], 8)
    assert "limit" not in sql.lower()
    assert probe.run_sql.__doc__


def test_a_placeholder_is_found_by_counting_not_by_asking():
    got = probe.sentinel_findings("r", ["a", "b"], {"min_0": -9999, "max_0": 5,
                                                    "min_1": 0, "max_1": 10})
    assert [c for c, _v, _w in got] == ["a"]
    assert "never clamped" in got[0][2]


def test_a_legitimate_zero_is_not_treated_as_a_placeholder():
    assert probe.sentinel_findings("r", ["a"], {"min_0": 0, "max_0": 0}) == []


def test_the_feed_layer_samples_every_column_not_the_key_candidates():
    """Targeting for the PROBE picks possible keys. A drifting feed hides in the other columns."""
    from types import SimpleNamespace

    cols = SimpleNamespace(names=["order_id", "county", "amount", "email"])
    sch = SimpleNamespace(columns=lambda _uid: cols)
    got = feeds.columns_to_sample(sch, "u", ["order_id"])
    assert "county" in got and "amount" in got


def test_a_unit_question_is_only_asked_where_there_are_numbers():
    subj = feeds.FeedSubject(relation="r", uid="u", columns=["a", "b"],
                             sample=[{"a": 1, "b": "x"}],
                             profile={"row_count": 10, "min_0": 1, "max_0": 9, "num_0": 10,
                                      "min_1": None, "max_1": None, "num_1": 0})
    qs = feeds.questions_for(["a", "b"], subj)
    assert "unit__a" in qs and "unit__b" not in qs
    assert "name__a" in qs and "name__b" in qs


# --------------------------------------------------------------------------- alignment

def test_a_label_is_the_two_sides_of_one_equality():
    """Pairing every column in an ON clause produced ('bad','city') and 131 other nonsense
    labels: a compound join is two separate claims, not one."""
    import inspect
    src = inspect.getsource(align.joined_pairs)
    assert "exp.EQ" in src and "two sides of one equality" in src.lower()


def test_noise_suffixes_do_not_make_two_concepts_different():
    assert align.relatedness("county", "county_name") == 1.0
    assert align.relatedness("county", "city_name") == 0.0
    assert align.tokens("parcel_id") == {"parcel"}


def test_routing_rounds_to_the_nearest_level_with_no_threshold_to_tune():
    assert align.route({"answer": "0.2"}) == "different"
    assert align.route({"answer": "1.0"}) == "review"
    assert align.route({"answer": "1.8"}) == "same"
    assert align.route({"answer": "not a number"}) == "review"


def _col_entry(model, cols):
    e = ModelEntry(uid=f"model.p.{model}", name=model, path="p.sql", layer="marts",
                   materialized="table")
    e.columns = [ColumnEntry(name=c, provenance=Fact("carried", "derived"),
                             role=Fact(r, "judged", 0.9) if r else None) for c, r in cols]
    return e


def test_labeled_pairs_are_put_first_or_calibration_never_happens():
    """Walking columns in order filled the cap with unlabeled pairs and reported '0 already
    asserted' on a project that had 26 such assertions."""
    entries = [_col_entry("a", [("zzz_thing", "identifier"), ("county", "dimension")]),
               _col_entry("b", [("zzz_thing_name", "identifier"), ("county_name", "dimension")])]
    pairs = align.candidates(entries, {("county", "county_name")}, max_pairs=1)
    assert len(pairs) == 1 and pairs[0].label == "same"


def test_a_missing_role_does_not_disqualify_a_pair():
    """Requiring a judged role produced zero candidates on a real project."""
    entries = [_col_entry("a", [("county", None)]), _col_entry("b", [("county_name", None)])]
    assert align.candidates(entries, set(), max_pairs=10)


# --------------------------------------------------------------------------- test quality

def test_coverage_is_code_and_names_the_specific_thing_nothing_watches():
    assert set(testing.EXPOSURE) == {"joins", "aggregates", "coalesce", "case", "window"}
    for why, catchers in testing.EXPOSURE.values():
        assert why and catchers


def test_severity_is_a_score_because_the_levels_are_ordered():
    subs = [testing.TestSubject("t", "unique", "id", "m", "u", "warn", 5, 3)]
    q = testing.questions_for(subs)["sev__0"]
    assert q["type"] == "score" and len(q["criteria"]) == 3


def test_a_violation_reaching_a_mart_should_not_be_a_warning():
    s = testing.TestSubject("t", "unique", "id", "m", "u", "warn", 9, 4)
    got = testing.mismatch(s, {"answer": "2.0"})
    assert got and "4 mart" in got[1]


def test_a_cosmetic_test_set_to_error_is_also_a_mismatch():
    s = testing.TestSubject("t", "not_null", "x", "m", "u", "error", 0, 0)
    assert testing.mismatch(s, {"answer": "0.0"})


def test_agreement_is_not_reported_as_a_mismatch():
    s = testing.TestSubject("t", "unique", "id", "m", "u", "error", 9, 4)
    assert testing.mismatch(s, {"answer": "2.0"}) is None


# --------------------------------------------------------------------------- row adjudication

def test_the_explanation_options_can_be_replaced_per_mart():
    """The options ARE the domain knowledge and there is one set per mart."""
    generic = rows.options_for("x", None)
    custom = rows.options_for("water_rights",
                              {"water_rights": {"conditional_right": "no structure yet"}})
    assert "conditional_right" in custom and "conditional_right" not in generic
    assert "cannot_tell" in custom          # the no-match option survives


def test_coherence_is_asked_beside_the_explanation_never_instead_of_it():
    """Measured: coherence read 0.29-0.32 on rows that were entirely normal, correctly seeing the
    fields contradict, while the explanation said why that contradiction is expected."""
    fr = rows.FailingRow("t", "m", "u", "r", {"a": 1})
    qs = rows.questions_for(fr)
    assert qs["explanation"]["type"] == "choice" and qs["coherent"]["type"] == "noul"


def test_a_row_state_drops_empty_fields_rather_than_sending_nulls():
    fr = rows.FailingRow("t", "m", "u", "r", {"a": 1, "b": None}, rule="unique on `a`")
    st = rows.build_state(fr)
    assert st["row"] == {"a": 1} and st["rule"] == "unique on `a`"
    assert "model_purpose" not in st


# --------------------------------------------------------------------------- standard practice

def test_the_standard_checks_are_split_three_ways_not_all_enforced():
    """Enforcing conventions is how a tool gets muted."""
    from dbt_assay import practices as prac
    cats = set(prac.ALL.values())
    assert cats == {"enforce", "recommend", "adjudicate"}
    assert "fct_model_naming_conventions" in prac.RECOMMEND
    assert "fct_hard_coded_references" in prac.ENFORCE
    assert "fct_model_fanout" in prac.ADJUDICATE


def test_every_enforced_check_says_why_it_has_no_exception():
    from dbt_assay import practices as prac
    assert all(len(v) > 30 for v in prac.ENFORCE.values())


def test_a_check_can_be_recategorised_or_switched_off():
    from dbt_assay import practices as prac
    c = prac.categories({"fct_model_fanout": "recommend", "fct_root_models": "off"})
    assert c["fct_model_fanout"] == "recommend" and c["fct_root_models"] == "off"
    import pytest
    with pytest.raises(ValueError):
        prac.categories({"fct_model_fanout": "nonsense"})


def test_the_offending_model_is_found_whatever_column_names_it():
    from dbt_assay import practices as prac
    assert prac.model_of({"resource_name": "model.p.water_rights"}) == "water_rights"
    assert prac.model_of({"child": "model.p.a", "parent": "model.p.b"}) == "a"
    assert prac.model_of({"nothing": 1}) is None


def test_a_missing_key_test_comes_with_the_grain_it_should_cover():
    """Evaluator says 'no primary key test'. This says which columns -- a patch, not a nag."""
    from types import SimpleNamespace

    from dbt_assay import practices as prac
    e = ModelEntry(uid="model.p.m", name="m", path="p.sql", layer="marts", materialized="table")
    e.grain = Fact(["section_id", "county"], "declared")
    e.marts = 3
    project = SimpleNamespace(tests=[])
    got = prac.primary_key_patches(project, [e])
    # the fifth element is what the grain names and the model does NOT emit;
    # an entry with no columns known falls back to proposing the whole grain
    assert got == [("m", ["section_id", "county"], "declared", 3, [])]


def test_a_model_that_already_has_a_uniqueness_test_is_not_patched():
    from types import SimpleNamespace

    from dbt_assay import practices as prac
    e = ModelEntry(uid="model.p.m", name="m", path="p.sql", layer="marts", materialized="table")
    e.grain = Fact(["id"], "declared")
    project = SimpleNamespace(tests=[SimpleNamespace(kind="unique", tests_model="model.p.m")])
    assert prac.primary_key_patches(project, [e]) == []


def test_the_shipped_skill_tells_an_agent_to_check_its_own_work():
    from dbt_assay.skilltext import SKILL_MD
    assert "changed_contracts()" in SKILL_MD
    assert "Never guess a model's grain" in SKILL_MD
    assert "Never remove a filter you do not understand" in SKILL_MD
    assert "not what you concluded" in SKILL_MD


def test_a_uniqueness_test_of_either_kind_counts_as_coverage():
    """A compound grain IS asserted with unique_combination_of_columns; listing only `unique`
    reported a model that carries one as having nothing watching it."""
    from dbt_assay import testing as t
    for sig in ("joins", "aggregates", "window"):
        assert set(t.EXPOSURE[sig][1]) == set(t.UNIQUENESS)


def test_only_a_bounded_case_is_worth_an_accepted_values_test():
    """'the model contains a CASE' flagged 97 models on a real project and essentially all were
    noise: a CASE building a geography, one concatenating a string, and passthroughs of the shape
    `case when cep in ('nan','0') then null else cep end`."""
    from dbt_assay.parse import digest as dg
    from dbt_assay.testing import _bounded_case

    enum = dg("select case when s='a' then 'A' when s='b' then 'B' else 'C' end as f from t")
    assert _bounded_case(enum)

    passthrough = dg("select case when cep in ('nan','0') then null else cep end as cep from t")
    assert not _bounded_case(passthrough)

    built = dg("select case when lon is not null then st_geogpoint(lon, lat) end as g from t")
    assert not _bounded_case(built)


def test_an_aggregate_with_nothing_to_inflate_it_is_not_an_exposure():
    """Firing on any SUM or COUNT flagged 62 models on a real project that had no joins at all."""
    from types import SimpleNamespace

    from dbt_assay.inventory import Fact, ModelEntry
    from dbt_assay.parse import digest as dg
    from dbt_assay.testing import coverage_gaps

    project = SimpleNamespace(tests=[], models={"model.p.m": object()})
    e = ModelEntry(uid="model.p.m", name="m", path="p.sql", layer="marts", materialized="table")
    e.grain = Fact(["a"], "derived")

    alone = {"model.p.m": dg("select k, sum(v) as t from t group by k")}
    assert not [g for g in coverage_gaps(project, alone, [e]) if "inflated" in g.exposure]

    joined = {"model.p.m": dg("select k, sum(v) as t from t join u on u.k = t.k group by k")}
    assert [g for g in coverage_gaps(project, joined, [e]) if "inflated" in g.exposure]
