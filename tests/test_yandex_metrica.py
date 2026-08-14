import json

import httpx
import pytest

from app.integrations.yandex_metrica import YandexMetricaClient


@pytest.mark.asyncio
async def test_metrica_counter_and_goals_are_mockable_and_idempotent():
    calls = []

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/counters") and request.method == "GET":
            return httpx.Response(200, json={"counters": [{"id": 101, "name": "MAXIBOT"}]})
        if request.url.path.endswith("/goals") and request.method == "GET":
            return httpx.Response(200, json={"goals": [{"id": 201, "name": "toChannel"}, {"id": 202, "name": "userDidSubscribe"}]})
        raise AssertionError("duplicate create call")

    client = YandexMetricaClient("opaque", "https://metrics.test")
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="https://metrics.test", transport=httpx.MockTransport(handler))
    assert (await client.list_counters())[0]["id"] == 101
    goals = await client.list_goals(101)
    assert {goal["name"] for goal in goals} == {"toChannel", "userDidSubscribe"}
    await client.close()
    assert all("opaque" not in str(call) for call in calls)


@pytest.mark.asyncio
async def test_create_counter_sends_domain_without_scheme():
    def handler(request: httpx.Request):
        assert request.method == "POST"
        assert request.url.path == "/management/v1/counters"
        assert json.loads(request.content)["counter"]["site"] == "go.maxibot.jobjin.ru"
        return httpx.Response(200, json={"counter": {"id": 303}})

    client = YandexMetricaClient("opaque", "https://metrics.test")
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="https://metrics.test", transport=httpx.MockTransport(handler))
    assert await client.create_counter("MAXIBOT", "https://go.maxibot.jobjin.ru") == 303
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
