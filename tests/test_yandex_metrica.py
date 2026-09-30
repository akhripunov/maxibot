import json
from datetime import datetime, timezone

import httpx
import pytest

from app.integrations.yandex_metrica import YandexMetricaClient
from app.traffic.metrica_sender import send_max_subscriber, send_user_subscribed
from app.core.config import settings


@pytest.mark.asyncio
async def test_metrica_counter_and_goals_are_mockable_and_idempotent():
    calls = []

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/counters") and request.method == "GET":
            return httpx.Response(200, json={"counters": [{"id": 101, "name": "MAXIBOT"}]})
        if request.url.path.endswith("/goals") and request.method == "GET":
            return httpx.Response(200, json={"goals": [
                {"id": 201, "name": "toChannel"},
                {"id": 202, "name": "userDidOpenMax"},
                {"id": 203, "name": "userDidSubscribe"},
                {"id": 204, "name": "userDidUnsubscribe"},
            ]})
        raise AssertionError("duplicate create call")

    client = YandexMetricaClient("opaque", "https://metrics.test")
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="https://metrics.test", transport=httpx.MockTransport(handler))
    assert (await client.list_counters())[0]["id"] == 101
    goals = await client.list_goals(101)
    assert {goal["name"] for goal in goals} == {"toChannel", "userDidOpenMax", "userDidSubscribe", "userDidUnsubscribe"}
    await client.close()
    assert all("opaque" not in str(call) for call in calls)


def test_existing_counter_goal_backfill_is_explicit_and_does_not_create_counter():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app/integrations/yandex_metrica.py").read_text()
    start = source.index("async def ensure_channel_goals_for_existing_counter")
    end = source.index("async def ensure_system_setup", start)
    helper = source[start:end]
    assert "counter_id: int" in helper
    assert "list_goals" in helper
    assert "create_action_goal" in helper
    assert "create_counter" not in helper
    assert '"userDidUnsubscribe"' in helper


@pytest.mark.asyncio
async def test_create_counter_sends_domain_without_scheme():
    def handler(request: httpx.Request):
        assert request.method == "POST"
        assert request.url.path == "/management/v1/counters"
        assert json.loads(request.content)["counter"]["site"] == "go.maxibot.pro"
        return httpx.Response(200, json={"counter": {"id": 303}})

    client = YandexMetricaClient("opaque", "https://metrics.test")
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="https://metrics.test", transport=httpx.MockTransport(handler))
    assert await client.create_counter("MAXIBOT", "https://go.maxibot.pro") == 303
    await client.close()


@pytest.mark.asyncio
async def test_create_action_goal_sends_event_condition():
    def handler(request: httpx.Request):
        body = json.loads(request.content)
        assert body["goal"] == {
            "name": "toChannel",
            "type": "action",
            "conditions": [{"type": "exact", "url": "toChannel"}],
        }
        return httpx.Response(200, json={"goal": {"id": 404}})

    client = YandexMetricaClient("opaque", "https://metrics.test")
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="https://metrics.test", transport=httpx.MockTransport(handler))
    assert await client.create_action_goal(303, "toChannel") == 404
    await client.close()


@pytest.mark.asyncio
async def test_upload_offline_conversion_uses_goal_identifier_in_csv():
    def handler(request: httpx.Request):
        assert request.method == "POST"
        assert b"ClientId,Yclid,DateTime,Target" in request.content
        assert b"123456,yclid-A,1700000000,userDidSubscribe" in request.content
        assert b"userDidSubscribe" in request.content
        assert b"max_subscriber" not in request.content
        return httpx.Response(200, json={"id": 1, "status": "UPLOADED"})

    client = YandexMetricaClient("opaque", "https://metrics.test")
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="https://metrics.test", transport=httpx.MockTransport(handler))
    result = await client.upload_offline_conversion(303, "yclid-A", "123456", "userDidSubscribe", "1700000000")
    assert result["status"] == "UPLOADED"
    await client.close()


@pytest.mark.asyncio
async def test_upload_error_logs_safe_status_body_headers_and_hash(caplog):
    def handler(request: httpx.Request):
        return httpx.Response(400, headers={"X-Request-ID": "req-test"}, text='{"error":"bad csv"}')

    client = YandexMetricaClient("opaque", "https://metrics.test")
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="https://metrics.test", transport=httpx.MockTransport(handler))
    with caplog.at_level("ERROR"):
        with pytest.raises(httpx.HTTPStatusError):
            await client.upload_offline_conversion(303, "yclid-secret", "123456", 909, "1700000000")
    message = " ".join(record.getMessage() for record in caplog.records)
    assert "400" in str(caplog.records[0].http_status)
    assert caplog.records[0].response_body == '{"error":"bad csv"}'
    assert caplog.records[0].response_headers == {"x-request-id": "req-test"}
    assert "yclid-secret" not in message
    await client.close()


@pytest.mark.asyncio
async def test_exact_membership_sender_targets_user_did_subscribe(monkeypatch):
    class Client:
        def __init__(self):
            self.goal_names = []

        async def ensure_action_goal_identifier(self, _counter_id, goal_name):
            self.goal_names.append(goal_name)
            return goal_name

        async def upload_offline_conversion(self, _counter_id, _yclid, _client_id, goal_identifier, _timestamp):
            assert goal_identifier == "userDidSubscribe"
            return {"status": "UPLOADED"}

    monkeypatch.setattr(settings, "yandex_conversion_dry_run", False)
    client = Client()
    result = await send_user_subscribed(
        client, counter_id=303, yclid="yclid-A", client_id="123456", conversion_datetime="1700000000", verified_membership=True,
        visit_at=datetime.now(timezone.utc),
    )
    alias_result = await send_max_subscriber(
        client, counter_id=303, yclid="yclid-B", client_id="123457", conversion_datetime="1700000001", exact_membership=True,
        visit_at=datetime.now(timezone.utc),
    )
    assert result.status == alias_result.status == "sent"
    assert client.goal_names == ["userDidSubscribe", "userDidSubscribe"]


@pytest.mark.asyncio
async def test_get_counter_reads_non_secret_owner_login():
    def handler(request: httpx.Request):
        assert request.method == "GET"
        assert request.url.path.endswith("/counter/303")
        return httpx.Response(200, json={"counter": {"id": 303, "owner_login": "owner-login"}})

    client = YandexMetricaClient("opaque", "https://metrics.test")
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="https://metrics.test", transport=httpx.MockTransport(handler))
    assert (await client.get_counter(303))["owner_login"] == "owner-login"
    await client.close()
