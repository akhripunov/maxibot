"""Deterministic, aligned MaxiBot membership metrics."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdsCampaign, AdsPromotionCampaignLink, BotConversion, BotTrafficVisit, TrafficMembership, TrafficVisit
from app.traffic.analytics import traffic_stats_for_channels
from app.traffic.funnel_reporting import campaign_matches
from sqlalchemy import func, select

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
            "campaign_specific": None,
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
    links = list((await db.scalars(select(AdsPromotionCampaignLink).where(
        AdsPromotionCampaignLink.channel_id == channel_id,
        AdsPromotionCampaignLink.object_type == "CHANNEL",
        AdsPromotionCampaignLink.status == "ACTIVE",
    ))).all())
    campaign_specific: dict[str, Any] = {"supported": False, "value": None, "reason": "reliable campaign-scoped visit and membership population is unavailable"}
    if len(links) == 1:
        campaign = await db.get(AdsCampaign, links[0].campaign_id)
        if campaign:
            start, end = window_bounds(end_date=end_date, window="7d")
            memberships = list((await db.scalars(select(TrafficMembership).join(
                TrafficVisit, TrafficVisit.id == TrafficMembership.attributed_visit_id
            ).where(
                TrafficMembership.channel_id == channel_id,
                TrafficMembership.attribution_status.in_(("EXACT", "HIGH")),
                TrafficMembership.joined_at >= start,
                TrafficMembership.joined_at < end,
            ))).all())
            matched = 0
            unknown = 0
            for membership in memberships:
                visit = await db.get(TrafficVisit, membership.attributed_visit_id)
                if visit and visit.utm_campaign:
                    matched += int(campaign_matches(visit.utm_campaign, campaign.external_id))
                else:
                    unknown += 1
            if not unknown:
                campaign_specific = {
                    "supported": True,
                    "value": matched,
                    "coverage": matched / len(memberships) if memberships else 1.0,
                    "reason": "single active campaign with deterministic landing UTM mapping",
                    "campaign_id": campaign.external_id,
                }
    return result


async def build_bot_metrics(db: AsyncSession, *, bot_id: str, end_date: date, windows: tuple[str, ...] = ("1d", "3d", "7d", "30d")) -> dict[str, Any]:
    """Return the same bounded outcome envelope for a BOT promotion object."""
    result: dict[str, Any] = {}
    for window in windows:
        start, end = window_bounds(end_date=end_date, window=window)
        visits = int(await db.scalar(select(func.count(BotTrafficVisit.id)).where(
            BotTrafficVisit.bot_id == bot_id,
            BotTrafficVisit.created_at >= start,
            BotTrafficVisit.created_at < end,
        )) or 0)
        started = int(await db.scalar(select(func.count(BotConversion.id)).join(
            BotTrafficVisit, BotTrafficVisit.id == BotConversion.bot_visit_id
        ).where(
            BotConversion.bot_id == bot_id,
            BotConversion.internal_event == "bot_started",
            BotConversion.created_at >= start,
            BotConversion.created_at < end,
        )) or 0)
        result[window] = {
            "visits": visits,
            "qualified_outcomes": started,
            "qualified_memberships": started,
            "membership_rate": started / visits if visits else None,
            "scope": "BOT",
            "campaign_specific": False,
            "campaign_scope": None,
            "attribution_level": "BOT_STARTED",
            "provenance": {
                "numerator": "BotConversion.bot_started",
                "denominator": "BotTrafficVisit landing visits",
                "window": window,
                "date_from": start.isoformat(),
                "date_to_exclusive": end.isoformat(),
                "timezone": "Europe/Moscow",
                "bot_scope": bot_id,
                "campaign_scope": None,
                "calculated_at": datetime.now(timezone.utc).isoformat(),
            },
        }
    result["campaign_specific"] = {"supported": False, "value": None, "reason": "reliable campaign-scoped bot visit population is unavailable"}
    return result
