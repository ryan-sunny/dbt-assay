"""`assay suggest`: from what the check found to what this project should therefore configure.

*** THE RULE THAT MATTERS MOST IS A RULE ABOUT WHAT IT WILL NOT DO. ***
It proposes the candidate and the measurement and never the meaning. A plausible vocab block
written from model names looks exactly like knowledge, is not, and then rides along with every
judged question from that point on -- the tool's worst failure shipped as a feature, and the most
confident-sounding output it would produce.
"""
import yaml

from dbt_assay import suggest
from dbt_assay.config import Config
from dbt_assay.store import Store


def _store(tmp_path):
    s = Store(str(tmp_path / "s.duckdb"))
    s.con.execute("insert into runs (run_id, started_at, project) "
                  "values ('r1', current_timestamp, 'p')")
    return s


def _edge(s, parent, child, cols):
    import json
    s.con.execute(
        "insert into edge_facts (run_id, parent, child, parent_name, child_name, joined_on) "
        "values ('r1', ?, ?, ?, ?, ?)",
        [f"model.p.{parent}", f"model.p.{child}", parent, child, json.dumps(cols)])


def _rule(s, subject, question, verdict, note, source="agent", family=None):
    s.con.execute(
        "insert into adjudications (subject, question, family, verdict, note, source, "
        "decided_by, decided_at) values (?, ?, ?, ?, ?, ?, ?, current_timestamp)",
        [subject, question, family or question, verdict, note, source, source])


# ----------------------------------------------------------------- it never writes a meaning

def test_no_suggestion_ever_fills_in_a_meaning(tmp_path):
    """*** THE ONE PROPERTY THAT CANNOT REGRESS QUIETLY. ***

    A `means:` filled from a model name is indistinguishable, in the file, from one a person
    decided on. It would never be caught by reading `audit.yml` later, and it would be sent with
    every judged question from that point on.
    """
    s = _store(tmp_path)
    for i in range(4):
        _edge(s, f"a{i}", f"b{i}", ["section_id", "parcel_pk"])
    _rule(s, "model.p.x", "hop_multiplies_rows", "disagree", "a union member edge cannot multiply")
    _rule(s, "model.p.y", "hop_multiplies_rows", "disagree", "a union member edge cannot multiply")
    out = suggest.build(s, Config(), {"grain_unresolved"}, "r1")
    assert out, "the fixture produced no suggestions; this test is not measuring anything"
    for item in out:
        for line in item.draft.splitlines():
            t = line.strip()
            if t.startswith(("means:", "implies:")):
                assert t in ('means: ""', 'implies: ""'), f"{item.key}: {line!r}"


def test_every_draft_is_valid_yaml(tmp_path):
    """A draft that does not parse is a draft nobody can use, and the failure is at paste time."""
    s = _store(tmp_path)
    for i in range(4):
        _edge(s, f"a{i}", f"b{i}", ["section_id"])
    _rule(s, "model.p.x", "hop_multiplies_rows", "disagree", "a long written reason about unions")
    out = suggest.build(s, Config(), {"grain_unresolved", "seed_reaches_nothing"}, "r1")
    for item in out:
        if not item.draft.strip():
            continue
        body = "\n".join(ln for ln in item.draft.splitlines() if not ln.strip().startswith("#"))
        if not body.strip():
            continue
        yaml.safe_load(body)          # raises if it is not YAML


def test_a_questions_draft_loads_as_real_config(tmp_path):
    """*** AND VALID YAML IS NOT THE SAME AS VALID CONFIG. ***

    `shipped_action` returns `queue` for a flat opinion and the SENTENCE `queue above a threshold`
    for a thresholded one. Both are strings and both are valid YAML; one of them makes `Config`
    raise `unknown action`. The draft is built from the shipped entry itself for exactly this
    reason -- a summary of a thing is not the thing.
    """
    s = _store(tmp_path)
    out = suggest.build(s, Config(), {"grain_unresolved", "seed_reaches_nothing"}, "r1")
    drafts = [i for i in out if i.section == "questions" and i.draft.strip()]
    assert drafts, "no question drafts; the fixture is not exercising this"
    for item in drafts:
        Config.from_dict(yaml.safe_load(item.draft) or {})


# ----------------------------------------------------------------- what it refuses to decide

