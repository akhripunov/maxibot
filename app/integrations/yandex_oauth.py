from __future__ import annotations

import json
import secrets
import hashlib
import hmac
import base64
import binascii
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


def _oauth_ticket_signature(body: bytes) -> str:
    key = hmac.new(settings.app_secret.encode(), b"MAXIBOT\x00OAUTH-INIT-TICKET\x00V1", hashlib.sha256).digest()
    return hmac.new(key, b"yandex-oauth-init\x00" + body, hashlib.sha256).hexdigest()


async def issue_oauth_ticket(admin_external_id: str, service: str, resource_id: str | None = None) -> str:
    """Issue a short-lived, single-use OAuth initiation ticket for an authenticated menu."""
    if service not in {"metrica", "direct", "system", "bot"}:
        raise ValueError("YANDEX_OAUTH_SERVICE_UNSUPPORTED")
    if (service in {"metrica", "direct", "bot"}) != bool(resource_id):
        raise ValueError("YANDEX_OAUTH_RESOURCE_INVALID")
    payload = {"admin": str(admin_external_id), "service": service, "resource": str(resource_id) if resource_id else None,
               "nonce": secrets.token_urlsafe(24), "exp": int(datetime.now(timezone.utc).timestamp()) + 600}
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(body).decode().rstrip("=")
    ticket = encoded + "." + _oauth_ticket_signature(body)
    client = _redis()
    try:
        reserved = await client.set(f"yandex:oauth:init:{payload['nonce']}", ticket, ex=600, nx=True)
        if not reserved:
            raise RuntimeError("YANDEX_OAUTH_TICKET_RESERVATION_FAILED")
    finally:
        await client.aclose()
    return ticket


async def consume_oauth_ticket(ticket: str, expected_service: str) -> dict | None:
    """Validate ticket claims and atomically reserve the nonce for one callback start."""
    if not ticket or len(ticket) > 2048:
        return None
    try:
        encoded, signature = ticket.split(".", 1)
        if len(signature) != 64 or any(ch not in "0123456789abcdef" for ch in signature):
            return None
        body = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        if not hmac.compare_digest(_oauth_ticket_signature(body), signature):
            return None
        payload = json.loads(body)
        if not isinstance(payload, dict):
            return None
        if payload.get("service") != expected_service or int(payload.get("exp", 0)) < int(datetime.now(timezone.utc).timestamp()):
            return None
        if not payload.get("admin") or not payload.get("nonce"):
            return None
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError, binascii.Error):
        return None
    client = _redis()
    try:
        reserved_ticket = await client.getdel(f"yandex:oauth:init:{payload['nonce']}")
        if not reserved_ticket or not hmac.compare_digest(reserved_ticket, ticket):
            return None
    finally:
        await client.aclose()
    return payload


async def create_state(admin_external_id: str, channel_id: str, *, service: str = "metrica") -> str:
    state = secrets.token_urlsafe(32)
    client = _redis()
    try:
        await client.setex(f"yandex:oauth:state:{state}", 600, json.dumps({"admin": admin_external_id, "channel": channel_id, "service": service}))
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
        raw = await client.getdel(key)
        if raw is None:
            return None
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
    return {
        "access_token": token,
        "token_type": payload.get("token_type", "bearer"),
        "scope": payload.get("scope"),
    }


def _client_fingerprint() -> str:
    return hashlib.sha256((settings.yandex_oauth_client_id or "").encode()).hexdigest()[:16]


async def save_token(admin_external_id: str, channel_id: str, token_payload: dict, *, db: AsyncSession, service: str = "metrica") -> str:
    """Persist a service-scoped encrypted token; Redis is a best-effort cache only."""
    if service not in {"metrica", "direct"}:
        raise ValueError("YANDEX_OAUTH_SERVICE_UNSUPPORTED")
    encrypted = encrypt_token(token_payload["access_token"])
    connected_at = datetime.now(timezone.utc)
    profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == channel_id))
    if not profile:
        raise ValueError("traffic profile unavailable for OAuth connection")
    if service == "direct":
        profile.yandex_direct_oauth_token_encrypted = encrypted
        profile.yandex_direct_oauth_connected_at = connected_at
        profile.yandex_direct_oauth_client_fingerprint = _client_fingerprint()
        profile.yandex_direct_oauth_scopes = str(token_payload.get("scope") or combined_scope())
    else:
        profile.yandex_metrica_oauth_token_encrypted = encrypted
        profile.yandex_metrica_oauth_connected_at = connected_at
        profile.yandex_metrica_oauth_client_fingerprint = _client_fingerprint()
        profile.yandex_metrica_oauth_scopes = str(token_payload.get("scope") or metrica_scope())
    value = {
        "token": encrypted,
        "token_type": token_payload.get("token_type", "bearer"),
        "connected_at": connected_at.isoformat(),
    }
    client = _redis()
    try:
        await client.set(f"yandex:oauth:{service}:token:{admin_external_id}:{channel_id}", json.dumps(value))
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
    """Backward-compatible Metrica credential accessor."""
    return await get_metrica_encrypted_token(admin_external_id, channel_id, db=db)


async def get_metrica_encrypted_token(admin_external_id: str, channel_id: str, *, db: AsyncSession | None = None) -> str | None:
    client = _redis()
    try:
        raw = await client.get(f"yandex:oauth:metrica:token:{admin_external_id}:{channel_id}")
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
    encrypted = (profile.yandex_metrica_oauth_token_encrypted or profile.yandex_oauth_token_encrypted) if profile else None
    if not encrypted:
        return None
    # Validate the encrypted value before returning it and rebuild the cache
    # only after a successful durable fallback.
    decrypt_token(encrypted)
    client = _redis()
    try:
        await client.set(
            f"yandex:oauth:metrica:token:{admin_external_id}:{channel_id}",
            json.dumps({"token": encrypted, "connected_at": profile.yandex_oauth_connected_at.isoformat() if profile.yandex_oauth_connected_at else None}),
        )
    except Exception:
        pass
    finally:
        await client.aclose()
    return encrypted


async def get_direct_encrypted_token(admin_external_id: str, channel_id: str, *, db: AsyncSession | None = None) -> str | None:
    """Return only the service-scoped Direct credential; never legacy fallback."""
    client = _redis()
    try:
        raw = await client.get(f"yandex:oauth:direct:token:{admin_external_id}:{channel_id}")
        if raw:
            return json.loads(raw).get("token")
    except Exception:
        pass
    finally:
        await client.aclose()
    if db is None:
        return None
    profile = await db.scalar(
        select(TrafficProfile).join(Channel, Channel.id == TrafficProfile.channel_id).join(Admin, Admin.id == Channel.admin_id).where(
            Channel.id == channel_id, Admin.external_id == admin_external_id,
        )
    )
    if not profile or not profile.yandex_direct_oauth_token_encrypted:
        return None
    encrypted = profile.yandex_direct_oauth_token_encrypted
    decrypt_token(encrypted)
    client = _redis()
    try:
        await client.set(f"yandex:oauth:direct:token:{admin_external_id}:{channel_id}", json.dumps({"token": encrypted, "connected_at": profile.yandex_direct_oauth_connected_at.isoformat() if profile.yandex_direct_oauth_connected_at else None}))
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
