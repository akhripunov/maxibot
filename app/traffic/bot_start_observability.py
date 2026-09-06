"""Pure helpers for BOT start-screen diagnostics."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

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
