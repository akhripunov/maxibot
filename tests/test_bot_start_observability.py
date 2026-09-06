from datetime import datetime, timedelta, timezone

from app.traffic.bot_start_observability import (
    dropoff_classification,
    first_time_bot_user,
    handoff_snapshot,
    start_wait_timeout_reached,
)


def test_first_time_without_start_is_explicit_and_times_out():
    assert first_time_bot_user(0) == "YES"
    snapshot = handoff_snapshot(first_time="YES", wait_seconds=30)
    assert snapshot["real_start_received"] == "NO"
    handoff = datetime(2026, 9, 6, tzinfo=timezone.utc)
    assert start_wait_timeout_reached(
        handoff, now=handoff + timedelta(seconds=30), wait_seconds=30
    )
    assert dropoff_classification(real_start_received=False, timeout_reached=True) == "USER_DROPOFF_AT_BOT_START"


def test_returning_user_without_start_is_not_first_time():
    assert first_time_bot_user(1) == "NO"
    assert first_time_bot_user(None) == "UNKNOWN"
    assert dropoff_classification(real_start_received=False, timeout_reached=False) == "WAITING_FOR_BOT_START"


def test_real_start_is_terminal_observation_without_screen_claims():
    snapshot = handoff_snapshot(first_time="YES", wait_seconds=30)
    assert snapshot["bot_open_confirmed"] == "UNPROVEN"
    assert snapshot["start_screen_shown"] == "UNPROVEN"
    assert dropoff_classification(real_start_received=True, timeout_reached=False) == "REAL_START_RECEIVED"


def test_runtime_contract_dedupes_real_start_before_conversion():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app/bot/menu.py").read_text(encoding="utf-8")
    start = source.index("async def bind_system_startapp")
    end = source.index("async def queue_system_channel_connected", start)
    section = source[start:end]
    assert 'event_type="REAL_START_RECEIVED"' in section
    assert 'dedupe_key=f"bot-telemetry:{visit.id}:REAL_START_RECEIVED"' in section
    assert 'internal_event="bot_started"' in section
