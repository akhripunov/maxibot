from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Admin, Channel, TrafficProfile, SystemPromotionProfile, BotPromotionProfile
from app.security_tokens import decrypt_token, encrypt_token

METRICA_SCOPES = ("metrika:read", "metrika:write")
DIRECT_SCOPES = ("direct:api",)


def metrica_scope() -> str:
    """Return only scopes needed for Metrica counters/goals/conversions."""
    configured_scopes = set((settings.yandex_oauth_scope or "").split())
    return " ".join(scope for scope in METRICA_SCOPES if scope in configured_scopes)


def direct_scope() -> str:
    """Return only the Direct API permission requested by this integration."""
    configured_scopes = set((settings.yandex_oauth_scope or "").split())
    return " ".join(scope for scope in DIRECT_SCOPES if scope in configured_scopes)


def combined_scope() -> str:
    """Return the scopes needed for a combined Metrica + Direct connection."""
    return " ".join(scope for scope in (*METRICA_SCOPES, *DIRECT_SCOPES) if scope in (settings.yandex_oauth_scope or "").split())


def configured() -> bool:
    return bool(settings.yandex_oauth_client_id and settings.yandex_oauth_client_secret and settings.yandex_oauth_redirect_uri)


def authorize_url(state: str, *, scopes: str | None = None) -> str:
    # Yandex OAuth selects the first registered Web-service Redirect URI when
    # redirect_uri is omitted.  This avoids a provider-side mismatch caused by
    # an active app configuration/cache while keeping the callback configured
    # and validated by ``configured()``.
    return settings.yandex_oauth_authorize_url + "?" + urlencode({
        "response_type": "code",
        "client_id": settings.yandex_oauth_client_id,
        "scope": scopes if scopes is not None else metrica_scope(),
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


async def create_system_state(admin_external_id: str) -> str:
    state = secrets.token_urlsafe(32)
    client = _redis()
    try:
        await client.setex(f"yandex:oauth:state:{state}", 600, json.dumps({"admin": admin_external_id, "system": "maxibot"}))
    finally:
        await client.aclose()
    return state


async def create_bot_state(admin_external_id: str, bot_id: str) -> str:
    state = secrets.token_urlsafe(32)
    client = _redis()
    try:
        await client.setex(f"yandex:oauth:state:{state}", 600, json.dumps({"admin": admin_external_id, "bot": bot_id}))
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


async def save_token(admin_external_id: str, channel_id: str, token_payload: dict, *, db: AsyncSession) -> str:
    """Persist an encrypted token durably; Redis is a best-effort cache only."""
    encrypted = encrypt_token(token_payload["access_token"])
    connected_at = datetime.now(timezone.utc)
    profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == channel_id))
    if not profile:
        raise ValueError("traffic profile unavailable for OAuth connection")
    profile.yandex_oauth_token_encrypted = encrypted
    profile.yandex_oauth_connected_at = connected_at
    value = {
        "token": encrypted,
        "token_type": token_payload.get("token_type", "bearer"),
        "connected_at": connected_at.isoformat(),
    }
    client = _redis()
    try:
        await client.set(f"yandex:oauth:token:{admin_external_id}:{channel_id}", json.dumps(value))
    except Exception:
        # A cache outage must not discard a successfully persisted connection.
        pass
    finally:
        await client.aclose()
    return encrypted


async def save_system_token(admin_external_id: str, token_payload: dict, *, db: AsyncSession) -> str:
    encrypted = encrypt_token(token_payload["access_token"])
    profile = await db.scalar(select(BotPromotionProfile).where(
        BotPromotionProfile.bot_key == "maxibot",
        BotPromotionProfile.bot_type == "SYSTEM",
    ))
    if not profile:
        raise ValueError("canonical system BOT promotion profile unavailable")
    profile.yandex_oauth_token_encrypted = encrypted
    profile.yandex_oauth_connected_at = datetime.now(timezone.utc)
    return encrypted


async def save_bot_token(admin_external_id: str, bot_id: str, token_payload: dict, *, db: AsyncSession) -> str:
    encrypted = encrypt_token(token_payload["access_token"])
    profile = await db.scalar(select(BotPromotionProfile).join(
        Admin, Admin.id == BotPromotionProfile.owner_admin_id,
    ).where(
        BotPromotionProfile.bot_connection_id == bot_id,
        Admin.external_id == admin_external_id,
    ))
    if not profile:
        raise ValueError("customer BOT promotion profile unavailable")
    profile.yandex_oauth_token_encrypted = encrypted
    profile.yandex_oauth_connected_at = datetime.now(timezone.utc)
    return encrypted


async def system_connection_status(*, db: AsyncSession) -> dict | None:
    profile = await db.scalar(select(BotPromotionProfile).where(
        BotPromotionProfile.bot_key == "maxibot",
        BotPromotionProfile.bot_type == "SYSTEM",
    ))
    if not profile or not profile.yandex_oauth_token_encrypted:
        return None
    try:
        decrypt_token(profile.yandex_oauth_token_encrypted)
    except Exception:
        return None
    return {"connected_at": profile.yandex_oauth_connected_at.isoformat() if profile.yandex_oauth_connected_at else None}


async def get_encrypted_token(admin_external_id: str, channel_id: str, *, db: AsyncSession | None = None) -> str | None:
    client = _redis()
    try:
        raw = await client.get(f"yandex:oauth:token:{admin_external_id}:{channel_id}")
        if raw:
            return json.loads(raw).get("token")
    except Exception:
        pass
    finally:
        await client.aclose()
    if db is None:
        return None
    profile = await db.scalar(
        select(TrafficProfile)
        .join(Channel, Channel.id == TrafficProfile.channel_id)
        .join(Admin, Admin.id == Channel.admin_id)
        .where(Channel.id == channel_id, Admin.external_id == admin_external_id)
    )
    if not profile or not profile.yandex_oauth_token_encrypted:
        return None
    encrypted = profile.yandex_oauth_token_encrypted
    # Validate the encrypted value before returning it and rebuild the cache
    # only after a successful durable fallback.
    decrypt_token(encrypted)
    client = _redis()
    try:
        await client.set(
            f"yandex:oauth:token:{admin_external_id}:{channel_id}",
            json.dumps({"token": encrypted, "connected_at": profile.yandex_oauth_connected_at.isoformat() if profile.yandex_oauth_connected_at else None}),
        )
    except Exception:
        pass
    finally:
        await client.aclose()
    return encrypted


async def connection_status(admin_external_id: str, channel_id: str, *, db: AsyncSession | None = None) -> dict | None:
    try:
        encrypted = await get_encrypted_token(admin_external_id, channel_id, db=db)
        if not encrypted:
            return None
        decrypt_token(encrypted)
        profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == channel_id)) if db else None
        return {"connected_at": profile.yandex_oauth_connected_at.isoformat() if profile and profile.yandex_oauth_connected_at else None}
    except Exception:
        return None
