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
