from datetime import datetime, timedelta, timezone
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import BotMaxIdentityLink, BotTrafficEvent, BotTrafficVisit
from app.traffic import bot_start_observability as observability
from app.traffic.bot_start_observability import (
    dropoff_classification,
    first_time_bot_user,
    handoff_snapshot,
    start_wait_timeout_reached,
    timeout_event_metadata,
    process_bot_start_timeouts,
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


def test_timeout_event_is_diagnostic_only():
    handoff = datetime(2026, 9, 6, tzinfo=timezone.utc)
    metadata = timeout_event_metadata(
        handoff_at=handoff,
        deadline_at=handoff + timedelta(seconds=30),
        fired_at=handoff + timedelta(seconds=31),
    )
    assert metadata["start_wait_timeout_reached"] == "YES"
    assert metadata["real_start_received"] == "NO"
    assert metadata["dropoff_classification"] == "USER_DROPOFF_AT_BOT_START"


def test_runtime_contract_dedupes_real_start_before_conversion():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app/bot/menu.py").read_text(encoding="utf-8")
    start = source.index("async def bind_system_startapp")
    end = source.index("async def queue_system_channel_connected", start)
    section = source[start:end]
    assert 'event_type="REAL_START_RECEIVED"' in section
    assert 'dedupe_key=f"bot-telemetry:{visit.id}:REAL_START_RECEIVED"' in section
    assert 'internal_event="bot_started"' in section


def test_timeout_processor_is_diagnostic_and_identity_scoped():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app/traffic/bot_start_observability.py").read_text(encoding="utf-8")
    processor = source[source.index("async def process_bot_start_timeouts"):]
    assert 'event_type == "BOT_START_TIMEOUT_REACHED"' in processor
    assert "BotMaxIdentityLink.bot_id == BotTrafficVisit.bot_id" in processor
    assert "~real_start_exists" in processor
    assert "BotConversion" not in processor
    assert "send_message" not in processor


@pytest.mark.asyncio
async def test_timeout_processor_executes_correlated_exists_query(monkeypatch):
    """Execute the ORM query so SQLAlchemy auto-correlation regressions fail here."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        for model in (BotTrafficVisit, BotMaxIdentityLink, BotTrafficEvent):
            await connection.run_sync(model.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    # SQLite's DateTime emulation returns naive values; production PostgreSQL
    # returns the timezone-aware value used by the processor.
    handoff_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=60)
    class SQLiteDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return super().now(tz).replace(tzinfo=None)

    monkeypatch.setattr(observability, "datetime", SQLiteDateTime)
    async with sessions() as db:
        db.add(BotTrafficVisit(
            id="visit-timeout-regression",
            token="token-timeout-regression",
            bot_id="bot-1",
            destination_id="bot-1",
            status="HANDOFF_CONFIRMED",
        ))
        db.add(BotMaxIdentityLink(
            id="link-timeout-regression",
            bot_visit_id="visit-timeout-regression",
            bot_id="bot-1",
            destination_id="bot-1",
            max_user_id="user-1",
            startapp_token="token-timeout-regression",
            init_data_fingerprint="f" * 64,
        ))
        db.add(BotTrafficEvent(
            id="handoff-timeout-regression",
            bot_visit_id="visit-timeout-regression",
            bot_id="bot-1",
            destination_id="bot-1",
            event_type="BOT_HANDOFF_CONFIRMED",
            event_time=handoff_at,
            metadata_json=json.dumps({"bot_start_wait_seconds": 30}),
            dedupe_key="handoff-timeout-regression",
        ))
        await db.commit()

    assert await process_bot_start_timeouts(sessions) == 1
    async with sessions() as db:
        timeout = await db.scalar(select(BotTrafficEvent).where(
            BotTrafficEvent.bot_visit_id == "visit-timeout-regression",
            BotTrafficEvent.event_type == "BOT_START_TIMEOUT_REACHED",
        ))
        assert timeout is not None
    await engine.dispose()


def test_worker_runs_timeout_processor_without_changing_conversion_loop():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app/poller.py").read_text(encoding="utf-8")
    assert "process_bot_start_timeouts(SessionLocal)" in source
    assert "BOT_START_TIMEOUTS_PERSISTED" in source
