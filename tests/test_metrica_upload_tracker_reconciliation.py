from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import (
    Admin,
    Channel,
    LandingPage,
    OfflineConversion,
    TrafficMembership,
    TrafficProfile,
    TrafficVisit,
)
from app.traffic import metrica_upload_tracker as tracker


class FakeMetricaClient:
    def __init__(self, response=None, error=None, before_get=None):
        self.response = response or {"uploading": {"status": "PROCESSED"}}
        self.error = error
        self.before_get = before_get
        self.get_calls = []
        self.closed = False

    async def get_offline_conversion_upload(self, counter_id, upload_id):
        self.get_calls.append((counter_id, upload_id))
        if self.before_get:
            await self.before_get()
        if self.error:
            raise self.error
        return self.response

    async def close(self):
        self.closed = True


@pytest.fixture
async def database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def add_upload(sessions, *, status="linkage_failure", counter_id="100", age_days=1, goal="userDidUnsubscribe"):
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        admin = Admin(external_id="admin")
        db.add(admin)
        await db.flush()
        channel = Channel(admin_id=admin.id, external_id="channel", title="Channel", public_slug="slug")
        db.add(channel)
        await db.flush()
        landing = LandingPage(channel_id=channel.id, public_id="public", title="Channel", description="")
        db.add(landing)
        await db.flush()
        visit = TrafficVisit(visit_token="visit-token", landing_page_id=landing.id, channel_id=channel.id)
        db.add(visit)
        await db.flush()
        membership = TrafficMembership(channel_id=channel.id, max_user_id="max-user", attribution_status="EXACT")
        profile = TrafficProfile(channel_id=channel.id, yandex_counter_id="999")
        db.add_all([membership, profile])
        await db.flush()
        conversion = OfflineConversion(
            visit_id=visit.id,
            membership_id=membership.id,
            target="YANDEX",
            goal=goal,
            status=status,
            last_error="metrica_linkage_failure" if status == "linkage_failure" else None,
            metrica_upload_id="upload-1",
            metrica_upload_counter_id=counter_id,
            metrica_upload_status="LINKAGE_FAILURE" if status == "linkage_failure" else "UPLOADED",
            metrica_upload_checked_at=now - timedelta(hours=1),
            metrica_upload_next_check_at=now - timedelta(minutes=1),
            metrica_upload_observation_started_at=now - timedelta(days=age_days),
            metrica_upload_check_attempts=1,
            created_at=now - timedelta(days=age_days),
        )
        db.add(conversion)
        await db.commit()
        return conversion.id


def install_client(monkeypatch, client):
    async def get_token(*args, **kwargs):
        return "encrypted-token"

    monkeypatch.setattr(tracker, "get_encrypted_token", get_token)
    monkeypatch.setattr(tracker, "decrypt_token", lambda _value: "token")
    monkeypatch.setattr(tracker, "YandexMetricaClient", lambda _token: client)


async def read_conversion(sessions, conversion_id):
    async with sessions() as db:
        return await db.get(OfflineConversion, conversion_id)


def as_utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@pytest.mark.asyncio
async def test_linkage_failure_can_be_reconciled_to_processed_without_resending(monkeypatch, database):
    conversion_id = await add_upload(database)
    client = FakeMetricaClient()
    install_client(monkeypatch, client)

    assert await tracker.refresh_metrica_upload_statuses(database) == 1

    conversion = await read_conversion(database, conversion_id)
    assert conversion.status == "sent"
    assert conversion.metrica_upload_status == "PROCESSED"
    assert conversion.last_error is None
    assert conversion.metrica_upload_check_error is None
    assert conversion.metrica_upload_next_check_at is None
    assert conversion.metrica_upload_recovered_at is not None
    assert conversion.sent_at is not None
    assert client.get_calls == [(100, "upload-1")]
    assert not hasattr(client, "upload_offline_conversion")
    assert client.closed

    # A second tracker tick cannot create or upload another conversion.
    assert await tracker.refresh_metrica_upload_statuses(database) == 0
    assert client.get_calls == [(100, "upload-1")]
    async with database() as db:
        assert await db.scalar(select(func.count(OfflineConversion.id))) == 1


@pytest.mark.asyncio
async def test_uploaded_subscribe_reconciliation_keeps_goal_and_counter_snapshot(monkeypatch, database):
    conversion_id = await add_upload(database, status="uploaded", counter_id="321", goal="userDidSubscribe")
    client = FakeMetricaClient()
    install_client(monkeypatch, client)

    assert await tracker.refresh_metrica_upload_statuses(database) == 1

    conversion = await read_conversion(database, conversion_id)
    assert conversion.status == "sent"
    assert conversion.goal == "userDidSubscribe"
    assert client.get_calls == [(321, "upload-1")]  # never the mutable current profile counter


