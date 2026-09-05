from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from app.core.config import settings
from app.traffic import system_conversion_processor as processor


class FakeContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *args):
        return False


class FakeDb:
    def __init__(self, profile, rows, current=None):
        self.profile = profile
        self.rows = rows
        self.current = current
        self.commits = 0

    async def scalar(self, statement):
        return self.profile

    async def execute(self, statement):
        return SimpleNamespace(all=lambda: self.rows)

    async def get(self, model, object_id):
        return self.current

    async def commit(self):
        self.commits += 1


class FakeFactory:
    def __init__(self, db):
        self.db = db
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return FakeContext(self.db)


class FakeMetrica:
    instances = []

    def __init__(self, token):
        self.token = token
        self.goals = []
        self.uploads = []
        self.closed = False
        self.instances.append(self)

    async def ensure_action_goal_identifier(self, counter_id, goal):
        self.goals.append((counter_id, goal))
        return "goal-id"

    async def upload_offline_conversion(self, *args):
        self.uploads.append(args)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_dry_run_is_bounded_and_has_no_db_or_network_side_effect(monkeypatch):
    monkeypatch.setattr(settings, "yandex_conversion_dry_run", True)
    factory = FakeFactory(None)
    assert await processor.process_system_conversions(factory) == 0
    assert factory.calls == 0
    assert FakeMetrica.instances == []


@pytest.mark.asyncio
async def test_success_marks_conversion_sent_once_and_closes_client(monkeypatch):
    monkeypatch.setattr(settings, "yandex_conversion_dry_run", False)
    monkeypatch.setattr(processor, "decrypt_token", lambda value: "opaque-token")
    monkeypatch.setattr(processor, "YandexMetricaClient", FakeMetrica)
    FakeMetrica.instances.clear()
    created_at = datetime(2026, 9, 5, 7, tzinfo=timezone.utc)
    conversion = SimpleNamespace(id="conversion-1", status="pending", goal="bot_started", created_at=created_at)
    visit = SimpleNamespace(yclid="yclid-1", metrica_client_id="client-1", started_at=created_at)
    profile = SimpleNamespace(yandex_oauth_token_encrypted="encrypted", yandex_counter_id="123")
    db = FakeDb(profile, [(conversion, visit)], conversion)
    factory = FakeFactory(db)

    assert await processor.process_system_conversions(factory) == 1
    assert conversion.status == "sent"
    assert db.commits == 2
    assert FakeMetrica.instances[0].uploads
    assert FakeMetrica.instances[0].closed


@pytest.mark.asyncio
async def test_send_error_returns_pending_and_does_not_leave_sending(monkeypatch):
    monkeypatch.setattr(settings, "yandex_conversion_dry_run", False)
    monkeypatch.setattr(processor, "decrypt_token", lambda value: "opaque-token")

    class FailingMetrica(FakeMetrica):
        async def upload_offline_conversion(self, *args):
            raise RuntimeError("isolated provider failure")

    monkeypatch.setattr(processor, "YandexMetricaClient", FailingMetrica)
    conversion = SimpleNamespace(id="conversion-2", status="pending", goal="bot_started", created_at=None)
    visit = SimpleNamespace(yclid="yclid-2", metrica_client_id="client-2", started_at=None)
    profile = SimpleNamespace(yandex_oauth_token_encrypted="encrypted", yandex_counter_id="123")
    db = FakeDb(profile, [(conversion, visit)], conversion)

    assert await processor.process_system_conversions(FakeFactory(db)) == 0
    assert conversion.status == "pending"
    assert db.commits == 2


@pytest.mark.asyncio
async def test_already_non_pending_row_is_skipped_idempotently(monkeypatch):
    monkeypatch.setattr(settings, "yandex_conversion_dry_run", False)
    monkeypatch.setattr(processor, "decrypt_token", lambda value: "opaque-token")
    monkeypatch.setattr(processor, "YandexMetricaClient", FakeMetrica)
    FakeMetrica.instances.clear()
    conversion = SimpleNamespace(id="conversion-3", status="sent", goal="bot_started", created_at=datetime.now(timezone.utc))
    visit = SimpleNamespace(yclid="yclid-3", metrica_client_id="client-3", started_at=datetime.now(timezone.utc))
    profile = SimpleNamespace(yandex_oauth_token_encrypted="encrypted", yandex_counter_id="123")
    db = FakeDb(profile, [(conversion, visit)], conversion)

    assert await processor.process_system_conversions(FakeFactory(db)) == 0
    assert conversion.status == "sent"
    assert FakeMetrica.instances[0].uploads == []
