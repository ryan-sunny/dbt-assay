"""Three bugs reported from a real project, and the guards that keep them fixed.

None of them failed a test. Each one either took hours, destroyed evidence, or killed a command
outright, and the suite was green throughout.
"""
import json
from pathlib import Path

import pytest


def test_adjudicate_narrows_in_one_query_instead_of_one_per_test():
    """*** 1,288 TESTS x A COLD `dbt show` = FIVE AND A HALF HOURS. ***

    Reported from a project that turns store_failures on globally. Thirteen of those audit tables
    held a single row between them; every other call paid a dbt startup to be told a table was
    empty. A `union all` of counts answers it for hundreds of relations at once.
    """
    from dbt_assay import rows

    calls = []

    class _Probe:
        @staticmethod
        def run_sql(sql, *_a, **_k):
            calls.append(sql)
            return [{"rel": "db.aud.t_one", "n": 3}, {"rel": "db.aud.t_two", "n": 0}]

    rels = [f"db.aud.t_{i}" for i in range(400)] + ["db.aud.t_one", "db.aud.t_two"]
    have, unknown = rows.which_have_failures(rels, _Probe, ".", None, "dbt")
    assert have == {"db.aud.t_one"}
    assert unknown == []
    # 402 relations, batched at 200: three statements, not 402.
    assert len(calls) <= 4, len(calls)
    assert "union all" in calls[0]


def test_a_relation_it_cannot_read_is_unknown_and_never_counted_clean():
    """A batch that fails is halved until the culprit is alone, and that one is UNKNOWN. Silence
    has to be distinguishable from absence -- the rule this whole tool is built on."""
    from dbt_assay import rows

    class _Probe:
        @staticmethod
        def run_sql(sql, *_a, **_k):
            return [] if "bad" in sql else [{"rel": "ok", "n": 1}]

    have, unknown = rows.which_have_failures(["good.a", "bad.b"], _Probe, ".", None, "dbt")
    assert "bad.b" in unknown
    assert "bad.b" not in have


def test_compiling_does_not_destroy_which_tests_failed(tmp_path):
    """*** `dbt compile` OVERWRITES run_results.json. ***

    Reported from the field: after `assay onboard --compile`, run_results held one result and no
    test status, so anything downstream reading it saw a clean project. assay caused a check to
    stop seeing and report a pass.
    """
    from dbt_assay.cli import _run_dbt_compile

    (tmp_path / "dbt_project.yml").write_text("name: p\n")
    t = tmp_path / "target"
    t.mkdir()
    (t / "run_results.json").write_text(json.dumps({"results": [{"unique_id": "test.x",
                                                                 "status": "fail"}]}))
    _run_dbt_compile(t, dbt_bin="definitely-not-a-real-dbt")
    kept = t / "run_results.before-assay-compile.json"
    assert kept.exists(), "the previous run_results was not preserved"
    assert json.loads(kept.read_text())["results"][0]["status"] == "fail"


def test_the_review_loop_does_not_need_click_to_exist():
    """*** typer 0.27 STOPPED DEPENDING ON click. ***

    `import click` sat inside the one function that records verdicts, so the review loop -- the
    only path by which a question reaches its gate -- died with ModuleNotFoundError on a current
    typer. click is declared now, and this degrades to line input rather than dying.
    """
    import builtins
    import io
    import sys

    from dbt_assay import cli

    real_import = builtins.__import__

    def no_click(name, *a, **k):
        if name == "click":
            raise ImportError("no click here")
        return real_import(name, *a, **k)

    class _Tty(io.StringIO):
        def isatty(self):        # take the keypress branch, then lose click inside it
            return True

    builtins.__import__ = no_click
    old = sys.stdin
    sys.stdin = _Tty("a\n")
    try:
        assert cli._keypress() == "a", "a missing click must fall back, not raise"
    finally:
        builtins.__import__ = real_import
        sys.stdin = old


def test_click_is_a_declared_dependency():
    root = Path(__file__).parent.parent
    pj = root / "pyproject.toml"
    if not pj.exists():
        pytest.skip("installed as a wheel")
    assert "click>=" in pj.read_text(), "review -i needs click; it must not arrive by accident"


