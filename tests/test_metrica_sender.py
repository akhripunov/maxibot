from datetime import datetime, timedelta, timezone

import pytest

from app.traffic.metrica_sender import (
    METRICA_VISIT_LINKAGE_WINDOW,
    _visit_window_expired,
    send_user_subscribed,
    send_user_unsubscribed,
)


def recent_visit():
    return datetime.now(timezone.utc) - timedelta(days=1)


class FakeMetrica:
    def __init__(self):
        self.calls = []

    async def upload_offline_conversion(self, *args):
        self.calls.append(args)
        return {"ok": True}

    async def ensure_action_goal_identifier(self, counter_id, goal_name):
        self.calls.append(("goal_identifier", counter_id, goal_name))
        return goal_name


@pytest.mark.asyncio
async def test_sender_requires_verified_membership_yclid_and_metrica_client_id(monkeypatch):
    client = FakeMetrica()
    monkeypatch.setattr("app.traffic.metrica_sender.settings.yandex_conversion_dry_run", False)
    result = await send_user_subscribed(client, counter_id=101, yclid=None, client_id="123456", conversion_datetime="2026-08-13T00:00:00Z", verified_membership=True, visit_at=recent_visit())
    assert result.status == "blocked"
    assert result.reason == "yclid_required"
    assert client.calls == []


@pytest.mark.asyncio
async def test_sender_defaults_to_dry_run_without_http_call(monkeypatch):
    client = FakeMetrica()
    monkeypatch.setattr("app.traffic.metrica_sender.settings.yandex_conversion_dry_run", True)
    result = await send_user_subscribed(client, counter_id=101, yclid="yclid-A", client_id="123456", conversion_datetime="2026-08-13T00:00:00Z", verified_membership=True, visit_at=recent_visit())
    assert result.status == "dry_run"
    assert client.calls == []


@pytest.mark.asyncio
async def test_sender_uploads_only_after_gate(monkeypatch):
    client = FakeMetrica()
    monkeypatch.setattr("app.traffic.metrica_sender.settings.yandex_conversion_dry_run", False)
    result = await send_user_subscribed(client, counter_id=101, yclid="yclid-A", client_id="123456", conversion_datetime="2026-08-13T00:00:00Z", verified_membership=True, visit_at=recent_visit())
    assert result.status == "sent"
    assert client.calls == [("goal_identifier", 101, "userDidSubscribe"), (101, "yclid-A", "123456", "userDidSubscribe", "2026-08-13T00:00:00Z")]


@pytest.mark.asyncio
async def test_max_subscriber_sender_is_dry_run_by_default(monkeypatch):
    from app.traffic.metrica_sender import send_max_subscriber
    client = FakeMetrica()
    monkeypatch.setattr("app.traffic.metrica_sender.settings.yandex_conversion_dry_run", True)
    result = await send_max_subscriber(client, counter_id=101, yclid="yclid-A", client_id="123456",
                                       conversion_datetime="2026-08-13T00:00:00Z", exact_membership=True,
                                       visit_at=recent_visit())
    assert result.status == "dry_run"
    assert client.calls == []


@pytest.mark.asyncio
async def test_unsubscribe_sender_uses_dedicated_goal_and_exact_gate(monkeypatch):
    client = FakeMetrica()
    monkeypatch.setattr("app.traffic.metrica_sender.settings.yandex_conversion_dry_run", False)
    result = await send_user_unsubscribed(client, counter_id=101, yclid="yclid-A", client_id="123456",
                                          conversion_datetime="2026-08-13T00:00:00Z", verified_membership=True,
                                          visit_at=recent_visit())
    assert result.status == "sent"
    assert client.calls == [("goal_identifier", 101, "userDidUnsubscribe"),
                             (101, "yclid-A", "123456", "userDidUnsubscribe", "2026-08-13T00:00:00Z")]


@pytest.mark.asyncio
@pytest.mark.parametrize("sender", [send_user_subscribed, send_user_unsubscribed])
async def test_sender_blocks_visits_outside_metrica_21_day_window(monkeypatch, sender):
    client = FakeMetrica()
    monkeypatch.setattr("app.traffic.metrica_sender.settings.yandex_conversion_dry_run", False)
    for _ in range(2):
        result = await sender(
            client,
            counter_id=101,
            yclid="yclid-A",
            client_id="123456",
            conversion_datetime="1700000000",
            verified_membership=True,
            visit_at=datetime.now(timezone.utc) - timedelta(days=22),
        )
        assert result.status == "blocked"
        assert result.reason == "metrica_visit_window_expired"
    assert client.calls == []


