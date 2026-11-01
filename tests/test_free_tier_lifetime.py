from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.bot import menu
from app.bot.runtime import MaxBotRuntime
from app.db.models import (
    Admin,
    Base,
    Channel,
    FreeTrial,
    LandingPage,
    OfflineConversion,
    TrafficMembership,
    TrafficProfile,
    TrafficSubscription,
)
from app.daily_reports import send_due_daily_reports
from app.integrations.subscriptions import channel_promotion_entitlement


def _request(path: str) -> Request:
    return Request({
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"host", b"go.maxibot.pro"), (b"user-agent", b"Android")],
        "server": ("go.maxibot.pro", 443),
        "client": ("test", 1),
    })


@pytest.mark.asyncio
async def test_new_user_gets_lifetime_free_without_trial_activation(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    repo = menu.MenuRepository(sessions)
    try:
        admin = await repo.get_admin("new-free-user")
        assert admin.subscription_tariff == "free"
        assert admin.subscription_expires_at is None

        async with sessions() as db:
            channel = Channel(
                admin_id=admin.id,
                external_id="free-channel",
                title="Free channel",
                public_slug="free-channel",
            )
            db.add(channel)
            await db.commit()

        monkeypatch.setattr(menu, "connection_status", _async_false)
        monkeypatch.setattr(menu, "channel_setup_status", _async_empty)
        text, _keyboard, _message_id, _media = await repo.render("new-free-user", "subscription")
        assert "Текущий тариф: Бесплатный" in text
        assert "Оплачено до: бессрочно" in text
        async with sessions() as db:
            assert await db.scalar(select(func.count(FreeTrial.id))) == 0
            admin = await db.scalar(select(Admin).where(Admin.external_id == "new-free-user"))
            channel = await db.scalar(select(Channel).where(Channel.external_id == "free-channel"))
            assert await channel_promotion_entitlement(db, admin.external_id, channel.id) == "FREE"
    finally:
        await engine.dispose()


async def _async_false(*_args, **_kwargs):
    return False


async def _async_empty(*_args, **_kwargs):
    return {}


@pytest.mark.asyncio
async def test_free_first_channel_landing_redirects_and_does_not_create_conversion(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.main.settings.traffic_bot_attribution_enabled", False)
    try:
        async with sessions() as db:
            free_user = Admin(external_id="free-owner", subscription_tariff="free")
            other_user = Admin(external_id="other-owner", subscription_tariff="free")
            db.add_all([free_user, other_user])
            await db.flush()
            first = Channel(
                id="00000000-0000-0000-0000-000000000001",
                admin_id=free_user.id,
                external_id="first-channel",
                title="First",
                public_slug="first-channel",
                connected_at=datetime.now(timezone.utc) - timedelta(days=20),
            )
            second = Channel(
                id="00000000-0000-0000-0000-000000000002",
                admin_id=free_user.id,
                external_id="second-channel",
                title="Second",
                public_slug="second-channel",
                connected_at=datetime.now(timezone.utc) - timedelta(days=2),
            )
            foreign = Channel(
                id="00000000-0000-0000-0000-000000000003",
                admin_id=other_user.id,
                external_id="foreign-channel",
                title="Foreign",
                public_slug="foreign-channel",
            )
            db.add_all([first, second, foreign])
            await db.flush()
            db.add_all([
                TrafficProfile(channel_id=first.id, enabled=True, target_url="https://max.ru/first-invite"),
                TrafficProfile(channel_id=second.id, enabled=True, target_url="https://max.ru/second-invite"),
            ])
            first_landing = LandingPage(
                channel_id=first.id, public_id="free-first-landing", title="First", description="test",
            )
            second_landing = LandingPage(
                channel_id=second.id, public_id="free-second-landing", title="Second", description="test",
            )
            db.add_all([first_landing, second_landing])
            # Legacy trial state must neither gate the first channel nor grant
            # a second Free channel.
            db.add(FreeTrial(
                admin_id=free_user.id,
                activated_channel_id=second.id,
                status="ACTIVE",
                trial_start_at=datetime.now(timezone.utc) - timedelta(days=8),
                trial_end_at=datetime.now(timezone.utc) - timedelta(days=1),
            ))
            await db.commit()

            assert await channel_promotion_entitlement(db, free_user.external_id, first.id) == "FREE"
            assert await channel_promotion_entitlement(db, free_user.external_id, second.id) == "NONE"
            assert await channel_promotion_entitlement(db, free_user.external_id, foreign.id) == "NONE"

            from app import main as main_module

            response = await main_module._landing_page(
                first_landing.public_id, _request("/free-first-landing"), db,
            )
            assert response.status_code == 307
            assert response.headers["location"] == "https://max.ru/first-invite"
            with pytest.raises(HTTPException) as denied:
                await main_module._landing_page(second_landing.public_id, _request("/free-second-landing"), db)
            assert denied.value.status_code == 402

            assert await db.scalar(select(func.count(TrafficMembership.id))) == 0
            assert await db.scalar(select(func.count(TrafficSubscription.id))) == 0
            assert await db.scalar(select(func.count(OfflineConversion.id))) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_first_free_channel_receives_daily_report(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    class ReportClient:
        def __init__(self):
            self.sent = []

        async def get_chat(self, _channel_external_id):
            return {"participants_count": 12}

        async def send_message(self, recipient, message, **kwargs):
            self.sent.append((recipient, message, kwargs))

    client = ReportClient()
    try:
        async with sessions() as db:
            admin = Admin(external_id="free-report-owner", subscription_tariff="free")
            db.add(admin)
            await db.flush()
            channel = Channel(
                admin_id=admin.id,
                external_id="free-report-channel",
                title="Free report channel",
                public_slug="free-report-channel",
                daily_reports_enabled=True,
                connected_at=datetime(2026, 10, 30, tzinfo=timezone.utc),
            )
            db.add(channel)
            await db.commit()

        delivered = await send_due_daily_reports(
            client,
            sessions,
            now=datetime(2026, 11, 1, 9, tzinfo=ZoneInfo("Europe/Moscow")),
        )
        assert delivered == 1
        assert len(client.sent) == 1
        assert client.sent[0][0] == "free-report-owner"
        assert "Free report channel" in client.sent[0][1]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_free_promotion_menu_has_landing_link_and_yandex_analytics_but_no_ai_entitlement(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    repo = menu.MenuRepository(sessions)
    monkeypatch.setattr(menu, "connection_status", _async_false)
    monkeypatch.setattr(menu, "channel_setup_status", _async_empty)
    monkeypatch.setattr(menu, "configured", lambda: False)
    async def _stats(*_args, **_kwargs):
        return {"visits": 0, "to_channel": 0, "lifetime_ads_subscribed": 0, "lifetime_ads_unsubscribed": 0}
    monkeypatch.setattr(menu, "traffic_stats", _stats)
    try:
        admin = await repo.get_admin("free-menu-user")
        async with sessions() as db:
            channel = Channel(
                admin_id=admin.id,
                external_id="menu-channel",
                title="Menu channel",
                public_slug="menu-channel",
            )
            db.add(channel)
            await db.flush()
            db.add(TrafficProfile(
                channel_id=channel.id, enabled=True, target_url="https://max.ru/menu-invite",
            ))
            await db.commit()

            text, keyboard, _message_id, _media = await repo.render(admin.external_id, f"promotion_channel:{channel.id}")
            landing = await db.scalar(select(LandingPage).where(
                LandingPage.channel_id == channel.id,
                LandingPage.is_active.is_(True),
            ))
            assert landing is not None
            assert menu.public_landing_url(landing.public_id) in text
            assert not any(
                button.get("text") == "🌐 Открыть лендинг"
                for row in keyboard
                for button in row
            )
            assert not any(button.get("callback_data") == f"ads:{channel.id}" for row in keyboard for button in row)

            yandex_text, _ = await menu.render_screen(db, admin, f"promotion_yandex:{channel.id}")
            analytics_text, _ = await menu.render_screen(db, admin, f"promotion_analytics:{channel.id}")
            assert "Подключение Яндекс.Директ" in yandex_text
            assert "📊 Аналитика" in analytics_text

        runtime = MaxBotRuntime(object(), sessions)
        assert await runtime._ai_promotion_active(admin.external_id) is False
    finally:
        await engine.dispose()
