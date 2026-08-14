import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.core.config import settings
from app.integrations import yandex_oauth


def test_authorize_url_contains_minimal_scope_and_no_secret(monkeypatch):
    monkeypatch.setattr(settings, "yandex_oauth_client_id", "client-123")
    monkeypatch.setattr(settings, "yandex_oauth_client_secret", "secret-value")
    monkeypatch.setattr(settings, "yandex_oauth_redirect_uri", "https://maxibot.jobjin.ru/oauth/yandex/callback")
    monkeypatch.setattr(settings, "yandex_oauth_scope", "metrika:read metrika:write")
    query = parse_qs(urlsplit(yandex_oauth.authorize_url("state-abc")).query)
    assert query["client_id"] == ["client-123"]
    assert query["redirect_uri"] == ["https://maxibot.jobjin.ru/oauth/yandex/callback"]
    assert query["scope"] == ["metrika:read metrika:write"]
    assert "direct:api" not in query["scope"][0]
    assert query["state"] == ["state-abc"]
    assert "secret-value" not in json.dumps(query)


@pytest.mark.asyncio
async def test_exchange_code_uses_mock_http_and_returns_token(monkeypatch):
    monkeypatch.setattr(settings, "yandex_oauth_client_id", "client-123")
    monkeypatch.setattr(settings, "yandex_oauth_client_secret", "secret-value")

    def handler(request: httpx.Request):
        assert request.url.path == "/token"
        assert request.content.decode().find("code=one-time-code") >= 0
        return httpx.Response(200, json={"access_token": "opaque-token", "token_type": "bearer"})

    original = httpx.AsyncClient

    class MockClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockClient)
    result = await yandex_oauth.exchange_code("one-time-code")
    assert result["access_token"] == "opaque-token"
    monkeypatch.setattr(httpx, "AsyncClient", original)


def test_configured_requires_all_oauth_credentials(monkeypatch):
    monkeypatch.setattr(settings, "yandex_oauth_client_id", "client")
    monkeypatch.setattr(settings, "yandex_oauth_client_secret", None)
    monkeypatch.setattr(settings, "yandex_oauth_redirect_uri", "https://example.test/callback")
    assert yandex_oauth.configured() is False