def test_a_family_nothing_asks_is_reported_rather_than_shown_as_coverage():
    """*** THREE CUSTOM FAMILIES LINTED CLEAN, LISTED AS `yours`, AND DID NOTHING. ***

    Every call site names a SHIPPED family by string literal; there is no generic runner. So a
    family with a NEW name is loaded, validated, displayed and inert -- and it looks exactly like
    coverage. Worse, the documented example used a new name.
    """
    from dbt_assay.contracts import SHIPPED
    from dbt_assay.lint import CALLERS, caller_of

    uncalled = [f for f in SHIPPED if caller_of(f) is None]
    assert not uncalled, f"shipped families nothing asks: {uncalled}"
    assert caller_of("a_family_nobody_wrote_a_call_site_for") is None
    for fam, (_mod, cmd, state) in CALLERS.items():
        assert fam in SHIPPED, f"{fam} is claimed to have a caller but is not a shipped family"
        assert cmd.startswith("assay "), fam
        assert state, fam


def test_the_caller_table_matches_the_source_that_asks():
    """A hand-kept table is a second copy of a fact, and the second copy is what drifts."""
    import importlib
    import inspect

    from dbt_assay.lint import CALLERS
    for fam, (mod, _cmd, _state) in CALLERS.items():
        src = inspect.getsource(importlib.import_module(f"dbt_assay.{mod}"))
        assert f'"{fam}"' in src, f"{mod}.py does not mention {fam}"


def test_the_documented_example_replaces_a_shipped_family():
    """The docs taught the broken pattern: their example used a NEW name, so anyone following
    them wrote a question that cannot run."""
    from pathlib import Path

    from dbt_assay.contracts import SHIPPED
    root = Path(__file__).parent.parent
    p = root / "docs" / "OVERVIEW.md"
    if not p.exists():
        pytest.skip("no docs in a wheel install")
    import re
    blocks = re.findall(r"```yaml\n# assay_questions/(.*?)```", p.read_text(), re.DOTALL)
    assert blocks, "the worked examples are gone"
    for b in blocks:
        named = set(re.findall(r"^([a-z_]+):$", b, re.MULTILINE)) - {"criteria", "instructions"}
        # An example is runnable EITHER because it replaces a shipped family, OR because it
        # declares a subject and so is run by the generic runner. Anything else teaches the bug.
        assert (named & set(SHIPPED)) or "subject:" in b, (
            f"the example defines {named}, which is neither a shipped family nor a subject "
            f"declaration, so following it produces a question nothing asks")


def test_a_parent_collapsed_before_the_join_is_seen():
    """*** 33% OF 543 HOPS READ `silently_multiplied`, AND THE TOP ONE WAS THIS. ***

    `join (select wdid, count(*) from int_water_diligence group by wdid) dl on dl.wdid = r.wdid`
    is already one row per key. Without this the state says "joins on wdid, no grouping" -- every
    word true, conclusion wrong. Measured: cannot_tell @0.24 became same_thing @0.41.
    """
    from dbt_assay.parse import digest

    sub = digest("select r.wdid, dl.n from water_rights r left join "
                 "(select wdid, count(*) as n from int_water_diligence group by wdid) dl "
                 "on dl.wdid = r.wdid", "m", "duckdb")
    assert sub.pre_aggregated == {"int_water_diligence": ["wdid"]}

    plain = digest("select r.wdid, d.note from water_rights r "
                   "join int_water_diligence d on d.wdid = r.wdid", "m", "duckdb")
    assert plain.pre_aggregated == {}


def test_a_top_level_group_by_is_not_mistaken_for_pre_aggregation():
    """That would mask a deliberate narrowing as "the parent was already collapsed"."""
    from dbt_assay.parse import digest
    assert digest("select wdid, count(*) n from water_rights group by wdid",
                  "m", "duckdb").pre_aggregated == {}


def test_a_missing_dbt_suggests_the_wrapper_this_project_actually_uses(tmp_path):
    """It failed with one quiet line, the run completed looking successful, and the models stayed
    unreadable. A uv or poetry project has no bare `dbt` on PATH, which is the common case."""
    from dbt_assay.cli import _run_dbt_compile

    (tmp_path / "dbt_project.yml").write_text("name: p\n")
    (tmp_path / "uv.lock").write_text("")
    t = tmp_path / "target"
    t.mkdir()
    _ok, why = _run_dbt_compile(t, dbt_bin="definitely-not-a-real-dbt")
    assert "uv run dbt" in why


