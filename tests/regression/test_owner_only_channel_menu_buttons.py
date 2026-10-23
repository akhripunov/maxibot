from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot import menu
from app.db.models import Admin, Base, Channel


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        yield session
    await engine.dispose()


def _button_pairs(keyboard):
    return [(item["text"], item["callback_data"]) for row in keyboard for item in row]


async def _render_channel(db, monkeypatch, admin, channel):
    async def no_yandex(*args, **kwargs):
        return False

    async def no_metrica(*args, **kwargs):
        return {}

    monkeypatch.setattr(menu, "connection_status", no_yandex)
    monkeypatch.setattr(menu, "channel_setup_status", no_metrica)
    return await menu.render_screen(db, admin, f"promotion_channel:{channel.id}")


@pytest.mark.asyncio
async def test_maxibot_owner_keeps_all_three_buttons_and_original_order(db, monkeypatch):
    monkeypatch.setattr(menu.settings, "maxibot_owner_user_id", "maxibot-owner")
    owner = Admin(external_id="maxibot-owner")
    db.add(owner)
    await db.flush()
    channel = Channel(admin_id=owner.id, external_id="owner-channel", title="Owner channel", public_slug="owner-channel")
    db.add(channel)
    await db.flush()

    _, channel_keyboard = await _render_channel(db, monkeypatch, owner, channel)
    _, subscription_keyboard = await menu.render_screen(db, owner, "subscription")

    assert _button_pairs(channel_keyboard) == [
        ("🔗 Яндекс.Директ ⚠️ не подключен", f"promotion_yandex:{channel.id}"),
        ("🤖 AI Продвижение", f"ads:{channel.id}"),
        ("📊 Аналитика", f"promotion_analytics:{channel.id}"),
        ("⛔ Отключить ежедневные отчёты", f"daily_reports:{channel.id}:off"),
        ("📩 Обращения / Предложка", f"channel:{channel.id}"),
        ("🗑 Удалить канал", f"delete:{channel.id}"),
        ("⬅️ Назад", "promotion"),
    ]
    assert _button_pairs(subscription_keyboard) == [
        ("Тариф Start", "subscription_tariff:start"),
        ("Тариф Pro", "subscription_tariff:pro"),
        ("🤖 AI Продвижение", "subscription_ai"),
        ("⬅️ Назад", "home"),
    ]


@pytest.mark.asyncio
async def test_paid_channel_admin_without_maxibot_owner_id_does_not_see_owner_buttons(db, monkeypatch):
    monkeypatch.setattr(menu.settings, "maxibot_owner_user_id", "maxibot-owner")
    ordinary = Admin(
        external_id="paid-channel-admin",
        subscription_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        subscription_tariff="pro",
        ai_promotion_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(ordinary)
    await db.flush()
    channel = Channel(admin_id=ordinary.id, external_id="ordinary-channel", title="Ordinary channel", public_slug="ordinary-channel")
    db.add(channel)
    await db.flush()

    _, channel_keyboard = await _render_channel(db, monkeypatch, ordinary, channel)
    _, subscription_keyboard = await menu.render_screen(db, ordinary, "subscription")

    assert _button_pairs(channel_keyboard) == [
        ("🔗 Яндекс.Директ ⚠️ не подключен", f"promotion_yandex:{channel.id}"),
        ("📊 Аналитика", f"promotion_analytics:{channel.id}"),
        ("⛔ Отключить ежедневные отчёты", f"daily_reports:{channel.id}:off"),
        ("🗑 Удалить канал", f"delete:{channel.id}"),
        ("⬅️ Назад", "promotion"),
    ]
    assert _button_pairs(subscription_keyboard) == [
        ("Тариф Start", "subscription_tariff:start"),
        ("Тариф Pro", "subscription_tariff:pro"),
        ("⬅️ Назад", "home"),
    ]


@pytest.mark.asyncio
async def test_missing_maxibot_owner_configuration_hides_owner_buttons_without_render_error(db, monkeypatch):
    monkeypatch.setattr(menu.settings, "maxibot_owner_user_id", None)
    ordinary = Admin(external_id="ordinary-user")
    db.add(ordinary)
    await db.flush()
    channel = Channel(admin_id=ordinary.id, external_id="ordinary-channel-unconfigured", title="Channel", public_slug="channel")
    db.add(channel)
    await db.flush()

    _, channel_keyboard = await _render_channel(db, monkeypatch, ordinary, channel)
    _, subscription_keyboard = await menu.render_screen(db, ordinary, "subscription")

    assert all(callback not in {f"ads:{channel.id}", f"channel:{channel.id}"} for _, callback in _button_pairs(channel_keyboard))
    assert all(callback != "subscription_ai" for _, callback in _button_pairs(subscription_keyboard))
