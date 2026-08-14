from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from redis.asyncio import Redis

from app.core.config import settings
from app.security_tokens import decrypt_token, encrypt_token

METRICA_SCOPES = ("metrika:read", "metrika:write")


def metrica_scope() -> str:
    """Return only scopes needed for Metrica counters/goals/conversions."""
    configured_scopes = set((settings.yandex_oauth_scope or "").split())
    return " ".join(scope for scope in METRICA_SCOPES if scope in configured_scopes)


def configured() -> bool:
    return bool(settings.yandex_oauth_client_id and settings.yandex_oauth_client_secret and settings.yandex_oauth_redirect_uri)


def authorize_url(state: str) -> str:
    return settings.yandex_oauth_authorize_url + "?" + urlencode({
        "response_type": "code",
        "client_id": settings.yandex_oauth_client_id,
        "redirect_uri": settings.yandex_oauth_redirect_uri,
        "scope": metrica_scope(),
        "state": state,
    })


def _redis() -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


async def create_state(admin_external_id: str, channel_id: str) -> str:
    state = secrets.token_urlsafe(32)
    client = _redis()
    try:
        await client.setex(f"yandex:oauth:state:{state}", 600, json.dumps({"admin": admin_external_id, "channel": channel_id}))
    finally:
        await client.aclose()
    return state


async def consume_state(state: str) -> dict | None:
    if not state:
        return None
    client = _redis()
    try:
        key = f"yandex:oauth:state:{state}"
        raw = await client.get(key)
        if raw is None:
            return None
        await client.delete(key)
        return json.loads(raw)
    finally:
        await client.aclose()


async def exchange_code(code: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(settings.yandex_oauth_token_url, data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": settings.yandex_oauth_client_id,
            "client_secret": settings.yandex_oauth_client_secret,
        })
        response.raise_for_status()
        payload = response.json()
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError("Yandex token response has no access token")
    return {"access_token": token, "token_type": payload.get("token_type", "bearer")}


async def save_token(admin_external_id: str, channel_id: str, token_payload: dict) -> None:
    client = _redis()
    try:
        value = {
            "token": encrypt_token(token_payload["access_token"]),
            "token_type": token_payload.get("token_type", "bearer"),
            "connected_at": datetime.now(timezone.utc).isoformat(),
        }
        await client.set(f"yandex:oauth:token:{admin_external_id}:{channel_id}", json.dumps(value))
    finally:
        await client.aclose()


async def get_encrypted_token(admin_external_id: str, channel_id: str) -> str | None:
    client = _redis()
    try:
        raw = await client.get(f"yandex:oauth:token:{admin_external_id}:{channel_id}")
        return json.loads(raw).get("token") if raw else None
    finally:
        await client.aclose()


async def connection_status(admin_external_id: str, channel_id: str) -> dict | None:
    client = _redis()
    try:
        raw = await client.get(f"yandex:oauth:token:{admin_external_id}:{channel_id}")
        if not raw:
            return None
        value = json.loads(raw)
        # Validate decryptability without returning the secret.
        decrypt_token(value["token"])
        return {"connected_at": value.get("connected_at")}
    except Exception:
        return None
    finally:
        await client.aclose()
