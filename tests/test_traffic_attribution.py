from app.traffic.attribution import (
    AMBIGUOUS,
    HIGH,
    MEDIUM,
    UNATTRIBUTED,
    can_send_offline_conversion,
    decide,
    decide_scored,
)


def test_single_candidate_is_not_exact():
    assert decide(1, "visit-1", 1).status == HIGH


def test_multiple_candidates_are_ambiguous():
    assert decide(2, "visit-1", 1).status == AMBIGUOUS


def test_no_candidate_is_unattributed():
    assert decide(0).status == UNATTRIBUTED


def test_offline_conversion_requires_exact_and_identifier():
    assert not can_send_offline_conversion(HIGH, "yclid")
    assert not can_send_offline_conversion("EXACT", None)
    assert can_send_offline_conversion("EXACT", "yclid")


def test_scored_multiple_candidates_are_not_high():
    decision = decide_scored([("a", 3), ("b", 6)], 120)
    assert decision.status in {MEDIUM, AMBIGUOUS}
    assert decision.status != HIGH


def test_scored_no_candidates_are_unattributed():
    assert decide_scored([], 120).status == UNATTRIBUTED
