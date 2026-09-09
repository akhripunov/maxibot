"""Fail-closed recovery for missed MAX membership updates.

The authoritative source is still MAX's membership endpoint. This job only
looks at unexpired, verified Mini App channel handoffs and records a member
when MAX confirms that exact user is currently in the channel.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.bot.menu import MenuRepository
from app.db.models import Channel, TrafficJoinIntent
from app.traffic.join_intent import ACTIVE


logger = logging.getLogger(__name__)


async def reconcile_active_memberships(client, session_factory, *, limit: int = 100) -> int:
    """Recover members confirmed by MAX after an unavailable long-poll update."""
    now = datetime.now(timezone.utc)
    async with session_factory() as db:
        rows = list((await db.execute(
            select(TrafficJoinIntent.id, TrafficJoinIntent.max_user_id, Channel.external_id)
            .join(Channel, Channel.id == TrafficJoinIntent.channel_id)
            .where(
                TrafficJoinIntent.status == ACTIVE,
                TrafficJoinIntent.expires_at >= now,
            )
            .order_by(TrafficJoinIntent.membership_checked_at.asc().nullsfirst(), TrafficJoinIntent.created_at.asc())
            .limit(limit)
        )).all())

    repo = MenuRepository(session_factory)
    recovered = 0
    for intent_id, max_user_id, channel_external_id in rows:
        try:
            member = await client.get_member(str(channel_external_id), str(max_user_id))
        except Exception as exc:
            logger.warning("MAX_MEMBERSHIP_RECONCILIATION_LOOKUP_FAILED category=%s", type(exc).__name__)
            continue
        async with session_factory() as db:
            intent = await db.get(TrafficJoinIntent, intent_id)
            if intent and intent.status == ACTIVE:
                intent.membership_checked_at = now
                await db.commit()
        if not member:
            continue
        handled = await repo.record_traffic_membership(
            str(channel_external_id),
            str(max_user_id),
            "user_added",
            event_id=f"reconcile:{intent_id}",
            event_time=now,
            metadata={"source": "official_member_lookup"},
        )
        recovered += int(bool(handled))
    return recovered