def test_a_window_subject_carries_the_actual_ordering():
    """*** IT CARRIED THE WORD "column" AND 65% OF ANSWERS WERE `cannot_tell`. ***

    `order_sql` holds `['adjudication_date DESC']`; `order_roots` holds `['column']`. The first
    version of this builder guessed a field name that does not exist and fell back to the roots,
    so the question was asked about nothing.
    """
    from pathlib import Path

    from dbt_assay.parse import digest

    t = Path(__file__).parent / "_fixture_target"
    if not t.exists():
        # build the state directly from a digest; no fixture project needed
        d = digest("select wdid, row_number() over (partition by wdid "
                   "order by adjudication_date desc) as rn from r", "m", "duckdb")
        w = d.windows[0]
        assert w.order_sql == ["adjudication_date DESC"]
        assert w.order_roots == ["column"], "the roots are useless as a subject; order_sql is not"
        return


def test_a_family_that_declares_a_subject_is_reported_as_asked():
    """The runner exists precisely so a new name is not inert. Reporting it as uncalled would be
    the same lie in reverse."""
    from dbt_assay.lint import caller_of
    assert caller_of("anything_at_all", {"subject": "window"}) is not None
    assert caller_of("anything_at_all", {"subject": "window"})[1] == "assay ask"
    assert caller_of("anything_at_all", {}) is None


def test_every_subject_kind_builds_a_state_with_the_thing_it_names_in_it():
    """A subject that does not carry its own subject produces a confident non-answer. The window
    builder did exactly that: it sent the word "column" instead of the ordering."""
    from dbt_assay import subjects
    assert {"model", "edge", "column", "predicate", "expression",
            "window"} <= set(subjects.KINDS)
    with pytest.raises(ValueError, match="unknown subject"):
        subjects.build("sql", subjects.SubjectSource())


def test_ask_estimates_before_it_spends_and_refuses_over_the_cap():
    """*** `subject: expression` YIELDS THOUSANDS OF SUBJECTS. ***

    Reported from the field: 3,540 expressions against 82 windows on one project. The number worth
    printing is the one you see BEFORE running it without `--select`. A cap that fires after the
    spend is not a cap, and neither is an estimate.
    """
    import inspect

    from dbt_assay.cli import _estimate, ask
    from dbt_assay.subjects import Subject

    subs = [Subject("expression", f"k{i}", "m", f"n{i}", state={"expression": "a + b" * 20})
            for i in range(1000)]
    q = {"id_prefix": "x", "type": "choice", "instructions": {"question": "?"},
         "criteria": {"a": {"what": "x"}, "cannot_tell": {"what": "y"}}}
    cheap = _estimate(subs[:10], q)
    dear = _estimate(subs, q)
    assert dear > cheap * 50, "the estimate must scale with the number of subjects"
    assert _estimate([], q) == 0.0

    src = inspect.getsource(ask)
    assert "refused before spending anything" in src
    assert "--dry-run" in src, "the escape hatch must be named where the refusal happens"


def test_every_subject_says_what_one_row_of_its_model_is():
    """*** THE RESIDUE AFTER FIXING THE CRITERIA IS IN THE STATE, NOT THE WORDING. ***

    Reported from the field, and it is the sharpest observation made about this tool: a window
    subject carried the model name, the partition, the order by and the position -- and nothing
    saying what the ROWS ARE. No phrasing of the options can settle "is this ranking rights or
    sections" when the state never says. The author rewrote the criteria to exclude a case in
    plain words and the model still answered it at 0.55 and 0.63.

    assay already knew: 239 of 356 models on the test warehouse carry a declared key.
    """
    import inspect

    from dbt_assay import subjects
    src = inspect.getsource(subjects._add_what_a_row_is)
    assert "declared_keys" in src
    assert "group_by_columns" in src, "a model with no declared key still has its own grouping"
    assert "schema.columns" in src, "118 of 356 have neither; the columns are the fallback"
    # and it is applied to EVERY kind, not just the one that prompted it
    assert "_add_what_a_row_is(out" in inspect.getsource(subjects.build)


