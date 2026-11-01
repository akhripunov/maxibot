from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.bot.menu import (
    FREE_TIER_INFO_TEXT,
    paid_subscription_status_text,
    service_subscription_text,
)
from app.integrations.subscriptions import (
    channel_promotion_entitlement,
    channel_promotion_entitlement_source,
)
from app.db.models import Admin, Base, Channel, FreeTrial, LandingPage, TrafficProfile, TrafficVisit


def test_free_tier_copy_has_no_trial_or_activation_requirement():
    assert "Free действует бессрочно" in FREE_TIER_INFO_TEXT
    assert "Дополнительная активация не требуется" in FREE_TIER_INFO_TEXT
    assert "7 дней" not in FREE_TIER_INFO_TEXT


def test_legacy_trial_records_remain_stored_for_history_without_new_schema():
    models = (Path(__file__).parents[1] / "app" / "db" / "models.py").read_text()
    migration = (Path(__file__).parents[1] / "alembic" / "versions" / "0073_free_trial_v1.py").read_text()
    assert "admin_id: Mapped[str]" in models
    assert "activated_channel_id" in models
    assert 'UniqueConstraint("admin_id"' in models or 'unique=True' in models
    assert "uq_free_trials_admin_id" in migration
    assert "ondelete=\"SET NULL\"" in migration


