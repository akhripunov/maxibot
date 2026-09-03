"""Deterministic, aligned MaxiBot membership metrics."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.traffic.analytics import traffic_stats_for_channels

MOSCOW = ZoneInfo("Europe/Moscow")
WINDOW_DAYS = {"1d": 1, "3d": 3, "7d": 7, "30d": 30}


def window_bounds(*, end_date: date, window: str) -> tuple[datetime, datetime]:
    days = WINDOW_DAYS[window]
    local_end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=MOSCOW)
    local_start = local_end - timedelta(days=days)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


async def build_maxibot_metrics(db: AsyncSession, *, channel_id: str, end_date: date, windows: tuple[str, ...] = ("1d", "3d", "7d", "30d")) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for window in windows:
        start, end = window_bounds(end_date=end_date, window=window)
        stats = await traffic_stats_for_channels(db, [channel_id], window_start=start, window_end=end)
        qualified = int(stats["EXACT"]) + int(stats["HIGH"])
        visits = int(stats["visits"])
        result[window] = {
            "visits": visits,
            "qualified_memberships": qualified,
            "membership_rate": qualified / visits if visits else None,
            "scope": "CHANNEL",
            "campaign_specific": False,
            "campaign_scope": None,
            "attribution_level": "EXACT_OR_HIGH_MEMBERSHIP",
            "provenance": {
                "numerator": "TrafficMembership joined_at with attribution_status EXACT or HIGH",
                "denominator": "TrafficVisit landing_at",
                "window": window,
                "date_from": start.isoformat(),
                "date_to_exclusive": end.isoformat(),
                "timezone": "Europe/Moscow",
                "channel_scope": channel_id,
                "campaign_scope": None,
                "calculated_at": datetime.now(timezone.utc).isoformat(),
            },
        }
    result["campaign_specific"] = {"supported": False, "value": None, "reason": "reliable campaign-scoped visit and membership population is unavailable"}
    return result