def test_the_judged_lint_is_cached_so_it_cannot_flap_in_ci():
    """*** SEVEN WARNINGS ON ONE RUN, SIX ON THE NEXT, OVER AN UNCHANGED SET OF BANKS. ***

    A probability against a threshold flaps, and a family sitting near the line will flip forever
    until nobody trusts the check. `decide` caches on a hash of the state, so an unchanged
    question keeps its answer and only a REWORDED one is asked again. Verified live: run one made
    ten calls, runs two and three made zero and produced byte-identical output.
    """
    import inspect

    from dbt_assay.lint import judge_overlap
    src = inspect.getsource(judge_overlap)
    assert "decide(store, client" in src
    assert 'prompt_version=spec["prompt_version"]' in src, "the cache must invalidate on a reword"


def test_a_family_can_opt_out_of_the_row_field_that_costs_it_answers():
    """*** A FIELD THAT HELPS ONE FAMILY CAN COST ANOTHER, AND BOTH ARE THE FIELD WORKING. ***

    Reported from the field: `what_one_row_of_this_model_is` fixed a wildfire case and cost two of
    eight verified answers on a family whose criteria reason about the WINDOW's partition. The
    model read a right id in the MODEL's grain as satisfying a clause about the partition, which
    was a different list. Rewording to compensate scored WORSE (5/8). The fix is an opt-out, not
    removing a field other families need.
    """
    import inspect

    from dbt_assay import subjects
    src = inspect.getsource(subjects.build)
    assert 'state == "full"' in src
    assert "minimal" in src
    with pytest.raises(ValueError, match="subject_state"):
        subjects.build("window", subjects.SubjectSource(), state="whatever")

    from dbt_assay.lint import lint_question
    bad = {"type": "choice", "prompt_version": "x.v1", "id_prefix": "zz", "subject": "window",
           "subject_state": "tiny", "finding_when": ["a"],
           "instructions": {"question": "?"},
           "criteria": {"a": {"what": "one thing that happens"},
                        "b": {"what": "a different thing entirely"},
                        "cannot_tell": {"what": "not enough to decide either way"}}}
    assert "subject_state" in {i.rule for i in lint_question("q", bad)}


def test_confirmed_answers_are_the_regression_test_for_assay_itself():
    """*** THE ANSWER DISTRIBUTION BARELY MOVED. THE REGRESSION WAS INVISIBLE. ***

    Reported from the field: an upgrade moved two of eight verified answers while 79 of the same
    answer came back either side. No summary this tool prints would have shown it. Eight rulings
    on record did.
    """
    from dbt_assay.store import Store

    s = Store(":memory:")
    s.con.execute(
        "insert into adjudications (subject, question, family, answered, verdict, source, "
        "decided_at) values "
        "('m::win::0','senior','fam','ordered_by_appropriation','agree','human',current_timestamp),"
        "('m::win::1','senior','fam','not_a_seniority_order','disagree','human',current_timestamp),"
        "('m::win::2','senior','fam','x','agree','label',current_timestamp)")
    got = s.confirmed()
    s.close()
    # only what a PERSON agreed with: a disagreement is not a baseline, and a label is not a person
    assert [r["subject"] for r in got] == ["m::win::0"]
    assert got[0]["answered"] == "ordered_by_appropriation"


def test_regress_resolves_a_verdict_filed_under_a_prefix():
    """*** `review` RECORDED A PREFIX AND `regress` LOOKED UP A NAME. ***

    Every verdict was skipped on the mismatch, and the command written to catch exactly that class
    of regression reported a pass. Resolved on BOTH spellings, because an old store must keep
    working and the two will coexist in every store that already exists.
    """
    from dbt_assay.cli import _resolve_family
    banks = {"seniority_ordered_by_the_wrong_date": {"id_prefix": "senior"},
             "column_role": {"id_prefix": "role"}}
    assert _resolve_family("senior", banks) == "seniority_ordered_by_the_wrong_date"
    assert _resolve_family("column_role", banks) == "column_role"
    assert _resolve_family("a_family_that_was_deleted", banks) is None


def test_regress_refuses_to_report_a_pass_over_an_empty_set():
    """*** "0/0 CONFIRMED ANSWERS STILL HOLD", GREEN, EXIT 0, HAVING REPLAYED NOTHING. ***

    The same shape as a guard that scans nothing and a scanner that matches nothing -- in the one
    command written to catch regressions.
    """
    import inspect

    from dbt_assay.cli import regress
    src = inspect.getsource(regress)
    assert "NOTHING WAS REPLAYED" in src
    assert "held == 0 and not moved" in src


