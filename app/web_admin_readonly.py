"""Read-only platform-admin business overview API.

This module deliberately exposes aggregates and internal opaque ids only. It
does not expose MAX/Yandex credentials, tokens, raw attribution identities, or
mutation endpoints.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    Admin,
    AdsAiRun,
    AdsMemory,
    AdsSnapshot,
    BotConnection,
    Channel,
    Conversation,
    SubscriptionOrder,
    TrafficProfile,
)
from app.db.session import get_session
from app.web_admin_auth import require_web_admin

router = APIRouter(prefix="/admin", tags=["web-admin"])


def canonical_metric_definitions() -> dict[str, dict[str, object]]:
    return {
        "total_clients": {"definition": "count(Admin)", "status": "PROVEN"},
        "paying_clients": {"definition": "distinct Admin with a paid SubscriptionOrder", "status": "PROVEN"},
        "active_subscriptions": {"definition": "Admin.subscription_expires_at > server_now", "status": "PROVEN"},
        "new_clients": {"definition": "Admin.created_at inside requested UTC window", "status": "PROVEN"},
        "new_paying_clients": {"definition": "first paid_at per Admin inside requested UTC window", "status": "PROVEN"},
        "mrr": {"definition": "monthly recurring revenue", "status": "UNKNOWN", "reason": "plans are prepaid and auto-renewal is not implemented"},
        "cancellations": {"definition": "cancelled paid subscriptions", "status": "UNKNOWN", "reason": "no cancellation ledger exists"},
        "conversion": {"definition": "numerator/denominator must be selected from a documented funnel", "status": "UNKNOWN", "reason": "no canonical client conversion funnel exists"},
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _count(db: AsyncSession, model) -> int:
    return int(await db.scalar(select(func.count()).select_from(model)) or 0)


@router.get("/overview")
async def admin_overview(
    window_days: int = Query(default=30, ge=1, le=365),
    _: str = Depends(require_web_admin),
    db: AsyncSession = Depends(get_session),
):
    now = _now()
    start = now - timedelta(days=window_days)
    total_clients = await _count(db, Admin)
    paying_clients = int(await db.scalar(select(func.count(distinct(SubscriptionOrder.admin_id))).where(SubscriptionOrder.status == "paid")) or 0)
    active_subscriptions = int(await db.scalar(select(func.count()).select_from(Admin).where(Admin.subscription_expires_at > now)) or 0)
    new_clients = int(await db.scalar(select(func.count()).select_from(Admin).where(Admin.created_at >= start)) or 0)
    first_paid = select(SubscriptionOrder.admin_id, func.min(SubscriptionOrder.paid_at).label("first_paid_at")).where(SubscriptionOrder.status == "paid").group_by(SubscriptionOrder.admin_id).subquery()
    new_paying = int(await db.scalar(select(func.count()).select_from(first_paid).where(first_paid.c.first_paid_at >= start)) or 0)
    windows = {}
    for days in (1, 7, 30):
        window_start = now - timedelta(days=days)
        windows[str(days)] = {
            "from": window_start.isoformat(),
            "to": now.isoformat(),
            "new_clients": int(await db.scalar(select(func.count()).select_from(Admin).where(Admin.created_at >= window_start)) or 0),
            "new_paying_clients": int(await db.scalar(select(func.count()).select_from(first_paid).where(first_paid.c.first_paid_at >= window_start)) or 0),
        }
    return {
        "scope": {"window_days": window_days, "from": start.isoformat(), "to": now.isoformat(), "timezone": "UTC"},
        "windows": windows,
        "metrics": {
            "total_clients": total_clients,
            "paying_clients": paying_clients,
            "active_subscriptions": active_subscriptions,
            "new_clients": new_clients,
            "new_paying_clients": new_paying,
            "mrr": None,
            "cancellations": None,
            "conversion": {"numerator": None, "denominator": None, "status": "UNKNOWN"},
        },
        "definitions": canonical_metric_definitions(),
    }


@router.get("/clients")
async def admin_clients(
    limit: int = Query(default=100, ge=1, le=500),
    _: str = Depends(require_web_admin),
    db: AsyncSession = Depends(get_session),
):
    admins = list((await db.scalars(select(Admin).order_by(Admin.created_at.desc()).limit(limit))).all())
    rows = []
    for admin in admins:
        channel_count = await db.scalar(select(func.count()).select_from(Channel).where(Channel.admin_id == admin.id))
        paid_orders = await db.scalar(select(func.count()).select_from(SubscriptionOrder).where(SubscriptionOrder.admin_id == admin.id, SubscriptionOrder.status == "paid"))
        rows.append({
            "client_id": admin.id,
            "created_at": admin.created_at.isoformat() if admin.created_at else None,
            "channel_count": int(channel_count or 0),
            "paid_order_count": int(paid_orders or 0),
            "subscription_expires_at": admin.subscription_expires_at.isoformat() if admin.subscription_expires_at else None,
            "active_subscription": bool(admin.subscription_expires_at and admin.subscription_expires_at > _now()),
        })
    return {"items": rows, "read_only": True}


@router.get("/subscriptions")
async def admin_subscriptions(
    limit: int = Query(default=100, ge=1, le=500),
    _: str = Depends(require_web_admin),
    db: AsyncSession = Depends(get_session),
):
    orders = list((await db.scalars(select(SubscriptionOrder).order_by(SubscriptionOrder.created_at.desc()).limit(limit))).all())
    return {
        "items": [
            {
                "order_id": order.id,
                "client_id": order.admin_id,
                "channel_id": order.channel_id,
                "months": order.months,
                "amount": float(order.amount),
                "status": order.status,
                "created_at": order.created_at.isoformat() if order.created_at else None,
                "paid_at": order.paid_at.isoformat() if order.paid_at else None,
            }
            for order in orders
        ],
        "read_only": True,
    }


@router.get("/product-usage")
async def admin_product_usage(
    _: str = Depends(require_web_admin),
    db: AsyncSession = Depends(get_session),
):
    now = _now()
    today = now - timedelta(days=1)
    week = now - timedelta(days=7)
    return {
        "counts": {
            "channels": await _count(db, Channel),
            "bots": await _count(db, BotConnection),
            "conversations": await _count(db, Conversation),
            "connected_channels": int(await db.scalar(select(func.count()).select_from(Channel).where(Channel.external_id.is_not(None))) or 0),
            "yandex_connected_channels": int(await db.scalar(select(func.count()).select_from(TrafficProfile).where(TrafficProfile.yandex_enabled.is_(True), TrafficProfile.yandex_oauth_token_encrypted.is_not(None))) or 0),
            "metrica_connected_channels": int(await db.scalar(select(func.count()).select_from(TrafficProfile).where(TrafficProfile.yandex_counter_id.is_not(None))) or 0),
            "daily_reports_enabled_channels": int(await db.scalar(select(func.count()).select_from(Channel).where(Channel.daily_reports_enabled.is_(True))) or 0),
            "ai_runs": await _count(db, AdsAiRun),
            "ai_runs_today": int(await db.scalar(select(func.count()).select_from(AdsAiRun).where(AdsAiRun.created_at >= today)) or 0),
            "ai_runs_7d": int(await db.scalar(select(func.count()).select_from(AdsAiRun).where(AdsAiRun.created_at >= week)) or 0),
            "active_clients": int(await db.scalar(select(func.count()).select_from(Admin).where(Admin.subscription_expires_at > now)) or 0),
        },
        "read_only": True,
    }


@router.get("/system-health")
async def admin_system_health(
    _: str = Depends(require_web_admin),
    db: AsyncSession = Depends(get_session),
):
    await db.scalar(select(func.count()).select_from(Admin))
    return {
        "service": "maxibot",
        "api": {"status": "ready", "release_id": settings.maxibot_release_id},
        "max": {"mode": settings.max_client_mode},
        "database": {"status": "ready"},
        "read_only": True,
    }


@router.get("/qa-release")
async def admin_qa_release(_: str = Depends(require_web_admin)):
    return {
        "release_id": settings.maxibot_release_id,
        "qa_status": "UNKNOWN",
        "production_deploy": "NOT_CHECKED",
        "reason": "release evidence is maintained outside the read-only business API",
        "read_only": True,
    }


@router.get("/ads-storage")
async def admin_ads_storage(
    _: str = Depends(require_web_admin),
    db: AsyncSession = Depends(get_session),
):
    return {
        "counts": {
            "ai_runs": await _count(db, AdsAiRun),
            "snapshots": await _count(db, AdsSnapshot),
            "memories": await _count(db, AdsMemory),
        },
        "secrets_exposed": False,
        "read_only": True,
    }
