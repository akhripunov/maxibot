import pytest

from app.traffic.metrica_sender import send_user_subscribed


class FakeMetrica:
    def __init__(self):
        self.calls = []

    async def upload_offline_conversion(self, *args):
        self.calls.append(args)
        return {"ok": True}


@pytest.mark.asyncio
async def test_sender_requires_verified_membership_and_yclid(monkeypatch):
    client = FakeMetrica()
    monkeypatch.setattr("app.traffic.metrica_sender.settings.yandex_conversion_dry_run", False)
    result = await send_user_subscribed(client, counter_id=101, yclid=None, conversion_datetime="2026-08-13T00:00:00Z", verified_membership=True)
    assert result.status == "blocked"
    assert result.reason == "yclid_required"
    assert client.calls == []


@pytest.mark.asyncio
async def test_sender_defaults_to_dry_run_without_http_call(monkeypatch):
    client = FakeMetrica()
    monkeypatch.setattr("app.traffic.metrica_sender.settings.yandex_conversion_dry_run", True)
    result = await send_user_subscribed(client, counter_id=101, yclid="yclid-A", conversion_datetime="2026-08-13T00:00:00Z", verified_membership=True)
    assert result.status == "dry_run"
    assert client.calls == []


@pytest.mark.asyncio
async def test_sender_uploads_only_after_gate(monkeypatch):
    client = FakeMetrica()
    monkeypatch.setattr("app.traffic.metrica_sender.settings.yandex_conversion_dry_run", False)
    result = await send_user_subscribed(client, counter_id=101, yclid="yclid-A", conversion_datetime="2026-08-13T00:00:00Z", verified_membership=True)
    assert result.status == "sent"
    assert client.calls == [(101, "yclid-A", "userDidSubscribe", "2026-08-13T00:00:00Z")]