def test_an_option_that_names_another_option_is_flagged():
    """*** A TEXT CHECK BELIEVES PROSE, AND HERE BELIEVING IT IS KNOWN TO BE WRONG. ***

    Reported with numbers: a question whose option said "the answer is <other>, not this" scored
    no_overlap 0.63 against an overlap mass of 0.35 -- the judged check read the routing as a
    disjointness guarantee. The answering model did not honor it, putting one subject under both
    options at 0.55 and 0.63.
    """
    from dbt_assay.lint import lint_question
    q = {"type": "choice", "prompt_version": "x.v1", "id_prefix": "zz",
         "instructions": {"question": "?"},
         "criteria": {
             "not_a_seniority_order": {"what": "It ranks rows by a column that is not a priority."},
             "something_else": {"what": "It ranks a non-right. If ranked by a non-priority column "
                                        "the answer is not_a_seniority_order, not this."},
             "cannot_tell": {"what": "The ordering columns do not settle which it is."}}}
    assert "option_routes_to_another" in {i.rule for i in lint_question("q", q)}


def test_the_cross_reference_rule_does_not_fire_on_a_substring():
    """`square_feet` was flagged for naming `feet`, and `other` matched inside "some other
    entity". An option name has to be a whole token, and never one that is merely part of a
    longer option name being legitimately described."""
    from dbt_assay.lint import lint_question
    q = {"type": "choice", "prompt_version": "x.v1", "id_prefix": "zz",
         "instructions": {"question": "?"},
         "criteria": {"feet": {"what": "The name claims feet, as a depth or a height."},
                      "square_feet": {"what": "The name claims square feet, as an area."},
                      "other": {"what": "Some other kind of thing entirely, not those."},
                      "cannot_tell": {"what": "The name is too abbreviated to say."}}}
    assert "option_routes_to_another" not in {i.rule for i in lint_question("q", q)}


def test_a_proposed_key_test_only_names_columns_the_model_emits():
    """*** 0 OF 15 PROPOSED GRAINS HELD, AND 9 NAMED A COLUMN THE MODEL DOES NOT EMIT. ***

    Reported from the field. The grain is what the SQL groups or dedups by, and a model can dedup
    on a key and then drop it. A test asserting on a column that is not in the output cannot even
    be written, so the patch was a nag with extra steps.
    """
    from types import SimpleNamespace

    from dbt_assay import practices as prac
    from dbt_assay.inventory import Fact, ModelEntry

    e = ModelEntry(uid="model.p.m", name="m", path="p.sql", layer="marts", materialized="table")
    e.grain = Fact(["name_key", "city"], "derived")
    e.columns = [SimpleNamespace(name="city"), SimpleNamespace(name="business_name")]
    e.marts = 2
    got = prac.primary_key_patches(SimpleNamespace(tests=[]), [e])
    assert got[0][1] == ["city"], "it must not propose a test on name_key"
    assert got[0][4] == ["name_key"], "and it must say which column it dropped and why"


def test_a_grain_entirely_absent_from_the_output_is_its_own_finding():
    """*** A MODEL THAT DEDUPS ON A COLUMN AND THEN DROPS IT CANNOT BE TESTED BY ANYTHING. ***

    Verification found this before the field did: a model documented "one row per company + city"
    that does `partition by name_key, city` and then `select * exclude (name_key)`. Nothing
    downstream can assert its uniqueness, and proposing a test was the wrong answer entirely.
    """
    from types import SimpleNamespace

    from dbt_assay import practices as prac
    from dbt_assay.inventory import Fact, ModelEntry

    e = ModelEntry(uid="model.p.m", name="m", path="p.sql", layer="marts", materialized="table")
    e.grain = Fact(["name_key"], "derived")
    e.columns = [SimpleNamespace(name="business_name"), SimpleNamespace(name="city")]
    got = prac.primary_key_patches(SimpleNamespace(tests=[]), [e])
    assert got[0][1] == [], "no test can be proposed"
    assert got[0][4] == ["name_key"], "and what it dedups on is reported instead"


