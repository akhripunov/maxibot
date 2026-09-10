import pytest

from app.traffic.metrica_sender import send_user_subscribed, send_user_unsubscribed


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
    result = await send_user_subscribed(client, counter_id=101, yclid=None, client_id="123456", conversion_datetime="2026-08-13T00:00:00Z", verified_membership=True)
    assert result.status == "blocked"
    assert result.reason == "yclid_required"
    assert client.calls == []


@pytest.mark.asyncio
async def test_sender_defaults_to_dry_run_without_http_call(monkeypatch):
    client = FakeMetrica()
    monkeypatch.setattr("app.traffic.metrica_sender.settings.yandex_conversion_dry_run", True)
    result = await send_user_subscribed(client, counter_id=101, yclid="yclid-A", client_id="123456", conversion_datetime="2026-08-13T00:00:00Z", verified_membership=True)
    assert result.status == "dry_run"
    assert client.calls == []


@pytest.mark.asyncio
async def test_sender_uploads_only_after_gate(monkeypatch):
    client = FakeMetrica()
    monkeypatch.setattr("app.traffic.metrica_sender.settings.yandex_conversion_dry_run", False)
    result = await send_user_subscribed(client, counter_id=101, yclid="yclid-A", client_id="123456", conversion_datetime="2026-08-13T00:00:00Z", verified_membership=True)
    assert result.status == "sent"
    assert client.calls == [("goal_identifier", 101, "userDidSubscribe"), (101, "yclid-A", "123456", "userDidSubscribe", "2026-08-13T00:00:00Z")]


@pytest.mark.asyncio
async def test_max_subscriber_sender_is_dry_run_by_default(monkeypatch):
    from app.traffic.metrica_sender import send_max_subscriber
    client = FakeMetrica()
    monkeypatch.setattr("app.traffic.metrica_sender.settings.yandex_conversion_dry_run", True)
    result = await send_max_subscriber(client, counter_id=101, yclid="yclid-A", client_id="123456",
                                       conversion_datetime="2026-08-13T00:00:00Z", exact_membership=True)
    assert result.status == "dry_run"
    assert client.calls == []


@pytest.mark.asyncio
async def test_unsubscribe_sender_uses_dedicated_goal_and_exact_gate(monkeypatch):
    client = FakeMetrica()
    monkeypatch.setattr("app.traffic.metrica_sender.settings.yandex_conversion_dry_run", False)
    result = await send_user_unsubscribed(client, counter_id=101, yclid="yclid-A", client_id="123456",
                                          conversion_datetime="2026-08-13T00:00:00Z", verified_membership=True)
    assert result.status == "sent"
    assert client.calls == [("goal_identifier", 101, "userDidUnsubscribe"),
                             (101, "yclid-A", "123456", "userDidUnsubscribe", "2026-08-13T00:00:00Z")]