def test_a_repeated_reason_names_both_files_and_picks_neither(tmp_path):
    """*** GUESSING HERE IS THE EXPENSIVE KIND OF WRONG. ***

    A reason repeating means something upstream of the config is missing. Whether that is a word
    the checker lacks or a case the checker gets wrong decides which file it belongs in, and on
    this project it went the second way: two models waived one reason, and the fix was structural
    (0.15.0, 0.21.1). A waiver would have silenced two models and left the third for production.
    """
    s = _store(tmp_path)
    reason = "a union member edge cannot multiply because the parents arrive through union all"
    _rule(s, "model.p.one", "hop_multiplies_rows", "disagree", reason)
    _rule(s, "model.p.two", "hop_multiplies_rows", "disagree", reason)
    out = [i for i in suggest.build(s, Config(), set(), "r1") if i.section == "open"]
    assert out, "a reason on two subjects produced nothing"
    item = next(i for i in out if "2 subjects" in i.headline)
    assert item.decide, "the rule that refuses to pick must say what it refuses"
    assert "vocab" in item.decide and "check" in item.decide
    assert not item.draft.strip(), "a draft here is something to paste before the question is settled"


def test_a_label_is_not_a_reason_and_cannot_become_a_waiver(tmp_path):
    """*** 26 OF 38 DISAGREEMENTS ON THE FIELD WAREHOUSE WERE GENERATED STUBS. ***

    `source='label'` rows are the project's own declarations read back as verdicts. Left in, the
    waiver rule drafts 26 waivers whose `reason:` is "the project asserts out" -- a permanent
    silence justified by a sentence nobody wrote.
    """
    s = _store(tmp_path)
    for i in range(3):
        _rule(s, f"model.p.m{i}", "key__x", "disagree", "the project asserts out",
              source="label")
    out = suggest.build(s, Config(), set(), "r1")
    assert not [i for i in out if i.section == "waivers"], "a stub became a waiver draft"
    stub = [i for i in out if i.key == "_label_stubs"]
    assert stub, "the exclusion is silent; a filter nobody can see is a guard that cannot see"
    assert "3" in stub[0].headline


def test_no_measured_agreement_says_so_instead_of_falling_back(tmp_path):
    """A recommendation drawn from the shipped default alone reads exactly like a measured one."""
    s = _store(tmp_path)
    for i in range(3):
        _rule(s, f"model.p.m{i}", "some_family", "unclear", "the state does not carry it")
    out = [i for i in suggest.build(s, Config(), set(), "r1")
           if i.section == "questions" and i.key == "some_family"]
    assert out, "a family with only unclear verdicts produced nothing"
    assert "no agreement rate" in out[0].headline
    assert "unclear" in " ".join(out[0].measured).lower()
    assert "QUESTION" in " ".join(out[0].measured), "an unclear points at the question, not the config"
    assert not out[0].draft.strip().startswith("questions:"), "it proposed an action anyway"


def test_unclear_never_enters_the_agreement_denominator(tmp_path):
    """Disagreement means the criteria are wrong; unclear means the state does not carry what the
    question asks. Different edits, so they are never one number."""
    s = _store(tmp_path)
    for i in range(9):
        _rule(s, f"model.p.a{i}", "fam", "agree", "")
    _rule(s, "model.p.b", "fam", "disagree", "written reason here")
    for i in range(30):
        _rule(s, f"model.p.c{i}", "fam", "unclear", "")
    out = [i for i in suggest.build(s, Config(), set(), "r1")
           if i.section == "questions" and i.key == "fam"]
    assert out
    assert "9/10" in out[0].headline, out[0].headline


# ----------------------------------------------------------------- ranking and ordering

def test_two_rules_are_never_ranked_against_each_other(tmp_path):
    """`section_id` scores 65 hops x 24 models = 1560 and `incident_id` scores 99.68% unique.

    Sorting those together declares one rule more important than another by an accident of scale,
    and buries every near-unique key under the join counts forever.
    """
    s = _store(tmp_path)
    for i in range(5):
        _edge(s, f"a{i}", f"b{i}", ["section_id"])
    s.con.execute(
        "insert into observed_keys (relation, column_name, row_count, distinct_ct, status, "
        "observed_at) values ('r', 'incident_id', 19628, 19566, 'has_duplicates', "
        "current_timestamp)")
    vocab = [i for i in suggest.build(s, Config(), set(), "r1") if i.section == "vocab"]
    bases = [i.basis for i in vocab]
    assert len(set(bases)) == 2, bases
    # every member of a rule is contiguous: the list is per-rule, not one pooled ranking
    assert bases == sorted(bases), bases


def test_the_order_is_total_so_two_runs_agree(tmp_path):
    """Two suggestions of equal rank are broken by key, never by whichever the database returned
    first. That is the `arbitrary_pick` defect this tool reports in other people's SQL."""
    s = _store(tmp_path)
    for i in range(6):
        _edge(s, f"a{i}", f"b{i}", ["k_one", "k_two"])
    a = [i.key for i in suggest.build(s, Config(), {"grain_unresolved"}, "r1")]
    b = [i.key for i in suggest.build(s, Config(), {"grain_unresolved"}, "r1")]
    assert a == b and a


