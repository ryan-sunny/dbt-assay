import pytest

from dbt_assay.config import Config, Threshold, ThresholdError

NOUL = {"kind": "noul", "answer": "0.91", "confidence": None}
CHOICE = {"kind": "choice", "answer": "supports", "confidence": 0.93}


def test_a_threshold_reads_its_direction_at_the_call_site():
    assert Threshold("p > 0.85").holds(NOUL)
    assert not Threshold("p < 0.30").holds(NOUL)


def test_a_choice_gate_names_both_the_answer_and_the_confidence():
    assert Threshold("answer == 'supports' and confidence > 0.8").holds(CHOICE)
    assert not Threshold("answer == 'contradicts' and confidence > 0.8").holds(CHOICE)


def test_a_noul_has_no_confidence_to_threshold_on():
    """Exposing one would invite a gate to read '0.91 confident' off a 91%-likely-yes answer."""
    with pytest.raises(ThresholdError):
        Threshold("confidence > 0.8").holds(NOUL)


def test_a_miswired_gate_raises_rather_than_silently_not_firing():
    with pytest.raises(ThresholdError):
        Threshold("p > 0.8").holds(CHOICE)


def test_thresholds_cannot_run_arbitrary_code():
    for bad in ("__import__('os').system('x')", "open('/etc/passwd')", "p.__class__"):
        with pytest.raises(ThresholdError):
            Threshold(bad)


def test_fail_is_refused_until_the_question_has_been_measured():
    cfg = Config.from_dict({"questions": {"q": {"act": {"fail": "p > 0.8", "queue": "p > 0.6"}}}})
    q = cfg.for_question("q")
    assert q.action_for(NOUL, adjudications=0, min_adjudications=20) == "queue"
    assert q.action_for(NOUL, adjudications=50, min_adjudications=20) == "fail"


def test_a_waiver_without_a_reason_is_rejected():
    with pytest.raises(ThresholdError):
        Config.from_dict({"waivers": {"m": [{"question": "q"}]}})


def test_an_expired_waiver_is_not_a_waiver():
    cfg = Config.from_dict({"waivers": {"m": [
        {"question": "q", "reason": "because", "until": "2000-01-01"}]}})
    assert cfg.waived("m", "q") is None
    cfg2 = Config.from_dict({"waivers": {"m": [
        {"question": "q", "reason": "because", "until": "2099-01-01"}]}})
    assert cfg2.waived("m", "q").reason == "because"


def test_an_unknown_action_is_rejected():
    with pytest.raises(ThresholdError):
        Config.from_dict({"questions": {"q": {"act": {"explode": "p > 0.5"}}}})


def test_the_gate_reads_real_adjudication_counts(tmp_path):
    """The refusal to gate is wired to the store, not to a constant."""
    from dbt_assay.store import Store

    s = Store(tmp_path / "a.duckdb")
    # named, not positional: a positional insert breaks the moment a column is added
    s.con.execute("""insert into model_decisions
        (decision_key, question, kind, answer, confidence, probabilities, state_hash,
         prompt_version, model_version, caller, decided_at)
        values ('m','role__x','choice','dimension',0.9,'{}','h','v1','jev','t',current_timestamp)""")
    assert s.adjudication_counts() == {}
    s.adjudicate("m", "role__x", "column_role", "dimension", "agree", note="ok", who="t")
    assert s.adjudication_counts()["column_role"] == 1
    acc = s.accuracy("column_role")
    assert acc["n"] == 1 and acc["agreement"] == 1.0
    assert s.pending() == []          # ruled on, so no longer pending
    s.close()


def test_an_unknown_verdict_is_rejected(tmp_path):
    import pytest as _pytest

    from dbt_assay.store import Store
    s = Store(tmp_path / "b.duckdb")
    with _pytest.raises(ValueError):
        s.adjudicate("m", "q", "fam", "a", "looks_fine")
    s.close()


def test_a_question_id_maps_back_to_its_family():
    """A verdict has to be recorded against the FAMILY, not the per-column question id, or a
    hundred verdicts look like a hundred questions with one verdict each."""
    from dbt_assay.cli import _family_of
    assert _family_of("role__amount") == "column_role"
    assert _family_of("key__section_id") == "column_is_part_of_the_key"
    assert _family_of("pred__0") == "predicate_intent"
    assert _family_of("explanation") == "row_explanation"


def test_a_store_written_by_an_older_assay_still_opens(tmp_path):
    """`create table if not exists` is not a migration: an old store keeps its old shape forever
    and the next insert fails with a column-count error on somebody else's machine."""
    import duckdb

    from dbt_assay.store import Store
    p = tmp_path / "old.duckdb"
    con = duckdb.connect(str(p))
    con.execute("""create table adjudications (
        subject varchar, question varchar, family varchar, answered varchar, verdict varchar,
        correction varchar, note varchar, decided_by varchar, decided_at timestamp,
        primary key (subject, question))""")
    con.execute("""insert into adjudications values
        ('m','q','fam','a','agree','','','me',current_timestamp)""")
    con.close()

    s = Store(p)                                  # opening it migrates
    s.adjudicate("m2", "q2", "fam", "a", "agree", source="label")
    # the pre-existing verdict is kept as human; the label one does not count toward a gate
    assert s.adjudication_counts("human") == {"fam": 1}
    assert s.adjudication_counts("all")["fam"] == 2
    s.close()