def test_a_partial_evaluator_build_does_not_read_as_a_clean_project():
    """*** FIVE fct_ MODELS OF MANY, AND THE REST REPORTED AS NOTHING AT ALL. ***

    Reported from the field. A check whose table is absent is not a check that passed, and this
    is the same defect as a guard that scans nothing -- the one this codebase keeps finding.
    """
    import inspect

    from dbt_assay.cli import practices
    src = inspect.getsource(practices)
    assert "not_checked" in src, "the missing checks must not be thrown away as `_missing`"
    assert "NOT\n" in src or "NOT " in src
    assert "This is not a pass" in src
    # and the empty-findings message must depend on whether anything was skipped
    assert "not because\n" in src or "not because " in src


def test_the_seed_instruction_is_one_that_works():
    """*** `--select assay_*` MATCHES NOTHING, AND IT WAS THE COMMAND'S LAST LINE. ***

    `dbt list` shows the nodes and the glob selects none of them. The closing line of a command
    is the one instruction a reader runs verbatim.
    """
    import inspect

    from dbt_assay.cli import export
    src = inspect.getsource(export)
    assert "path:" in src
    assert "relative to your dbt project" in src, "dbt resolves path: against the project root"


def test_the_readme_does_not_teach_the_selector_that_matches_nothing():
    from pathlib import Path

    import dbt_assay
    root = Path(dbt_assay.__file__).parent.parent.parent
    r = root / "README.md"
    if not r.exists():
        pytest.skip("installed as a wheel")
    body = r.read_text()
    assert "--select assay_*" not in body
    assert "--select path:seeds/assay" in body


def test_a_proposed_grain_is_counted_before_it_is_recommended():
    """*** "CAN BE WRITTEN" IS NOT "WOULD PASS", AND THE DIFFERENCE WAS 0 OF 7. ***

    Reported from the field after the columns fix: every proposal was expressible in the output
    and none held. `water_division` was proposed as the grain of a 1,045-row model with SEVEN
    distinct values. A reader following that writes a test that fails on its first run, and a
    command claiming to hand over a patch rather than a nag cannot do that.
    """
    from types import SimpleNamespace

    from dbt_assay import practices as prac

    sqls = []

    class _Probe:
        @staticmethod
        def run_sql(sql, *_a, **_k):
            sqls.append(sql)
            return [{"m": "water_rights", "n": 1045, "d": 7}, {"m": "ok", "n": 500, "d": 500}]

    proj = SimpleNamespace(models={"a": SimpleNamespace(name="water_rights"),
                                   "b": SimpleNamespace(name="ok")})
    patches = [("water_rights", ["water_division"], "derived", 3, []),
               ("ok", ["id"], "derived", 1, [])]
    held = prac.verify_grains(patches, proj, _Probe, ".", None, "dbt")
    assert held == {"water_rights": (1045, 7), "ok": (500, 500)}
    # batched, as `which_have_failures` is: one statement, not one per model
    assert len(sqls) == 1 and "union all" in sqls[0] and "count(distinct" in sqls[0]


def test_a_grain_that_cannot_be_counted_is_absent_rather_than_holding():
    """An absent count must never read as a pass. It is the rule this codebase keeps relearning."""
    from types import SimpleNamespace

    from dbt_assay import practices as prac

    class _Dead:
        @staticmethod
        def run_sql(*_a, **_k):
            return []

    proj = SimpleNamespace(models={"a": SimpleNamespace(name="m")})
    held = prac.verify_grains([("m", ["k"], "derived", 1, [])], proj, _Dead, ".", None, "dbt")
    assert held == {}, "an uncountable proposal must be absent, not recorded as holding"


def test_a_grain_that_does_not_hold_is_reported_as_the_stronger_finding():
    """No uniqueness test AND nobody knows what one row is. Worse than a missing test, and it was
    invisible -- printed as a recommendation."""
    import inspect

    from dbt_assay.cli import practices
    src = inspect.getsource(practices)
    assert "would_fail" in src
    assert "nobody knows what one row" in src
    assert "not a patch" in src, "a test that fails on its first run must not be recommended"