def test_a_batch_stamp_is_not_a_contested_key(tmp_path):
    """`_dlt_load_id` holds 3 distinct values in 1,744,203 rows. Nobody has ever been misled by it.

    The dangerous shape is the opposite one: nearly unique, so it passes every spot check.
    """
    s = _store(tmp_path)
    s.con.execute(
        "insert into observed_keys (relation, column_name, row_count, distinct_ct, status, "
        "observed_at) values ('r', '_dlt_load_id', 1744203, 3, 'has_duplicates', "
        "current_timestamp)")
    s.con.execute(
        "insert into observed_keys (relation, column_name, row_count, distinct_ct, status, "
        "observed_at) values ('r', 'incident_id', 19628, 19566, 'has_duplicates', "
        "current_timestamp)")
    keys = {i.key for i in suggest.build(s, Config(), set(), "r1") if i.section == "vocab"}
    assert "incident_id" in keys
    assert "_dlt_load_id" not in keys


def test_one_column_probed_repeatedly_is_one_candidate(tmp_path):
    """`observed_keys` is a history. Grouping without that reports one column three times."""
    s = _store(tmp_path)
    for _ in range(3):
        s.con.execute(
            "insert into observed_keys (relation, column_name, row_count, distinct_ct, status, "
            "observed_at) values ('r', 'incident_id', 19628, 19566, 'has_duplicates', "
            "current_timestamp)")
    got = [i for i in suggest.build(s, Config(), set(), "r1") if i.key == "incident_id"]
    assert len(got) == 1, f"{len(got)} candidates for one column"


def test_a_vocab_term_already_defined_is_not_proposed(tmp_path):
    """The set difference happens BEFORE the ranking, so the ordering is not decided by rows that
    are not in the list."""
    s = _store(tmp_path)
    for i in range(6):
        _edge(s, f"a{i}", f"b{i}", ["section_id", "other_col"])
    cfg = Config(vocab={"section_id": {"means": "a PLSS section"}})
    keys = {i.key for i in suggest.build(s, cfg, set(), "r1") if i.section == "vocab"}
    assert "section_id" not in keys
    assert "other_col" in keys


# --------------------------------------------- a decision queue holds decisions, not history

def _live(*pairs):
    """`{(question, subject)}` as `live_pairs` produces it."""
    return {(q, s) for q, s in pairs}


def test_a_cluster_nothing_still_fires_on_is_not_a_decision(tmp_path):
    """*** A CLUSTER GREW MORE PROMINENT THE MORE SUCCESSFULLY IT WAS FIXED. ***

    Reported from the field. The top two items under DECIDE FIRST were clusters of 8 subjects and
    2 subjects with ZERO live findings between them -- both already repaired, one of them by the
    structural fix the item's own text cites. Six models were firing `hop_multiplies_rows` that
    day and none of them was in the queue.

    It is the mirror of the 0.24.0 defect: that one hid live evidence, this one promoted dead
    evidence to the top of the list a person reads first.
    """
    s = _store(tmp_path)
    r = "a union member edge cannot multiply because the parents arrive through union all"
    for m in ("one", "two", "three"):
        _rule(s, f"model.p.{m}", "hop_multiplies_rows", "disagree", r)
    out = suggest.build(s, Config(), set(), "r1", live=set())
    assert not [i for i in out if i.basis.startswith("one reason")], \
        "a cluster with nothing still firing is in the decision queue"


def test_a_cluster_that_still_fires_stays_and_says_how_much_of_it_does(tmp_path):
    """Partly fixed is its own answer: 1 of 3 still firing says the repair was incomplete."""
    s = _store(tmp_path)
    r = "a union member edge cannot multiply because the parents arrive through union all"
    for m in ("one", "two", "three"):
        _rule(s, f"model.p.{m}", "hop_multiplies_rows", "disagree", r)
    live = _live(("hop_multiplies_rows", "model.p.two"))
    got = [i for i in suggest.build(s, Config(), set(), "r1", live=live)
           if i.basis.startswith("one reason")]
    assert got, "a cluster with a live subject was dropped"
    assert "1 of 3" in " ".join(got[0].measured), got[0].measured


