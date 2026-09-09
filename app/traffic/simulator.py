"""Deterministic local harness for Attribution Engine v1 scenarios."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.traffic.attribution import AttributionDecision, decide_scored


@dataclass(frozen=True)
class SimVisitor:
    visitor_id: str
    redirect_at: datetime
    open_score: float = 0.0


def simulate_join(visitors: list[SimVisitor], join_at: datetime, window_seconds: int = 120) -> AttributionDecision:
    candidates = [
        (visitor.visitor_id, (join_at - visitor.redirect_at).total_seconds())
        for visitor in visitors
        if timedelta(0) <= join_at - visitor.redirect_at <= timedelta(seconds=window_seconds)
    ]
    return decide_scored(candidates, window_seconds)


def simulate_join_v2(visitors: list[SimVisitor], join_at: datetime, window_seconds: int = 120) -> AttributionDecision:
    candidates = [
        (visitor.visitor_id, (join_at - visitor.redirect_at).total_seconds(), visitor.open_score)
        for visitor in visitors
        if timedelta(0) <= join_at - visitor.redirect_at <= timedelta(seconds=window_seconds)
    ]
    return decide_scored(candidates, window_seconds)


def compare_scenarios() -> list[dict]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    scenarios = [
        ([SimVisitor("a", base)], 5),
        ([SimVisitor("a", base), SimVisitor("b", base + timedelta(seconds=2))], 5),
        ([SimVisitor("a", base, 0.8), SimVisitor("b", base + timedelta(seconds=2))], 5),
        ([SimVisitor("a", base, 0.8), SimVisitor("b", base + timedelta(seconds=1), 0.8)], 5),
        ([SimVisitor("a", base)], 121),
    ]
    return [
        {"v1": simulate_join(visitors, base + timedelta(seconds=seconds)).status,
         "v2": simulate_join_v2(visitors, base + timedelta(seconds=seconds)).status}
        for visitors, seconds in scenarios
    ]


def demo_single_candidate() -> AttributionDecision:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return simulate_join([SimVisitor("visitor-A", base)], base + timedelta(seconds=5))
