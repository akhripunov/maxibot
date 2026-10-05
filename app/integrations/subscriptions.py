from __future__ import annotations

import calendar
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import settings


def add_subscription_months(
    current_expires_at: datetime | None,
    months: int,
    *,
    now: datetime | None = None,
) -> datetime:
    """Extend a subscription from its live expiry, or start it now.

    Calendar months are used instead of a fixed 30-day approximation. A paid
    period bought before expiry extends the existing period; an expired period
    starts from the payment time.
    """
    if months <= 0:
        raise ValueError("months must be positive")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    base = current
    if current_expires_at is not None:
        expiry = current_expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry > current:
            base = expiry
    month_index = base.month - 1 + months
    year = base.year + month_index // 12
    month = month_index % 12 + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return base.replace(year=year, month=month, day=day)


def subscription_status(expires_at: datetime | None, *, now: datetime | None = None) -> str:
    if not expires_at:
        return "не активирован"
    current = now or datetime.now(timezone.utc)
    expiry = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
    return "активен" if expiry > current else "истёк"


def free_trial_status(trial_end_at: datetime | None, *, now: datetime | None = None) -> str:
    if not trial_end_at:
        return "не использован"
    current = now or datetime.now(timezone.utc)
    expiry = trial_end_at if trial_end_at.tzinfo else trial_end_at.replace(tzinfo=timezone.utc)
    return "активен" if expiry > current else "истёк"


def service_entitlement_active(
    paid_expires_at: datetime | None,
    trial_end_at: datetime | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Service-level access is granted by either paid subscription or trial."""
    current = now or datetime.now(timezone.utc)
    return (
        subscription_status(paid_expires_at, now=current) == "активен"
        or free_trial_status(trial_end_at, now=current) == "активен"
    )


def channel_promotion_entitlement_source(
    paid_expires_at: datetime | None,
    trial_end_at: datetime | None,
    trial_channel_id: str | None,
    requested_channel_id: str | None,
    *,
    now: datetime | None = None,
) -> str:
    """Return PAID/TRIAL/NONE for one channel's promotion access."""
    current = now or datetime.now(timezone.utc)
    if subscription_status(paid_expires_at, now=current) == "активен":
        return "PAID"
    if (
        trial_channel_id
        and requested_channel_id
        and str(trial_channel_id) == str(requested_channel_id)
        and free_trial_status(trial_end_at, now=current) == "активен"
    ):
        return "TRIAL"
    return "NONE"


async def channel_promotion_entitlement(db, admin_external_id: str, channel_id: str) -> str:
    """Canonical owner/channel gate for paid promotion functionality."""
    from app.db.models import Admin, Channel, FreeTrial

    admin = await db.scalar(select(Admin).where(Admin.external_id == str(admin_external_id)))
    if not admin:
        return "NONE"
    channel = await db.scalar(
        select(Channel).where(Channel.id == str(channel_id), Channel.admin_id == admin.id)
    )
    if not channel:
        return "NONE"
    configured_owner_id = (settings.maxibot_owner_user_id or "").strip()
    if configured_owner_id and str(admin.external_id) == configured_owner_id:
        return "OWNER"
    trial = await db.scalar(select(FreeTrial).where(FreeTrial.admin_id == admin.id))
    return channel_promotion_entitlement_source(
        admin.subscription_expires_at,
        trial.trial_end_at if trial else None,
        trial.activated_channel_id if trial else None,
        channel_id,
    )