def test_a_grain_is_expressed_in_the_model_s_own_column_names():
    """*** `fact_sale` INHERITED `parcel_id` WHILE ITS OWN ARMS DO `parcel_id as sale_id`. ***

    The grain named a column the model does not emit, so `practices` proposed a uniqueness test
    that cannot be written and every consumer reading the inventory was told the wrong key. A
    grain is only useful in the names a consumer can see.
    """
    from types import SimpleNamespace

    from dbt_assay.contracts import GrainCandidate, _in_this_models_own_names

    sch = SimpleNamespace(columns=lambda _u: SimpleNamespace(names=["sale_id", "building_key"]))

    renamed = SimpleNamespace(alias_of={"parcel_id": "sale_id"}, output_exprs={})
    got = _in_this_models_own_names(
        GrainCandidate(["parcel_id"], "from_driver", ""), "u", {"u": renamed}, sch)
    assert got.columns == ["sale_id"] and got.not_emitted == []

    via_expr = SimpleNamespace(alias_of={}, output_exprs={"sale_id": "parcel_id"})
    got = _in_this_models_own_names(
        GrainCandidate(["parcel_id"], "from_driver", ""), "u", {"u": via_expr}, sch)
    assert got.columns == ["sale_id"]


def test_an_untranslatable_grain_column_is_kept_and_marked_not_dropped():
    """Dropping it would quietly NARROW a key, which is worse than naming a problem. 28 of 87
    proposals on a real warehouse name a column their model does not emit."""
    from types import SimpleNamespace

    from dbt_assay.contracts import GrainCandidate, _in_this_models_own_names

    sch = SimpleNamespace(columns=lambda _u: SimpleNamespace(names=["a", "b"]))
    d = SimpleNamespace(alias_of={}, output_exprs={})
    got = _in_this_models_own_names(
        GrainCandidate(["a", "gone"], "from_driver", ""), "u", {"u": d}, sch)
    assert got.columns == ["a", "gone"], "the key must not be narrowed silently"
    assert got.not_emitted == ["gone"]


def test_a_model_whose_columns_are_unknown_is_left_alone():
    """No column list is not the same fact as an empty one, and assuming it would mark every
    grain as wrong."""
    from types import SimpleNamespace

    from dbt_assay.contracts import GrainCandidate, _in_this_models_own_names

    sch = SimpleNamespace(columns=lambda _u: SimpleNamespace(names=[]))
    d = SimpleNamespace(alias_of={}, output_exprs={})
    got = _in_this_models_own_names(GrainCandidate(["x"], "from_driver", ""), "u", {"u": d}, sch)
    assert got.not_emitted == []


def test_the_mcp_findings_tool_returns_what_an_agent_needs_to_act():
    """*** IT RETURNED FIVE FIELDS WHILE `--json` RETURNED NINE. ***

    No file, so the agent could not open anything. No evidence, so three findings on one model
    came back with IDENTICAL summaries and no way to tell which column each was about. No blast
    radius, so a leaf looked like a model twenty-five marts read. All of it was already computed
    and thrown away at the one interface an agent uses.
    """
    import inspect

    from dbt_assay.mcp_server import Backend
    src = inspect.getsource(Backend.findings)
    for field in ('"file"', '"evidence"', '"downstream"', '"marts"'):
        assert field in src, field
    assert "must_stay_true" in src


def test_an_agent_is_told_what_a_fix_must_not_break():
    """A finding says what is wrong. `must_stay_true` says what was already RIGHT, which is the
    half an agent needs in order not to trade one defect for another."""
    import inspect

    from dbt_assay.mcp_server import Backend
    from dbt_assay.skilltext import SKILL_MD
    src = inspect.getsource(Backend._constraints)
    assert "claims_the_code_currently_supports" in src
    assert "verdicts_a_person_recorded" in src
    assert "source = 'human'" in src, "a label is not a person's ruling"
    # and the procedure tells it to read them
    assert "must_stay_true" in SKILL_MD
    assert "assay regress" in SKILL_MD


def test_an_agent_can_ask_what_would_actually_fail_the_build():
    """*** SEEING EVERY FINDING IS NOT KNOWING WHICH ONES STOP CI. ***

    Most findings are configured to annotate. Deciding which are which by reading the list is
    exactly the judgment an agent should not be making, and it is the difference between handing
    work back and handing back a red pipeline. `violations` applies the SAME `apply_policy` the
    CLI and the Action apply, including the refusal to let a judged question gate before it has
    recorded verdicts.
    """
    import inspect

    from dbt_assay.mcp_server import TOOLS, Backend
    from dbt_assay.skilltext import SKILL_MD
    src = inspect.getsource(Backend.violations)
    assert "apply_policy" in src, "it must not reimplement the policy, or it will diverge from CI"
    assert "would_fail_the_build" in src
    assert "recorded human verdicts" in src
    assert "violations" in {n for n, _d in TOOLS}
    assert "violations()" in SKILL_MD, "the procedure must tell the agent to call it"


