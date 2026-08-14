from datetime import datetime, timedelta, timezone

from app.traffic.attribution import AMBIGUOUS, HIGH, UNATTRIBUTED
from app.traffic.simulator import SimVisitor, simulate_join


def test_single_visitor_is_high():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    decision = simulate_join([SimVisitor("a", base)], base + timedelta(seconds=5))
    assert decision.status == HIGH


def test_multiple_visitors_are_not_high():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    decision = simulate_join([
        SimVisitor("a", base),
        SimVisitor("b", base + timedelta(seconds=2)),
    ], base + timedelta(seconds=5))
    assert decision.status == AMBIGUOUS


def test_old_visitor_is_unattributed():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    decision = simulate_join([SimVisitor("a", base)], base + timedelta(seconds=121))
    assert decision.status == UNATTRIBUTED