@pytest.mark.parametrize("age", [timedelta(days=20), timedelta(days=21) - timedelta(microseconds=1)])
def test_metrica_window_allows_recent_visits(age):
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    assert not _visit_window_expired(now - age, now=now)


@pytest.mark.asyncio
@pytest.mark.parametrize("sender", [send_user_subscribed, send_user_unsubscribed])
@pytest.mark.parametrize("age", [timedelta(days=20), timedelta(days=21) - timedelta(seconds=1)])
async def test_sender_preserves_api_flow_within_21_day_window(monkeypatch, sender, age):
    client = FakeMetrica()
    monkeypatch.setattr("app.traffic.metrica_sender.settings.yandex_conversion_dry_run", False)
    result = await sender(
        client,
        counter_id=101,
        yclid="yclid-A",
        client_id="123456",
        conversion_datetime="1700000000",
        verified_membership=True,
        visit_at=datetime.now(timezone.utc) - age,
    )
    assert result.status == "sent"
    assert client.calls == [
        ("goal_identifier", 101, "userDidSubscribe" if sender is send_user_subscribed else "userDidUnsubscribe"),
        (101, "yclid-A", "123456", "userDidSubscribe" if sender is send_user_subscribed else "userDidUnsubscribe", "1700000000"),
    ]


def test_metrica_window_21_day_boundary_is_inclusive_at_processing_time():
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    assert not _visit_window_expired(now - METRICA_VISIT_LINKAGE_WINDOW, now=now)
    assert _visit_window_expired(now - METRICA_VISIT_LINKAGE_WINDOW - timedelta(microseconds=1), now=now)


def test_metrica_window_uses_absolute_time_across_offsets():
    utc_visit = datetime(2026, 9, 9, 0, 0, tzinfo=timezone.utc)
    offset_visit = datetime.fromisoformat("2026-09-09T03:00:00+03:00")
    now = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)
    assert _visit_window_expired(utc_visit, now=now) == _visit_window_expired(offset_visit, now=now)


@pytest.mark.asyncio
@pytest.mark.parametrize("sender", [send_user_subscribed, send_user_unsubscribed])
async def test_sender_at_exactly_21_days_uploads_when_boundary_time_is_exact(monkeypatch, sender):
    class FrozenDateTime(datetime):
        current = datetime(2026, 9, 30, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr("app.traffic.metrica_sender.datetime", FrozenDateTime)
    monkeypatch.setattr("app.traffic.metrica_sender.settings.yandex_conversion_dry_run", False)
    client = FakeMetrica()
    result = await sender(
        client, counter_id=101, yclid="yclid-A", client_id="123456",
        conversion_datetime="1700000000", verified_membership=True,
        visit_at=FrozenDateTime.current - timedelta(days=21),
    )
    assert result.status == "sent"
    assert client.calls[-1][0] == 101


@pytest.mark.asyncio
@pytest.mark.parametrize("sender", [send_user_subscribed, send_user_unsubscribed])
async def test_sender_rechecks_age_after_goal_lookup_before_upload(monkeypatch, sender):
    class FrozenDateTime(datetime):
        current = datetime(2026, 9, 30, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    class CrossingBoundaryMetrica(FakeMetrica):
        async def ensure_action_goal_identifier(self, counter_id, goal_name):
            self.calls.append(("goal_identifier", counter_id, goal_name))
            FrozenDateTime.current += timedelta(seconds=1, microseconds=1)
            return goal_name

    monkeypatch.setattr("app.traffic.metrica_sender.datetime", FrozenDateTime)
    monkeypatch.setattr("app.traffic.metrica_sender.settings.yandex_conversion_dry_run", False)
    client = CrossingBoundaryMetrica()
    visit_at = FrozenDateTime.current - timedelta(days=21) + timedelta(seconds=1)
    result = await sender(
        client, counter_id=101, yclid="yclid-A", client_id="123456",
        conversion_datetime="1700000000", verified_membership=True, visit_at=visit_at,
    )
    assert result.status == "blocked"
    assert result.reason == "metrica_visit_window_expired"
    assert len(client.calls) == 1
    assert client.calls[0][0] == "goal_identifier"


@pytest.mark.asyncio
@pytest.mark.parametrize("sender", [send_user_subscribed, send_user_unsubscribed])
async def test_sender_fails_closed_when_source_visit_timestamp_missing(monkeypatch, sender):
    client = FakeMetrica()
    monkeypatch.setattr("app.traffic.metrica_sender.settings.yandex_conversion_dry_run", False)
    result = await sender(client, counter_id=101, yclid="yclid-A", client_id="123456",
                         conversion_datetime="1700000000", verified_membership=True, visit_at=None)
    assert result.status == "blocked"
    assert result.reason == "metrica_visit_timestamp_missing"
    assert client.calls == []
