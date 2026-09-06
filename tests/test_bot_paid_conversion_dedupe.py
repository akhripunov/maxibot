import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, BotConversion
from app.traffic.bot_conversion_dedupe import paid_bot_conversion_key


def test_paid_key_is_per_bot_user_and_yclid():
    first = paid_bot_conversion_key(bot_id="bot-a", max_user_id="user-a", yclid="Y1")
    assert first == paid_bot_conversion_key(bot_id="bot-a", max_user_id="user-a", yclid="Y1")
    assert first != paid_bot_conversion_key(bot_id="bot-a", max_user_id="user-a", yclid="Y2")
    assert first != paid_bot_conversion_key(bot_id="bot-b", max_user_id="user-a", yclid="Y1")
    assert first != paid_bot_conversion_key(bot_id="bot-a", max_user_id="user-b", yclid="Y1")
    assert paid_bot_conversion_key(bot_id="bot-a", max_user_id="user-a", yclid=None) is None


@pytest.mark.asyncio
async def test_database_idempotency_keeps_one_paid_conversion_and_historical_nulls():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(BotConversion.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    key = paid_bot_conversion_key(bot_id="bot-a", max_user_id="user-a", yclid="Y1")

    async def insert(row_id, visit_id, journey_key):
        async with sessions() as db:
            await db.execute(sqlite_insert(BotConversion).values(
                id=row_id,
                bot_visit_id=visit_id,
                bot_id="bot-a",
                destination_id="bot-a",
                max_user_id="user-a",
                internal_event="bot_started",
                status="pending",
                paid_journey_key=journey_key,
            ).on_conflict_do_nothing(index_elements=[BotConversion.paid_journey_key]))
            await db.commit()

    await asyncio.gather(
        insert("conversion-1", "visit-1", key),
        insert("conversion-2", "visit-2", key),
    )
    await insert("conversion-repeat", "visit-1", key)
    async with sessions() as db:
        count = await db.scalar(select(func.count()).select_from(BotConversion))
        assert count == 1
        await db.execute(sqlite_insert(BotConversion).values(
            id="historical-1", bot_visit_id="old-1", bot_id="bot-a",
            destination_id="bot-a", max_user_id="user-a", internal_event="bot_started",
            status="sent", paid_journey_key=None,
        ))
        await db.execute(sqlite_insert(BotConversion).values(
            id="historical-2", bot_visit_id="old-2", bot_id="bot-a",
            destination_id="bot-a", max_user_id="user-a", internal_event="bot_started",
            status="sent", paid_journey_key=None,
        ))
        await db.commit()
        count = await db.scalar(select(func.count()).select_from(BotConversion))
        assert count == 3
    await engine.dispose()


def test_dedupe_does_not_touch_channel_processor_or_metrika_semantics():
    from pathlib import Path

    root = Path(__file__).parents[1]
    channel = (root / "app/bot/menu.py").read_text(encoding="utf-8")
    processor = (root / "app/traffic/system_conversion_processor.py").read_text(encoding="utf-8")
    assert "paid_bot_conversion_key" in channel
    assert "BotTrafficVisit" in processor
    assert "channel" not in processor.lower()


def test_migration_is_linear_and_fits_production_revision_storage():
    from pathlib import Path
    import re

    migration = (Path(__file__).parents[1] / "alembic/versions/0071_bot_paid_conversion_idempotency.py").read_text(encoding="utf-8")
    revision = re.search(r'^revision = "([^"]+)"', migration, re.MULTILINE).group(1)
    down_revision = re.search(r'^down_revision = "([^"]+)"', migration, re.MULTILINE).group(1)
    assert len(revision) <= 32
    assert down_revision == "0070_service_subscription_orders"
    assert "create_unique_constraint" in migration
    assert "historical" not in migration.lower()