def test_channel_promotion_predicate_grants_first_free_channel_without_expiry():
    now = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
    assert channel_promotion_entitlement_source(None, True, "channel-a", now=now) == "FREE"
    assert channel_promotion_entitlement_source(None, True, "channel-a", now=now + timedelta(days=30)) == "FREE"
    assert channel_promotion_entitlement_source(None, False, "channel-b", now=now) == "NONE"
    assert channel_promotion_entitlement_source(now + timedelta(days=3), False, "channel-b", now=now) == "PAID"
    assert channel_promotion_entitlement_source(now - timedelta(days=1), True, "channel-a", now=now) == "FREE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "subscription_expires_at",
    [None, datetime(2026, 9, 1, tzinfo=timezone.utc)],
)
async def test_canonical_owner_gets_only_own_channel_promotion_access_without_active_subscription(
    monkeypatch, subscription_expires_at,
):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.integrations.subscriptions.settings.maxibot_owner_user_id", "configured-owner")
    try:
        async with sessions() as db:
            owner = Admin(
                external_id="configured-owner",
                subscription_expires_at=subscription_expires_at,
                subscription_tariff="start",
                ai_promotion_expires_at=None,
            )
            ordinary = Admin(external_id="ordinary-user")
            db.add_all([owner, ordinary])
            await db.flush()
            owner_channel = Channel(
                admin_id=owner.id, external_id="owner-channel", title="Москва 365", public_slug="owner-channel",
            )
            ordinary_channel = Channel(
                admin_id=ordinary.id, external_id="ordinary-channel", title="Москва 365", public_slug="ordinary-channel",
            )
            db.add_all([owner_channel, ordinary_channel])
            await db.flush()
            owner_landing = LandingPage(
                channel_id=owner_channel.id,
                public_id="owner-landing",
                title="Owner landing",
                description="test",
            )
            ordinary_landing = LandingPage(
                channel_id=ordinary_channel.id,
                public_id="ordinary-landing",
                title="Ordinary landing",
                description="test",
            )
            db.add_all([
                owner_landing,
                ordinary_landing,
                TrafficProfile(
                    channel_id=owner_channel.id,
                    enabled=True,
                    yandex_enabled=False,
                    target_url="https://max.ru/owner-channel-invite",
                ),
                TrafficProfile(
                    channel_id=ordinary_channel.id,
                    enabled=True,
                    yandex_enabled=False,
                    target_url="https://max.ru/ordinary-channel-invite",
                ),
            ])
            db.add(FreeTrial(
                admin_id=owner.id,
                activated_channel_id=owner_channel.id,
                status="EXPIRED",
                trial_start_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
                trial_end_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
            ))
            await db.commit()

            assert await channel_promotion_entitlement(db, owner.external_id, owner_channel.id) == "OWNER"
            assert await channel_promotion_entitlement(db, owner.external_id, ordinary_channel.id) == "NONE"
            assert await channel_promotion_entitlement(db, ordinary.external_id, ordinary_channel.id) == "FREE"
            assert await channel_promotion_entitlement(db, owner.external_id, "missing-channel") == "NONE"

            monkeypatch.setattr("app.integrations.subscriptions.settings.maxibot_owner_user_id", None)
            assert await channel_promotion_entitlement(db, owner.external_id, owner_channel.id) == "FREE"
            monkeypatch.setattr("app.integrations.subscriptions.settings.maxibot_owner_user_id", "configured-owner")

            from app import main as main_module

            monkeypatch.setattr(main_module.settings, "traffic_bot_attribution_enabled", False)

            def request(path: str, *, cookie: str | None = None) -> Request:
                headers = [(b"host", b"go.maxibot.pro"), (b"user-agent", b"pytest")]
                if cookie:
                    headers.append((b"cookie", cookie.encode()))
                return Request({
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": "GET",
                    "scheme": "https",
                    "path": path,
                    "raw_path": path.encode(),
                    "query_string": b"",
                    "headers": headers,
                    "server": ("go.maxibot.pro", 443),
                    "client": ("test", 1),
                })

            landing_response = await main_module._landing_page(
                owner_landing.public_id, request("/l/owner-landing"), db,
            )
            assert landing_response.status_code == 307
            assert landing_response.headers["location"] == "https://max.ru/owner-channel-invite"
            owner_visit = await db.scalar(select(TrafficVisit).where(
                TrafficVisit.landing_page_id == owner_landing.id,
            ))
            assert owner_visit is not None
            preview_response = await main_module._channel_preview(
                owner_landing.public_id,
                request("/landing/owner-landing", cookie=f"maxibot_traffic_visit={owner_visit.visit_token}"),
                db,
            )
            assert preview_response.status_code == 200
            redirect_response = await main_module._traffic_redirect(
                owner_landing.public_id, owner_visit.visit_token, db,
                request=request(f"/track/owner-landing/click?token={owner_visit.visit_token}"),
            )
            assert redirect_response.status_code == 307
            assert redirect_response.headers["location"] == "https://max.ru/owner-channel-invite"

            ordinary_response = await main_module._landing_page(
                ordinary_landing.public_id, request("/l/ordinary-landing"), db,
            )
            assert ordinary_response.status_code == 307
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_paid_and_free_channels_are_separate_from_legacy_trial_rows(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr("app.integrations.subscriptions.settings.maxibot_owner_user_id", "configured-owner")
    try:
        async with sessions() as db:
            paid = Admin(external_id="paid-user", subscription_expires_at=now + timedelta(days=3), subscription_tariff="pro")
            trial = Admin(external_id="trial-user")
            unentitled = Admin(external_id="unentitled-user")
            db.add_all([paid, trial, unentitled])
            await db.flush()
            channels = [
                Channel(
                    admin_id=admin.id, external_id=f"{admin.external_id}-channel", title="Channel",
                    public_slug=f"{admin.external_id}-channel",
                )
                for admin in (paid, trial, unentitled)
            ]
            db.add_all(channels)
            await db.flush()
            unentitled_second = Channel(
                admin_id=unentitled.id, external_id="unentitled-second", title="Second",
                public_slug="unentitled-second",
            )
            db.add(unentitled_second)
            await db.flush()
            db.add(FreeTrial(
                admin_id=unentitled.id,
                activated_channel_id=unentitled_second.id,
                status="ACTIVE",
                trial_start_at=now - timedelta(days=1),
                trial_end_at=now + timedelta(days=6),
            ))
            await db.commit()

            assert await channel_promotion_entitlement(db, paid.external_id, channels[0].id) == "PAID"
            assert await channel_promotion_entitlement(db, trial.external_id, channels[1].id) == "FREE"
            assert await channel_promotion_entitlement(db, unentitled.external_id, channels[2].id) == "FREE"
            assert await channel_promotion_entitlement(db, unentitled.external_id, unentitled_second.id) == "NONE"
    finally:
        await engine.dispose()


def test_owner_channel_consumers_use_canonical_entitlement():
    menu = (Path(__file__).parents[1] / "app" / "bot" / "menu.py").read_text()
    reports = (Path(__file__).parents[1] / "app" / "daily_reports.py").read_text()
    assert "resolve_channel_promotion_entitlement(db, external_id, channel.id)" in menu
    assert "channel_promotion_entitlement(db, admin.external_id, channel.id)" in reports


def test_second_channel_is_visible_but_paid_only_paths_use_the_canonical_gate():
    menu = (Path(__file__).parents[1] / "app" / "bot" / "menu.py").read_text()
    reports = (Path(__file__).parents[1] / "app" / "daily_reports.py").read_text()
    main = (Path(__file__).parents[1] / "app" / "main.py").read_text()
    assert 'rows = [[(channel.title, f"promotion_channel:{channel.id}")] for channel in channels]' in menu
    assert 'resolve_channel_promotion_entitlement(db, admin.external_id, selected_id)' in menu
    assert 'channel_promotion_entitlement(db, admin.external_id, channel.id)' in reports
    assert 'channel_promotion_entitlement(db, owner.external_id, channel.id)' in main


def test_subscription_screen_keeps_paid_plans_and_separates_ai_offer():
    source = (Path(__file__).parents[1] / "app" / "bot" / "menu.py").read_text()
    start = source.index('if screen == "subscription":')
    end = source.index('if screen.startswith("subscription_limit:")', start)
    block = source[start:end]
    assert '"Тариф Start", "subscription_tariff:start"' in block
    assert '"Тариф Pro", "subscription_tariff:pro"' in block
    assert '"🤖 AI Продвижение", "subscription_ai"' in block


def test_subscription_status_copy_is_service_level_and_server_side():
    now = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
    expiry = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
    assert service_subscription_text() == (
        "💳 Оплата тарифа\n\n🤖 MAXIBOT\n\nПодписка не активна.\n\nВыберите период подписки:"
    )
    assert paid_subscription_status_text(expiry, now=now) == (
        "✅ Подписка активна\n\nОплачено до: 14.09.2026\nОсталось: 7 дней"
    )


def test_subscription_status_handles_hours_without_timezone_ambiguity():
    source = (Path(__file__).parents[1] / "app" / "bot" / "menu.py").read_text()
    assert "math.ceil(seconds / 3600)" in source
    assert "expiry.astimezone(MOSCOW)" in source


def test_opening_subscription_does_not_create_or_activate_a_trial():
    source = (Path(__file__).parents[1] / "app" / "bot" / "menu.py").read_text()
    assert "ensure_existing_channel_free_trial" not in source
    assert "activate_free_trial" not in source


def test_legacy_free_trial_start_callback_only_opens_channel_connection():
    source = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    start = source.index('elif action == "free_trial_start":')
    end = source.index('elif action == "free_trial_connect":', start)
    block = source[start:end]
    assert 'await self.repo.begin_free_trial(str(user_id))' not in block
    assert 'await self._render(user_id, "connect_channel")' in block
    assert "activate_free_trial" not in block


def test_legacy_free_trial_callback_does_not_activate_or_write_a_trial():
    source = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    start = source.index('elif action == "free_trial_start":')
    end = source.index('elif action == "free_trial_connect":', start)
    block = source[start:end]
    assert 'await self.repo.begin_free_trial(str(user_id))' not in block
    assert 'await self._render(user_id, "connect_channel")' in block
    assert "activate_free_trial" not in block
