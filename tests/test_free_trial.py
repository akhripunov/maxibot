from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.bot.menu import FREE_TRIAL_INFO_TEXT, free_trial_active_text, free_trial_used_text
from app.integrations.subscriptions import (
    channel_promotion_entitlement_source,
    free_trial_status,
    service_entitlement_active,
)


def test_free_trial_copy_and_buttons_are_exactly_owner_facing():
    assert FREE_TRIAL_INFO_TEXT == (
        "🎁 7 дней бесплатно\n\n"
        "Попробуйте MAXIBOT бесплатно в течение 7 дней.\n\n"
        "✅ Один канал\n"
        "✅ Все функции продвижения\n"
        "✅ Интеграция с Яндекс.Директ\n"
        "✅ Аналитика и ежедневные отчёты\n\n"
        "Бесплатный период доступен один раз."
    )
    assert free_trial_active_text("Новости", datetime(2026, 10, 5, tzinfo=timezone.utc)) == (
        "🎁 Бесплатный период уже активен\n\nКанал: Новости\nАктивен до: 05.10.2026"
    )
    assert "Бесплатный период уже использован." in free_trial_used_text()


def test_trial_is_user_level_access_and_expires_after_seven_days():
    now = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
    end = now + timedelta(days=7)
    assert free_trial_status(end, now=now) == "активен"
    assert free_trial_status(end, now=end) == "истёк"
    assert service_entitlement_active(None, end, now=now) is True
    assert service_entitlement_active(None, end, now=end) is False


def test_trial_scope_is_service_level_and_one_channel_is_only_a_quota_marker():
    models = (Path(__file__).parents[1] / "app" / "db" / "models.py").read_text()
    migration = (Path(__file__).parents[1] / "alembic" / "versions" / "0073_free_trial_v1.py").read_text()
    assert "admin_id: Mapped[str]" in models
    assert "activated_channel_id" in models
    assert 'UniqueConstraint("admin_id"' in models or 'unique=True' in models
    assert "uq_free_trials_admin_id" in migration
    assert "ondelete=\"SET NULL\"" in migration


def test_channel_promotion_predicate_allows_only_trial_channel_and_preserves_paid_access():
    now = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
    end = now + timedelta(days=7)
    assert channel_promotion_entitlement_source(None, end, "channel-a", "channel-a", now=now) == "TRIAL"
    assert channel_promotion_entitlement_source(None, end, "channel-a", "channel-b", now=now) == "NONE"
    assert channel_promotion_entitlement_source(end, end, "channel-a", "channel-b", now=now) == "PAID"
    assert channel_promotion_entitlement_source(None, end, "channel-a", "channel-a", now=end) == "NONE"
    assert channel_promotion_entitlement_source(None, end, "channel-a", "channel-a", now=now) == "TRIAL"


def test_second_channel_is_visible_but_paid_only_paths_use_the_canonical_gate():
    menu = (Path(__file__).parents[1] / "app" / "bot" / "menu.py").read_text()
    reports = (Path(__file__).parents[1] / "app" / "daily_reports.py").read_text()
    main = (Path(__file__).parents[1] / "app" / "main.py").read_text()
    assert 'rows = [[(channel.title, f"promotion_channel:{channel.id}")] for channel in channels]' in menu
    assert 'resolve_channel_promotion_entitlement(db, admin.external_id, selected_id)' in menu
    assert 'channel_promotion_entitlement(db, admin.external_id, channel.id)' in reports
    assert 'channel_promotion_entitlement(db, owner.external_id, channel.id)' in main


def test_paid_tariff_screen_keeps_trial_above_the_three_existing_plans():
    source = (Path(__file__).parents[1] / "app" / "bot" / "menu.py").read_text()
    start = source.index('if screen == "subscription":')
    end = source.index('if screen == "free_trial":', start)
    block = source[start:end]
    assert '"🎁 Попробовать бесплатно", "free_trial"' in block
    assert 'buttons(("🎁 Попробовать бесплатно", "free_trial"), *plan_buttons' in block


def test_start_free_reuses_canonical_channel_connection_without_activating_trial():
    source = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    start = source.index('elif action == "free_trial_start":')
    end = source.index('elif action == "free_trial_connect":', start)
    block = source[start:end]
    assert 'await self._render(user_id, "connect_channel")' in block
    assert "begin_free_trial" not in block
    assert "activate_free_trial" not in block