def test_verdicts_recorded_before_the_column_existed_are_kept_as_human(tmp_path):
    """Leaving them NULL silently drops every verdict somebody had already recorded."""
    import duckdb

    from dbt_assay.store import Store
    p = tmp_path / "old.duckdb"
    con = duckdb.connect(str(p))
    con.execute("""create table adjudications (
        subject varchar, question varchar, family varchar, answered varchar, verdict varchar,
        correction varchar, note varchar, decided_by varchar, decided_at timestamp,
        primary key (subject, question))""")
    con.execute("""insert into adjudications values
        ('m','q','column_role','a','agree','','','ryan',current_timestamp)""")
    con.close()
    s = Store(p)
    assert s.adjudication_counts("human") == {"column_role": 1}
    s.close()


def test_a_check_that_fires_and_is_not_configured_is_named():
    """*** "IT REPORTED NOTHING" AND "IT IS NOT CONFIGURED" READ IDENTICALLY FROM THE OUTSIDE. ***

    That is audit.yml's own opening sentence, and it was the promise the file broke.
    `unknown_questions` catches a config naming a check that does not exist. Nothing caught the
    other direction -- a check that exists and the config does not name -- which is the direction
    that grows by itself, because every release adds checks and nobody's config grows with it.

    Reported from the field on a warehouse where four were firing unnamed, including the largest
    family at 115 findings.
    """
    from dbt_assay.config import Config

    cfg = Config(questions={"arbitrary_pick": object()})
    got = dict(cfg.unconfigured({"arbitrary_pick", "key_stopped_holding", "seed_reaches_nothing"}))
    assert "arbitrary_pick" not in got, "a configured check was reported as a gap"
    assert set(got) == {"key_stopped_holding", "seed_reaches_nothing"}
    # and it carries the shipped opinion, so the difference between what assay would suggest and
    # what is actually happening is visible rather than something you must know to look for
    assert got["key_stopped_holding"] == "fail"
    assert got["seed_reaches_nothing"] == "annotate"
    assert cfg.unconfigured(set()) == [], "it invents a gap when nothing fired"


def test_the_shipped_opinion_is_parsed_not_pattern_matched():
    """*** A READER THAT HANDLES ONE SPELLING OF A FACT IS THIS FILE'S OWN DEFECT. ***

    The first version regexed for `{action: x}` and silently missed every check written in the
    block form -- `duckdb_full_match` among them -- and every one configured with `act:`
    thresholds rather than a flat action. It appeared inside the fix for exactly that class.
    """
    import yaml

    from dbt_assay.config import DEFAULT_YML, shipped_action

    named = yaml.safe_load(DEFAULT_YML)["questions"]
    assert len(named) >= 10
    for check in named:
        assert shipped_action(check), f"{check} is named in the template and reads as unshipped"
    # the two spellings that broke it
    assert shipped_action("duckdb_full_match") == "queue"          # block form
    assert "threshold" in shipped_action("grain_unresolved")       # act: thresholds
    assert shipped_action("not_a_real_check") == ""


def test_an_unconfigured_check_falls_back_to_severity_and_can_never_fail():
    """*** THE SHIPPED FILE IS NOT A FALLBACK, AND FOR EVERY RELEASE UP TO 0.30.0 IT SAID IT WAS.

    `DEFAULT_YML` is only ever WRITTEN, by `assay init`; nothing parses it. So a check added after
    somebody ran `init` never gets the action it ships with. That is the RIGHT behavior -- a
    release adding a gating check must not turn a green build red on upgrade -- but the file's
    first line claimed the opposite, which is documentation promising behavior the code does not
    have, in the one place a person reads to find out what the tool does.
    """
    from dbt_assay.checks.structural import Finding
    from dbt_assay.config import DEFAULT_YML, Config
    from dbt_assay.judged import apply_policy

    # The claim is gone. It survives quoted, inside the comment explaining that it was false,
    # which is the record and not the promise -- so this checks the line that makes claims.
    head = "\n".join(DEFAULT_YML.splitlines()[:3]).lower()
    assert "the defaults below are what runs" not in head, head
    assert "falls back to its severity" in DEFAULT_YML.lower()
    assert "used to say" in DEFAULT_YML, "the correction was made silently"

    def _f(check, base):
        return Finding(check=check, subject="s", subject_name="m", file="", summary="x",
                       detail="y", base=base)

    kept, _ = apply_policy([_f("key_stopped_holding", 3), _f("seed_reaches_nothing", 1)],
                           Config(), None, None)
    got = {f.check: (act, why) for f, act, why in kept}
    # ships as `fail`, arrives as `queue`, and SAYS which
    assert got["key_stopped_holding"] == ("queue", "default by severity")
    assert got["seed_reaches_nothing"] == ("annotate", "default by severity")
    assert all(act != "fail" for act, _ in got.values()), "an unconfigured check gated a build"
