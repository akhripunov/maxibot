"""Pure helpers for BOT start-screen diagnostics."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import exists, select

from app.db.models import BotMaxIdentityLink, BotTrafficEvent, BotTrafficVisit

YES = "YES"
NO = "NO"
UNKNOWN = "UNKNOWN"
UNPROVEN = "UNPROVEN"


def first_time_bot_user(prior_real_start_count: int | None) -> str:
    if prior_real_start_count is None:
        return UNKNOWN
    return YES if prior_real_start_count == 0 else NO


def start_wait_timeout_reached(handoff_at: datetime | None, *, now: datetime, wait_seconds: int) -> bool:
    if handoff_at is None:
        return False
    return now >= handoff_at + timedelta(seconds=max(0, wait_seconds))


def dropoff_classification(*, real_start_received: bool, timeout_reached: bool) -> str:
    if real_start_received:
        return "REAL_START_RECEIVED"
    if timeout_reached:
        return "USER_DROPOFF_AT_BOT_START"
    return "WAITING_FOR_BOT_START"


def timeout_event_metadata(*, handoff_at: datetime, deadline_at: datetime, fired_at: datetime) -> dict[str, Any]:
    return {
        "handoff_at": handoff_at.isoformat(),
        "timeout_deadline_at": deadline_at.isoformat(),
        "timeout_fired_at": fired_at.isoformat(),
        "start_wait_timeout_reached": YES,
        "real_start_received": NO,
        "dropoff_classification": "USER_DROPOFF_AT_BOT_START",
    }


def _wait_seconds_from_metadata(metadata_json: str | None) -> int:
    try:
        return int(json.loads(metadata_json or "{}").get("bot_start_wait_seconds", 30))
    except (TypeError, ValueError, json.JSONDecodeError):
        return 30


async def process_bot_start_timeouts(session_factory, *, limit: int = 100) -> int:
    """Persist diagnostic timeout events without creating business conversions."""
    now = datetime.now(timezone.utc)
    async with session_factory() as db:
        timeout_exists = exists(select(BotTrafficEvent.id).where(
            BotTrafficEvent.bot_visit_id == BotTrafficVisit.id,
            BotTrafficEvent.event_type == "BOT_START_TIMEOUT_REACHED",
        ).correlate(BotTrafficVisit))
        real_start_exists = exists(select(BotTrafficEvent.id).where(
            BotTrafficEvent.bot_visit_id == BotTrafficVisit.id,
            BotTrafficEvent.event_type == "REAL_START_RECEIVED",
        ).correlate(BotTrafficVisit))
        rows = list((await db.execute(
            select(BotTrafficVisit, BotTrafficEvent)
            .join(BotTrafficEvent, BotTrafficEvent.bot_visit_id == BotTrafficVisit.id)
            .join(BotMaxIdentityLink, BotMaxIdentityLink.bot_visit_id == BotTrafficVisit.id)
            .where(
                BotTrafficEvent.event_type == "BOT_HANDOFF_CONFIRMED",
                BotTrafficVisit.status == "HANDOFF_CONFIRMED",
                BotTrafficVisit.bot_id == BotTrafficVisit.destination_id,
                BotMaxIdentityLink.bot_id == BotTrafficVisit.bot_id,
                BotMaxIdentityLink.destination_id == BotTrafficVisit.destination_id,
                ~timeout_exists,
                ~real_start_exists,
            )
            .order_by(BotTrafficEvent.event_time)
            .limit(limit)
        )).all())
        created = 0
        for visit, handoff in rows:
            wait_seconds = max(0, _wait_seconds_from_metadata(handoff.metadata_json))
            deadline = handoff.event_time + timedelta(seconds=wait_seconds)
            if now < deadline:
                continue
            db.add(BotTrafficEvent(
                bot_visit_id=visit.id,
                bot_id=visit.bot_id,
                destination_id=visit.destination_id,
                event_type="BOT_START_TIMEOUT_REACHED",
                event_time=now,
                metadata_json=json.dumps(timeout_event_metadata(
                    handoff_at=handoff.event_time,
                    deadline_at=deadline,
                    fired_at=now,
                ), ensure_ascii=False),
                dedupe_key=f"bot-telemetry:{visit.id}:BOT_START_TIMEOUT_REACHED",
            ))
            created += 1
        if created:
            await db.commit()
        return created


def handoff_snapshot(*, first_time: str, wait_seconds: int) -> dict[str, Any]:
    return {
        "bot_handoff_confirmed": True,
        "bot_open_confirmed": UNPROVEN,
        "first_time_bot_user": first_time,
        "start_screen_expected": YES,
        "start_screen_shown": UNPROVEN,
        "real_start_received": NO,
        "start_wait_timeout_reached": NO,
        "bot_start_wait_seconds": max(0, wait_seconds),
        "dropoff_classification": "WAITING_FOR_BOT_START",
    }
