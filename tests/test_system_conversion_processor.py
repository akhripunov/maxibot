from types import SimpleNamespace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core.config import settings
from app.traffic import system_conversion_processor as processor


ROOT = Path(__file__).resolve().parents[1]


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
        sql = str(statement)
        rows = self.rows
        if "IS NULL" in sql:
            rows = [
                row for row in self.rows
                if row[0].status == "pending"
                and (not row[1].yclid or not row[1].yclid.strip())
            ]
        elif "length(trim" in sql:
            rows = [
                row for row in self.rows
                if row[0].status == "pending"
                and row[1].yclid and row[1].yclid.strip()
                and row[1].metrica_client_id and row[1].metrica_client_id.strip()
            ]
        return SimpleNamespace(all=lambda: rows)

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
    conversion = SimpleNamespace(id="conversion-1", status="pending", internal_event="bot_started", created_at=created_at, attempt_count=0)
    visit = SimpleNamespace(yclid="yclid-1", metrica_client_id="client-1", started_at=created_at)
    profile = SimpleNamespace(yandex_oauth_token_encrypted="encrypted", yandex_counter_id="123")
    db = FakeDb(profile, [(conversion, visit)], conversion)
    factory = FakeFactory(db)

    assert await processor.process_system_conversions(factory) == 1
    assert conversion.status == "sent"
    assert conversion.attempt_count == 1
    assert conversion.last_result == "ACCEPTED"
    assert conversion.sent_at is not None
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
    conversion = SimpleNamespace(id="conversion-2", status="pending", internal_event="bot_started", created_at=datetime.now(timezone.utc), attempt_count=0)
    visit = SimpleNamespace(yclid="yclid-2", metrica_client_id="client-2", started_at=None)
    profile = SimpleNamespace(yandex_oauth_token_encrypted="encrypted", yandex_counter_id="123")
    db = FakeDb(profile, [(conversion, visit)], conversion)

    assert await processor.process_system_conversions(FakeFactory(db)) == 0
    assert conversion.status == "pending"
    assert conversion.attempt_count == 1
    assert conversion.last_result == "FAILED"
    assert conversion.last_error_class == "RuntimeError"
    assert db.commits == 2


@pytest.mark.asyncio
async def test_already_non_pending_row_is_skipped_idempotently(monkeypatch):
    monkeypatch.setattr(settings, "yandex_conversion_dry_run", False)
    monkeypatch.setattr(processor, "decrypt_token", lambda value: "opaque-token")
    monkeypatch.setattr(processor, "YandexMetricaClient", FakeMetrica)
    FakeMetrica.instances.clear()
    conversion = SimpleNamespace(id="conversion-3", status="sent", internal_event="bot_started", created_at=datetime.now(timezone.utc), attempt_count=1)
    visit = SimpleNamespace(yclid="yclid-3", metrica_client_id="client-3", started_at=datetime.now(timezone.utc))
    profile = SimpleNamespace(yandex_oauth_token_encrypted="encrypted", yandex_counter_id="123")
    db = FakeDb(profile, [(conversion, visit)], conversion)

    assert await processor.process_system_conversions(FakeFactory(db)) == 0
    assert conversion.status == "sent"
    assert FakeMetrica.instances == []


@pytest.mark.asyncio
async def test_missing_yclid_keeps_internal_conversion_unsent(monkeypatch):
    monkeypatch.setattr(settings, "yandex_conversion_dry_run", False)
    monkeypatch.setattr(processor, "decrypt_token", lambda value: "opaque-token")
    monkeypatch.setattr(processor, "YandexMetricaClient", FakeMetrica)
    FakeMetrica.instances.clear()
    conversion = SimpleNamespace(id="conversion-no-yclid", status="pending", internal_event="bot_started", created_at=datetime.now(timezone.utc), attempt_count=0)
    visit = SimpleNamespace(yclid=None, metrica_client_id="client-1", started_at=None)
    profile = SimpleNamespace(yandex_oauth_token_encrypted="encrypted", yandex_counter_id="123")
    db = FakeDb(profile, [(conversion, visit)], conversion)

    assert await processor.process_system_conversions(FakeFactory(db)) == 0
    assert conversion.status == "blocked"
    assert conversion.last_result == "BLOCKED_INELIGIBLE"
    assert conversion.last_error_class == "missing_yclid"
    assert FakeMetrica.instances == []


@pytest.mark.asyncio
async def test_eligible_row_beyond_old_batch_is_selected_before_limit(monkeypatch):
    """A row at logical position 63 must be visible to the eligible query."""
    monkeypatch.setattr(settings, "yandex_conversion_dry_run", False)
    monkeypatch.setattr(processor, "decrypt_token", lambda value: "opaque-token")
    monkeypatch.setattr(processor, "YandexMetricaClient", FakeMetrica)
    conversion = SimpleNamespace(id="eligible-63", status="pending", internal_event="bot_started", created_at=datetime.now(timezone.utc), attempt_count=0)
    visit = SimpleNamespace(yclid="yclid-63", metrica_client_id="client-63", started_at=datetime.now(timezone.utc))
    profile = SimpleNamespace(yandex_oauth_token_encrypted="encrypted", yandex_counter_id="123")
    db = FakeDb(profile, [(conversion, visit)], conversion)
    assert await processor.process_system_conversions(FakeFactory(db), limit=50) == 1
    assert conversion.status == "sent"


@pytest.mark.asyncio
async def test_ineligible_without_yclid_is_quarantined_and_does_not_block(monkeypatch):
    monkeypatch.setattr(settings, "yandex_conversion_dry_run", False)
    monkeypatch.setattr(processor, "decrypt_token", lambda value: "opaque-token")
    monkeypatch.setattr(processor, "YandexMetricaClient", FakeMetrica)
    conversion = SimpleNamespace(id="no-yclid", status="pending", internal_event="bot_started", created_at=datetime.now(timezone.utc), attempt_count=0)
    visit = SimpleNamespace(yclid=None, metrica_client_id="client", started_at=None)
    profile = SimpleNamespace(yandex_oauth_token_encrypted="encrypted", yandex_counter_id="123")
    db = FakeDb(profile, [(conversion, visit)], conversion)
    assert await processor.process_system_conversions(FakeFactory(db)) == 0
    assert conversion.status == "blocked"


def test_bot_processor_filters_before_limit_and_channel_is_not_imported():
    source = (ROOT / "app/traffic/system_conversion_processor.py").read_text(encoding="utf-8")
    channel = (ROOT / "app/bot/menu.py").read_text(encoding="utf-8")
    assert "BotTrafficVisit.yclid" in source
    assert "BotTrafficVisit.metrica_client_id" in source
    assert "OfflineConversion" not in source
    assert "BotConversion" not in channel[channel.index("async def _send_exact_conversion"):channel.index("def home_menu_banner_media")]


def test_processor_invariant_documented():
    doc = (ROOT / "docs/BOT_CHANNEL_METRIKA_PROCESSORS.md").read_text(encoding="utf-8")
    assert "BOT_AND_CHANNEL_CONVERSION_PROCESSORS_MUST_BE_OPERATIONALLY_SEPARATE=YES" in doc
