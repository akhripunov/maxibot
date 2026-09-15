"""Durable encrypted Telegram Ads service-identity lifecycle."""
from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ads.providers.telegram import decrypt_session_cookies, encrypt_session_cookies
from app.db.models import AdsAccount, TelegramAdsServiceIdentity


async def save_service_identity(db: AsyncSession, *, identity_key: str, cookies: dict[str, str]) -> TelegramAdsServiceIdentity:
    """Create or rotate one shared encrypted service session."""
    row = await db.scalar(select(TelegramAdsServiceIdentity).where(TelegramAdsServiceIdentity.identity_key == identity_key))
    now = datetime.now(timezone.utc)
    encrypted = encrypt_session_cookies(cookies)
    if row is None:
        row = TelegramAdsServiceIdentity(identity_key=identity_key, encrypted_session=encrypted, status="CONNECTED", session_updated_at=now)
        db.add(row)
    else:
        row.encrypted_session = encrypted
        row.status = "CONNECTED"
        row.session_updated_at = now
    await db.flush()
    return row


async def load_service_identity(db: AsyncSession, *, identity_id: str) -> dict[str, str] | None:
    row = await db.get(TelegramAdsServiceIdentity, identity_id)
    if not row or row.status in {"REAUTH_REQUIRED", "ACCESS_REVOKED"}:
        return None
    try:
        return decrypt_session_cookies(row.encrypted_session)
    except Exception:
        row.status = "REAUTH_REQUIRED"
        return None


async def mark_reauth_required(db: AsyncSession, *, identity_id: str) -> None:
    row = await db.get(TelegramAdsServiceIdentity, identity_id)
    if row:
        row.status = "REAUTH_REQUIRED"
        await db.commit()


async def bind_account(db: AsyncSession, *, account: AdsAccount, identity_id: str, external_account_id: str, display_name: str | None = None) -> None:
    """Bind by exact opaque external ID; display name is never the key."""
    identity = await db.get(TelegramAdsServiceIdentity, identity_id)
    if not identity:
        raise ValueError("TELEGRAM_ADS_SERVICE_IDENTITY_NOT_FOUND")
    account.provider = "TELEGRAM_ADS"
    account.telegram_service_identity_id = identity.id
    account.external_account_id = str(external_account_id)
    account.display_name = display_name or account.display_name
    account.status = "CONNECTED"
    await db.flush()
