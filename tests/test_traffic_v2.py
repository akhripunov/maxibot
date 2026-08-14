from datetime import datetime, timedelta, timezone

from app.traffic.attribution import AMBIGUOUS, HIGH, MEDIUM
from app.traffic.simulator import SimVisitor, compare_scenarios, simulate_join_v2


def test_one_open_candidate_is_high_but_not_confirmed():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = simulate_join_v2([SimVisitor("a", base, 0.8)], base + timedelta(seconds=5))
    assert result.status == HIGH
    assert result.reason == "single_candidate_with_open_signal"


def test_open_signal_breaks_multiple_candidates_only_to_medium():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = simulate_join_v2([
        SimVisitor("a", base, 0.8),
        SimVisitor("b", base - timedelta(seconds=20)),
        SimVisitor("c", base - timedelta(seconds=30)),
    ], base + timedelta(seconds=65))
    assert result.status == MEDIUM
    assert result.reason == "multiple_candidates_only_one_open_signal"


def test_multiple_open_candidates_remain_ambiguous_when_close():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = simulate_join_v2([
        SimVisitor("a", base, 0.8),
        SimVisitor("b", base + timedelta(seconds=1), 0.8),
    ], base + timedelta(seconds=5))
    assert result.status == AMBIGUOUS
    assert result.reason == "multiple_open_candidates"


def test_comparison_harness_has_v1_and_v2_rows():
    rows = compare_scenarios()
    assert len(rows) >= 5
    assert all("v1" in row and "v2" in row for row in rows)
