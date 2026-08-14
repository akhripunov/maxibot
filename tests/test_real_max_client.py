import httpx
import pytest

from app.integrations.max_client import MaxMessage, RealMaxClient


@pytest.mark.asyncio
async def test_real_client_uses_documented_send_messages_path_and_auth_header():
    seen = {}

    def handler(request: httpx.Request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"message": {"body": {"mid": "m-1"}}})

    client = RealMaxClient(token="secret-not-printed", base_url="https://platform-api2.max.ru")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=client.base_url, headers=client._client.headers)
    try:
        result = await client.send_message("user-1", "hello", [[{"text": "Open", "type": "callback", "payload": "home"}]])
    finally:
        await client.close()
    assert result == "m-1"
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/messages?user_id=user-1")
    assert seen["authorization"] == "secret-not-printed"


@pytest.mark.asyncio
async def test_real_client_uses_put_for_message_edit_and_preserves_message_id():
    seen = {}

    def handler(request: httpx.Request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        return httpx.Response(200, json={})

    client = RealMaxClient(token="token", base_url="https://platform-api2.max.ru")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=client.base_url, headers={"Authorization": "token"})
    try:
        result = await client.edit_message("admin", "m-2", "updated", [])
    finally:
        await client.close()
    assert result == "m-2"
    assert seen == {"method": "PUT", "url": "https://platform-api2.max.ru/messages?message_id=m-2"}


@pytest.mark.asyncio
async def test_real_client_checks_one_member_with_user_ids_filter():
    seen = {}

    def handler(request: httpx.Request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"members": [{"user_id": 42, "join_time": 123}]})

    client = RealMaxClient(token="token", base_url="https://platform-api2.max.ru")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=client.base_url, headers={"Authorization": "token"})
    try:
        member = await client.get_member("channel-1", "42")
    finally:
        await client.close()
    assert member["user_id"] == 42
    assert seen["method"] == "GET"
    assert seen["url"].startswith("https://platform-api2.max.ru/chats/channel-1/members?")
    assert "user_ids=42" in seen["url"]


@pytest.mark.asyncio
async def test_callback_message_replacement_sends_only_message_envelope():
    seen = {}

    def handler(request: httpx.Request):
        seen["json"] = request.read().decode()
        return httpx.Response(200, json={"success": True})

    client = RealMaxClient(token="token", base_url="https://platform-api2.max.ru")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=client.base_url, headers={"Authorization": "token"})
    try:
        await client.answer_callback("callback-1", {"text": "Мои каналы", "attachments": []})
    finally:
        await client.close()
    assert '"message"' in seen["json"]
    assert '"notification"' not in seen["json"]


@pytest.mark.asyncio
async def test_callback_ack_without_message_uses_valid_notification():
    seen = {}

    def handler(request: httpx.Request):
        seen["json"] = request.read().decode()
        return httpx.Response(200, json={"success": True})

    client = RealMaxClient(token="token", base_url="https://platform-api2.max.ru")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=client.base_url, headers={"Authorization": "token"})
    try:
        await client.answer_callback("callback-1")
    finally:
        await client.close()
    assert '"notification":"Готово"' in seen["json"]


@pytest.mark.asyncio
async def test_real_client_parses_message_created_update_without_logging_token():
    client = RealMaxClient(token="token", base_url="https://platform-api2.max.ru")
    update = client._parse_update({
        "update_type": "message_created",
        "user": {"user_id": 42, "name": "Анна"},
        "message": {"mid": "m-3", "sender": {"user_id": 42, "name": "Анна"}, "body": {"text": "Вопрос"}},
    })
    await client.close()
    assert update.update_type == "message_created"
    assert update.user_id == "42"
    assert update.message.text == "Вопрос"
    assert "token" not in repr(update).lower()
