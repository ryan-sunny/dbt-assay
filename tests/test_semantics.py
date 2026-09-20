from dbt_assay import semantics as sem
from dbt_assay.parse import digest


def test_a_scalar_subquerys_filter_is_not_the_models_filter():
    """`(select count(*) from z where amount = 0) as years_diverting_nothing` describes ONE
    column. Shown as a model filter it makes a diversion summary look like it keeps only the rows
    where nothing was diverted -- and a judgment shown that calls the description a lie, correctly,
    about evidence that was never true."""
    d = digest("select a, (select count(*) from z where amount = 0) n from t where state = 'CO'")
    assert d.predicates_atomic == ["state = 'CO'"]
    assert d.predicates_nested == ["amount = 0"]


def test_a_ctes_filter_belongs_to_the_model():
    """Scoping by nesting depth was wrong: most real filtering happens in CTEs, and dropping them
    took a project from 86 filters to 4."""
    d = digest("with keep as (select * from raw where state = 'CO') "
               "select a from keep where a is not null")
    assert sorted(d.predicates_atomic) == ["NOT a IS NULL", "state = 'CO'"]
    assert d.predicates_nested == []


def test_a_where_is_split_into_the_decisions_it_contains():
    d = digest("select 1 from t where a is not null and trim(b) <> '' and state = 'CO'")
    assert len(d.predicates_atomic) == 3


def test_an_or_stays_whole_because_its_branches_are_one_decision():
    d = digest("select 1 from t where (a = 1 or b = 2) and c = 3")
    assert any(" OR " in p for p in d.predicates_atomic)
    assert len(d.predicates_atomic) == 2


def test_one_choice_per_predicate_naming_the_predicate_inside_it():
    qs = sem.predicate_questions(["state = 'CO'", "x is not null"])
    assert sorted(qs) == ["pred__0", "pred__1"]
    assert qs["pred__0"]["instructions"]["predicate"] == "state = 'CO'"
    assert "cannot_tell" in qs["pred__0"]["criteria"]


def test_the_header_comment_stops_at_the_first_line_of_sql():
    sql = "-- one\n-- two\n\nselect 1 from t  -- not part of the header"
    h = sem.header_comment(sql)
    assert h == "one\ntwo"


def test_every_comment_is_documentation_for_the_description_family():
    """The opposite call from the defect checks, where comments measurably hurt. Here the prose
    IS the subject, and one model's explanation sat inline beside the columns it explained."""
    sql = "-- header\nselect a,\n  -- the full history bounds, outside the window\n  b from t"
    got = sem.all_comments(sql)
    assert "header" in got and "outside the window" in got


def test_a_model_with_no_prose_at_all_is_not_asked(project_dir):
    s = sem.Subject(uid="u", name="m", path="p", purpose=None, predicates=[], nested=[],
                    contract={}, header=None, comments=None)
    assert sem.description_state(s) is None


def test_the_description_question_is_phrased_as_the_defect():
    """High means the prose is wrong. Mixing polarity across a bank is how a threshold gets
    applied backwards."""
    q = sem.description_question()["desc"]
    assert q["type"] == "noul"
    assert "not true of the code" in q["criteria"]["true"]["what"].lower() or \
           "does not do" in str(q["criteria"]["true"]).lower()


def test_trivial_filters_are_not_worth_a_judgment():
    assert "1 = 1" in sem._SKIP and "TRUE" in sem._SKIP