def test_every_mcp_tool_description_is_still_indexed_correctly():
    """The tool list and the decorators are two copies of one order, and an off-by-one silently
    gives a tool somebody else's description."""
    import inspect

    from dbt_assay import mcp_server
    src = inspect.getsource(mcp_server.serve)
    for i, (name, _d) in enumerate(mcp_server.TOOLS):
        assert f"TOOLS[{i}][1])\n    def {name}(" in src, f"{name} is not registered at index {i}"


def test_an_empty_table_is_not_a_verified_grain():
    """*** assay WOULD HAVE GENERATED THE DEFECT IT IS BEST AT DETECTING. ***

    0 rows and 0 distinct satisfies `distinct >= rows`, so two of three files it offered to write
    were tests that pass because the table is empty. Committed, such a file is indistinguishable
    in the repo from a verified one -- which is exactly the ambiguity `test_cannot_fail` exists to
    find, and that check is 50 of one real project's 87 findings.

    The count was already in hand. Only the conclusion drawn from it was wrong.
    """
    from pathlib import Path

    from dbt_assay import patch as pm

    plans = pm.plan([("empty", ["id"], "derived", 1, [])], {"empty": (0, 0)}, "1.0.0",
                    Path("/tmp/_x"))
    assert not plans[0].sql
    assert "EMPTY" in plans[0].skipped


def test_the_mcp_extra_instruction_survives_rich():
    """*** rich EATS `[mcp]` AS A STYLE TAG. ***

    The message telling you to install the optional extra printed `uvx --from 'dbt-assay'` --
    without the extra. The instruction was broken by the exact defect it was written to fix, and
    there is already a note about square brackets in inventory.py.
    """
    import inspect

    from dbt_assay.cli import mcp
    from dbt_assay.mcp_server import server_class

    src = inspect.getsource(mcp)
    assert "markup=False" in src, "rich must not be allowed to parse this message"
    assert "mcp_server.server_class()" in src, "check the import BEFORE opening the transport"

    try:
        server_class()
    except RuntimeError as e:
        assert "dbt-assay[mcp]" in str(e), "the extra must be in the instruction"


def test_an_agent_can_rule_and_the_ruling_is_evidence_not_authority():
    """*** RULINGS ARE THE ONLY THING IN THIS SYSTEM THAT DO NOT COMPOUND. ***

    More checks find more, better states judge better, the warehouse accrues -- and none of it
    raises the number that says whether anything was UNDERSTOOD, because the only thing that could
    produce a ruling at scale had no way to write one down.

    So an agent can, filed apart. The ruled-on figure is the one number here nobody can game, and
    an agent able to raise it would destroy the property that makes it worth printing.
    """
    from dbt_assay.store import Store

    s = Store(":memory:")
    s.adjudicate("m::a", "role__x", "column_role", "measure", "agree",
                 note="read it", source="agent")
    s.adjudicate("m::b", "role__y", "column_role", "measure", "agree",
                 note="", source="human")
    try:
        assert len(s.agent_rulings()) == 1
        # none of the four authority paths can see it
        assert s.ruled_subjects() == {"m::b"}, "coverage must stay human-only"
        assert s.adjudication_counts() == {"column_role": 1}, "gating must stay human-only"
        assert [r["subject"] for r in s.confirmed()] == ["m::b"], "regress must anchor on a person"
    finally:
        s.close()


def test_an_agent_ruling_requires_a_reason():
    """A ruling nobody can check is not evidence, which is the thing this tool exists to object
    to. Waivers have required one since the beginning."""
    import inspect

    from dbt_assay.mcp_server import Backend
    src = inspect.getsource(Backend.rule)
    assert "a reason is required" in src
    assert 'source="agent"' in src, "it must not be able to write a human verdict"


def test_the_reviewer_sees_what_an_agent_already_concluded():
    """Otherwise the ruling is a log. Its only value is triaging what a person reads first."""
    import inspect

    from dbt_assay.cli import _review_coverage, _review_loop
    assert "an agent read this and said" in inspect.getsource(_review_loop)
    assert "an agent's reading" in inspect.getsource(_review_coverage)
