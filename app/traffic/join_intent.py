"""Fail-closed correlation of a verified Mini App handoff to user_added.

The MAX membership event contains a user and a channel, but no landing visit
token.  A join intent is created only after the Mini App presents verified
initData and the backend has accepted the configured channel target.  A newer
verified handoff for the same user/channel supersedes the older unfinished
intent.  No intent is inferred from timing, IP, browser, or a shared invite.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TrafficJoinIntent, TrafficVisit

ACTIVE = "ACTIVE"
SUPERSEDED = "SUPERSEDED"
CONSUMED = "CONSUMED"


def intent_expiry(now: datetime, window_hours: int) -> datetime:
    """Return a bounded expiry that still covers manual approval delay."""
    return now + timedelta(hours=max(1, min(int(window_hours), 168)))


async def activate_join_intent(
    db: AsyncSession,
    *,
    visit: TrafficVisit,
    max_user_id: str,
    window_hours: int,
    now: datetime | None = None,
) -> TrafficJoinIntent:
    """Make this verified handoff the only active intent for user/channel."""
    now = now or datetime.now(timezone.utc)
    await db.execute(
        update(TrafficJoinIntent)
        .where(
            TrafficJoinIntent.channel_id == visit.channel_id,
            TrafficJoinIntent.max_user_id == str(max_user_id),
            TrafficJoinIntent.status == ACTIVE,
            TrafficJoinIntent.visit_id != visit.id,
        )
        .values(status=SUPERSEDED, superseded_at=now)
    )
    intent = await db.scalar(select(TrafficJoinIntent).where(TrafficJoinIntent.visit_id == visit.id))
    if not intent:
        intent = TrafficJoinIntent(
            channel_id=visit.channel_id,
            visit_id=visit.id,
            max_user_id=str(max_user_id),
            status=ACTIVE,
            created_at=now,
            expires_at=intent_expiry(now, window_hours),
        )
        db.add(intent)
    else:
        intent.max_user_id = str(max_user_id)
        intent.status = ACTIVE
        intent.expires_at = intent_expiry(now, window_hours)
        intent.superseded_at = None
        intent.consumed_at = None
    return intent


async def consume_single_active_join_intent(
    db: AsyncSession,
    *,
    channel_id: str,
    max_user_id: str,
    now: datetime | None = None,
) -> str | None:
    """Consume exactly one unexpired intent, or return no attribution.

    The caller must use this only after the official ``user_added`` event.
    Multiple active rows are treated as unsafe rather than selecting one.
    """
    now = now or datetime.now(timezone.utc)
    intents = list((await db.scalars(
        select(TrafficJoinIntent)
        .where(
            TrafficJoinIntent.channel_id == channel_id,
            TrafficJoinIntent.max_user_id == str(max_user_id),
            TrafficJoinIntent.status == ACTIVE,
            TrafficJoinIntent.expires_at >= now,
        )
        .order_by(TrafficJoinIntent.created_at.desc())
    )).all())
    if len(intents) != 1:
        return None
    intent = intents[0]
    intent.status = CONSUMED
    intent.consumed_at = now
    return intent.visit_id
