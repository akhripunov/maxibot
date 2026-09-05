from datetime import datetime, timezone

from app.integrations.subscriptions import add_subscription_months, subscription_status


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