@pytest.mark.asyncio
async def test_linkage_failure_is_backed_off_and_observation_is_bounded(monkeypatch, database):
    conversion_id = await add_upload(database)
    client = FakeMetricaClient(response={"uploading": {"status": "LINKAGE_FAILURE"}})
    install_client(monkeypatch, client)

    assert await tracker.refresh_metrica_upload_statuses(database) == 1
    conversion = await read_conversion(database, conversion_id)
    assert conversion.status == "linkage_failure"
    assert conversion.last_error == "metrica_linkage_failure"
    assert conversion.metrica_upload_check_attempts == 2
    assert as_utc(conversion.metrica_upload_next_check_at) > datetime.now(timezone.utc)
    assert await tracker.refresh_metrica_upload_statuses(database) == 0
    assert len(client.get_calls) == 1

    async with database() as db:
        await db.execute(update(OfflineConversion).where(OfflineConversion.id == conversion_id).values(
            metrica_upload_observation_started_at=datetime.now(timezone.utc) - timedelta(days=31),
            metrica_upload_next_check_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ))
        await db.commit()
    assert await tracker.refresh_metrica_upload_statuses(database) == 0
    conversion = await read_conversion(database, conversion_id)
    assert conversion.metrica_upload_check_error == "OBSERVATION_WINDOW_ENDED"
    assert conversion.metrica_upload_next_check_at is None
    assert len(client.get_calls) == 1


@pytest.mark.asyncio
async def test_unknown_provider_state_does_not_mark_conversion_success(monkeypatch, database):
    conversion_id = await add_upload(database)
    client = FakeMetricaClient(response={"uploading": {"status": "FUTURE_STATE"}})
    install_client(monkeypatch, client)

    assert await tracker.refresh_metrica_upload_statuses(database) == 1
    conversion = await read_conversion(database, conversion_id)
    assert conversion.status == "linkage_failure"
    assert conversion.metrica_upload_status == "FUTURE_STATE"
    assert conversion.metrica_upload_check_error == "UNKNOWN_PROVIDER_STATUS"
    assert conversion.sent_at is None
    assert await tracker.refresh_metrica_upload_statuses(database) == 0
    assert len(client.get_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("counter_id", "expected_error"),
    [(None, "ORIGINAL_COUNTER_UNKNOWN"), ("not-a-counter", "ORIGINAL_COUNTER_INVALID")],
)
async def test_unverified_counter_is_not_replaced_by_current_profile(monkeypatch, database, counter_id, expected_error):
    conversion_id = await add_upload(database, counter_id=counter_id)
    client = FakeMetricaClient()
    install_client(monkeypatch, client)

    assert await tracker.refresh_metrica_upload_statuses(database) == 0
    conversion = await read_conversion(database, conversion_id)
    assert conversion.status == "linkage_failure"
    assert conversion.metrica_upload_check_error == expected_error
    assert client.get_calls == []


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (httpx.HTTPStatusError("bad request", request=httpx.Request("GET", "https://example.test"), response=httpx.Response(400)), "HTTP_400"),
        (httpx.HTTPStatusError("unauthorized", request=httpx.Request("GET", "https://example.test"), response=httpx.Response(401)), "HTTP_401"),
        (httpx.ReadTimeout("timeout"), "TIMEOUT"),
        (httpx.ConnectError("unavailable"), "NETWORK_ERROR"),
    ],
)
@pytest.mark.asyncio
async def test_provider_errors_are_categorized_and_backed_off(monkeypatch, database, error, category):
    conversion_id = await add_upload(database)
    client = FakeMetricaClient(error=error)
    install_client(monkeypatch, client)

    assert await tracker.refresh_metrica_upload_statuses(database) == 0
    conversion = await read_conversion(database, conversion_id)
    assert conversion.status == "linkage_failure"
    assert conversion.metrica_upload_check_error == category
    assert as_utc(conversion.metrica_upload_next_check_at) > datetime.now(timezone.utc)
    assert client.get_calls == [(100, "upload-1")]
    assert client.closed


@pytest.mark.asyncio
async def test_concurrent_status_change_is_not_overwritten(monkeypatch, database):
    conversion_id = await add_upload(database)

    async def concurrent_success():
        async with database() as db:
            await db.execute(update(OfflineConversion).where(OfflineConversion.id == conversion_id).values(
                status="sent", metrica_upload_status="PROCESSED", last_error=None,
            ))
            await db.commit()

    client = FakeMetricaClient(response={"uploading": {"status": "LINKAGE_FAILURE"}}, before_get=concurrent_success)
    install_client(monkeypatch, client)

    assert await tracker.refresh_metrica_upload_statuses(database) == 0
    conversion = await read_conversion(database, conversion_id)
    assert conversion.status == "sent"
    assert conversion.metrica_upload_status == "PROCESSED"
