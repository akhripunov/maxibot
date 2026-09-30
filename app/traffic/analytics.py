from __future__ import annotations

from datetime import datetime
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TrafficEvent, TrafficMembership, TrafficVisit


async def traffic_stats_for_channels(db: AsyncSession, channel_ids: list[str] | set[str], *, window_start: datetime | None = None, window_end: datetime | None = None) -> dict[str, int | float]:
    """Return canonical promotion analytics for an owned channel set."""
    ids = list(dict.fromkeys(str(value) for value in channel_ids if value))
    if not ids:
        return {"visits": 0, "opens": 0, "to_channel": 0, "new_members": 0,
                "unsubscribed": 0, "lifetime_ads_subscribed": 0,
                "lifetime_ads_unsubscribed": 0,
                "max_open_likely": 0, "max_open_confirmed": 0,
                "join_events": 0, "EXACT": 0, "HIGH": 0, "MEDIUM": 0,
                "AMBIGUOUS": 0, "UNATTRIBUTED": 0}

    async def count(model, *conditions):
        query_conditions = list(conditions)
        if window_start is not None:
            timestamp = TrafficVisit.landing_at if model is TrafficVisit else TrafficMembership.joined_at if model is TrafficMembership else TrafficEvent.created_at
            query_conditions.extend((timestamp >= window_start, timestamp < window_end))
        return int(await db.scalar(select(func.count(model.id)).where(model.channel_id.in_(ids), *query_conditions)) or 0)

    stats = {
        "visits": await count(TrafficVisit),
        "opens": await count(TrafficVisit, TrafficVisit.max_opened_at.is_not(None)),
        "to_channel": await count(TrafficVisit, TrafficVisit.to_channel_at.is_not(None)),
        "new_members": await count(TrafficMembership),
        "unsubscribed": await count(TrafficMembership, TrafficMembership.current_member.is_(False)),
        "max_open_likely": await count(TrafficEvent, TrafficEvent.event_type == "MAX_OPEN_LIKELY"),
        "max_open_confirmed": await count(TrafficEvent, TrafficEvent.event_type == "MAX_OPEN_CONFIRMED"),
        "join_events": await count(TrafficEvent, TrafficEvent.event_type.in_(["join_request_created", "join_request"])),
    }
    # Count only confirmed ad-attributed membership events, using the same
    # EXACT + non-empty yclid evidence as daily_reports._day_counts. Deduplicate
    # one MAX user per Moscow calendar day, so this is the cumulative sum of
    # daily report counts. Existing membership-state metrics remain unchanged.
    dialect = db.get_bind().dialect.name
    event_day = (
        func.date(func.timezone("Europe/Moscow", TrafficEvent.event_time))
        if dialect == "postgresql"
        else func.date(TrafficEvent.event_time, "+3 hours")
    )

    async def daily_ad_event_count(event_types: tuple[str, ...]) -> int:
        query = select(TrafficEvent.max_user_id, event_day.label("event_day")).where(
            TrafficEvent.channel_id.in_(ids),
            TrafficEvent.event_type.in_(event_types),
            TrafficEvent.max_user_id.is_not(None),
        )
        if window_start is not None:
            query = query.where(TrafficEvent.event_time >= window_start, TrafficEvent.event_time < window_end)
        query = query.join(
            TrafficMembership,
            (TrafficMembership.channel_id == TrafficEvent.channel_id)
            & (TrafficMembership.max_user_id == TrafficEvent.max_user_id),
        ).join(TrafficVisit, TrafficVisit.id == TrafficMembership.attributed_visit_id).where(
            TrafficMembership.attribution_status == "EXACT",
            TrafficVisit.yclid.is_not(None),
            TrafficVisit.yclid != "",
        )
        per_user_day = query.group_by(TrafficEvent.max_user_id, event_day).subquery()
        return int(await db.scalar(select(func.count()).select_from(per_user_day)) or 0)

    stats.update({
        "lifetime_ads_subscribed": await daily_ad_event_count(("user_added",)),
        "lifetime_ads_unsubscribed": await daily_ad_event_count(("user_removed", "member_removed", "unsubscribed")),
    })
    stats.update({status: await count(TrafficMembership, TrafficMembership.attribution_status == status)
                  for status in ("EXACT", "HIGH", "MEDIUM", "AMBIGUOUS", "UNATTRIBUTED")})
    return stats
