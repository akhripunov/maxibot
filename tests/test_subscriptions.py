from datetime import datetime, timezone

from app.integrations.subscriptions import add_subscription_months, subscription_status
from app.integrations.robokassa import amount_for_months


def test_subscription_starts_now_and_uses_calendar_months():
    now = datetime(2026, 1, 31, 12, tzinfo=timezone.utc)
    assert add_subscription_months(None, 1, now=now) == datetime(2026, 2, 28, 12, tzinfo=timezone.utc)


def test_subscription_extends_live_period_and_does_not_lose_existing_time():
    now = datetime(2026, 5, 10, 12, tzinfo=timezone.utc)
    expiry = datetime(2026, 6, 30, 12, tzinfo=timezone.utc)
    assert add_subscription_months(expiry, 3, now=now) == datetime(2026, 9, 30, 12, tzinfo=timezone.utc)


def test_subscription_status_is_explicit():
    now = datetime(2026, 5, 10, 12, tzinfo=timezone.utc)
    assert subscription_status(None, now=now) == "не активирован"
    assert subscription_status(datetime(2026, 5, 11, 12, tzinfo=timezone.utc), now=now) == "активен"
    assert subscription_status(datetime(2026, 5, 9, 12, tzinfo=timezone.utc), now=now) == "истёк"


def test_start_and_pro_tariff_prices_use_existing_period_discounts():
    assert amount_for_months(1, "start") == 1900
    assert amount_for_months(3, "start") == 5130
    assert amount_for_months(6, "start") == 7980
    assert amount_for_months(1, "pro") == 4900
    assert amount_for_months(3, "pro") == 13230
    assert amount_for_months(6, "pro") == 20580


def test_subscription_entitlement_migration_backfills_historical_paid_users_and_ai_order_kind():
    from pathlib import Path

    migration = Path("alembic/versions/0083_subscription_entitlements_v1.py").read_text()
    assert '"subscription_tariff"' in migration
    assert '"ai_promotion_expires_at"' in migration
    assert '"order_kind"' in migration
    assert "subscription_expires_at IS NOT NULL" in migration


def test_quota_and_ai_payment_paths_are_explicit_and_isolated():
    from pathlib import Path

    menu = Path("app/bot/menu.py").read_text()
    runtime = Path("app/bot/runtime.py").read_text()
    assert '"free": (1, 0), "start": (3, 1), "pro": (10, 3)' in menu
    assert '"SUBSCRIPTION_CHANNEL_LIMIT"' in menu
    assert '"SUBSCRIPTION_BOT_LIMIT"' in menu
    assert 'order_kind="ai_promotion"' in runtime
    assert 'admin.ai_promotion_expires_at' in Path("app/main.py").read_text()