def test_the_rank_counts_what_still_fires_not_what_was_ever_ruled_on(tmp_path):
    """Otherwise the ordering rewards resolution, which is how the queue filled with history."""
    s = _store(tmp_path)
    big = "the first reason, given on many models and almost entirely repaired since"
    small = "a different second reason about lookups unique on their join key, still live"
    for m in ("a", "b", "c", "d", "e"):
        _rule(s, f"model.p.{m}", "hop_multiplies_rows", "disagree", big)
    for m in ("x", "y", "z"):
        _rule(s, f"model.p.{m}", "hop_multiplies_rows", "disagree", small)
    live = _live(("hop_multiplies_rows", "model.p.a"),
                 ("hop_multiplies_rows", "model.p.x"),
                 ("hop_multiplies_rows", "model.p.y"))
    got = [i for i in suggest.build(s, Config(), set(), "r1", live=live)
           if i.basis.startswith("one reason")]
    assert len(got) == 2
    # 3 live beats 5 ruled-on-but-1-live, even though the 5 cluster is bigger
    assert "2 of 3" in " ".join(got[0].measured), [g.measured for g in got]


def test_with_no_live_findings_it_says_it_cannot_tell(tmp_path):
    """*** AN ABSENT MEASUREMENT IS NOT A PASS AND IT IS NOT A FAILURE. ***

    Called without a target there is no way to know which of these still fire. Dropping them all
    would read as "nothing to decide"; keeping them silently would read as "all of this is live".
    Both are claims the data does not support.
    """
    s = _store(tmp_path)
    r = "a union member edge cannot multiply because the parents arrive through union all"
    for m in ("one", "two"):
        _rule(s, f"model.p.{m}", "hop_multiplies_rows", "disagree", r)
    got = [i for i in suggest.build(s, Config(), set(), "r1", live=None)
           if i.basis.startswith("one reason")]
    assert got, "everything was dropped, which claims they are all resolved"
    assert "cannot tell" in " ".join(got[0].measured).lower()


def test_a_resolved_cluster_is_reported_as_improvement_not_deleted(tmp_path):
    """"Given on 8 models, fires on none" is the one improvement measure that needs no re-ruling.

    `effectiveness` otherwise moves only when a person reads again. This moves when the CHECK
    stops being wrong, so dropping it from the queue must not drop it from the record.
    """
    s = _store(tmp_path)
    r = "a union member edge cannot multiply because the parents arrive through union all"
    for m in ("one", "two", "three"):
        _rule(s, f"model.p.{m}", "hop_multiplies_rows", "disagree", r)
    gone = suggest.resolved_clusters(s, Config(), set())
    assert len(gone) == 1 and gone[0]["n"] == 3
    assert gone[0]["questions"] == ["hop_multiplies_rows"]
    # ...and it is exactly what the queue dropped: the two halves partition the clusters
    live = set()
    queued = {i.key for i in suggest.build(s, Config(), set(), "r1", live=live)
              if i.basis.startswith("one reason")}
    assert not queued


def test_live_pairs_reads_findings_and_artifact_rows_the_same_way():
    """Three callers hold findings in two shapes. Two normalizers would drift."""
    from types import SimpleNamespace
    objs = [SimpleNamespace(check="c", subject="model.p.a")]
    rows = [{"check": "c", "subject": "model.p.a"}]
    assert suggest.live_pairs(objs) == suggest.live_pairs(rows) == {("c", "model.p.a")}
    assert suggest.live_pairs([{"check": "c"}]) == set(), "a row with no subject invented one"
    assert suggest.live_pairs(None) == set()


# --------------------------------------------- the list is sorted by the number it reads first

def test_the_vocab_list_is_sorted_by_the_number_the_headline_leads_with(tmp_path):
    """*** A CORRECT ORDERING THAT READS AS A BROKEN ONE. ***

    Ranked on `hops x models` and led with hops, the list ran 65, 32, 40. A reader who cannot see
    the sort key has to take the order on trust, and this one looks wrong every time.

    Fixing the legibility fixed the ranking: a vocabulary term is worth writing when it is
    SHARED, and the payoff is every judged question that carries it -- which follows the models,
    not the joins. `city` at 40 hops across 4 models is one team's local habit.
    """
    import re
    s = _store(tmp_path)
    # Chosen so the two orderings DISAGREE, or the test proves nothing:
    #   wide   = 8 models x  8 hops = 64 by the old product rank
    #   narrow = 2 models x 40 hops = 80 by the old product rank, and it would come FIRST
    for i in range(8):
        _edge(s, f"p{i}", f"wide{i}", ["wide_col"])
    for i in range(40):
        _edge(s, f"q{i}", f"narrow_{i % 2}", ["narrow_col"])
    rows = [i for i in suggest.build(s, Config(), set(), "r1")
            if i.section == "vocab" and "models join on" in i.headline]
    assert len(rows) == 2, [r.headline for r in rows]
    leading = [int(re.match(r"(\d+) models", r.headline).group(1)) for r in rows]
    assert leading == sorted(leading, reverse=True), rows[0].headline + " | " + rows[1].headline
    assert "wide_col" in rows[0].headline, "a term joined inside ONE model outranked a shared one"
