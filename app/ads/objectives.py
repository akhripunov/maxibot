"""MaxiBot business objectives, separate from provider campaign settings."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdsCampaign, AdsPromotionCampaignLink

OBJECTIVE_TYPE = "TARGET_CPA"
ACTIVE = "ACTIVE"
DISABLED = "DISABLED"
PRIMARY_OUTCOMES = {"CHANNEL": "max_subscriber", "BOT": "bot_started"}


def primary_outcome(object_type: str) -> str:
    try:
        return PRIMARY_OUTCOMES[object_type.upper()]
    except KeyError as exc:
        raise ValueError("ADS_PROMOTION_OBJECT_TYPE_UNSUPPORTED") from exc


def validate_target_cpa(value: Any) -> Decimal:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, ValueError):
        raise ValueError("TARGET_CPA_INVALID")
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError("TARGET_CPA_MUST_BE_POSITIVE")
    return parsed


def validate_currency(value: str | None) -> str:
    currency = (value or "RUB").strip().upper()
    if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
        raise ValueError("CURRENCY_INVALID")
    return currency


async def active_links(db: AsyncSession, *, object_type: str, object_id: str, campaign_id: str | None = None) -> list[AdsPromotionCampaignLink]:
    predicate = AdsPromotionCampaignLink.channel_id == object_id if object_type == "CHANNEL" else AdsPromotionCampaignLink.bot_promotion_profile_id == object_id
    query = select(AdsPromotionCampaignLink).where(
        AdsPromotionCampaignLink.object_type == object_type,
        predicate,
        AdsPromotionCampaignLink.status == "ACTIVE",
    )
    if campaign_id:
        query = query.where(AdsPromotionCampaignLink.campaign_id == campaign_id)
    return list((await db.scalars(query.order_by(AdsPromotionCampaignLink.created_at))).all())


async def object_links(db: AsyncSession, *, object_type: str, object_id: str, campaign_id: str | None = None) -> list[AdsPromotionCampaignLink]:
    """Return all synchronized campaign links, independent of analysis selection.

    Link status is the separate analysis/autopilot selection state; an owner
    must be able to configure the object-level target CPA before selecting a
    campaign for analysis.
    """
    predicate = AdsPromotionCampaignLink.channel_id == object_id if object_type == "CHANNEL" else AdsPromotionCampaignLink.bot_promotion_profile_id == object_id
    query = select(AdsPromotionCampaignLink).where(
        AdsPromotionCampaignLink.object_type == object_type,
        predicate,
    )
    if campaign_id:
        query = query.where(AdsPromotionCampaignLink.campaign_id == campaign_id)
    return list((await db.scalars(query.order_by(AdsPromotionCampaignLink.created_at))).all())


async def set_objective(db: AsyncSession, *, object_type: str, object_id: str, target_cpa: Any, currency: str = "RUB", campaign_id: str | None = None) -> list[AdsPromotionCampaignLink]:
    object_type = object_type.upper()
    target = validate_target_cpa(target_cpa)
    currency = validate_currency(currency)
    links = await object_links(db, object_type=object_type, object_id=object_id, campaign_id=campaign_id)
    if not links:
        raise ValueError("ACTIVE_PROMOTION_CAMPAIGN_NOT_FOUND")
    timestamp = datetime.now(timezone.utc)
    for link in links:
        link.objective_type = OBJECTIVE_TYPE
        link.target_cpa = target
        link.currency = currency
        link.objective_status = ACTIVE
        link.objective_updated_at = timestamp
    await db.flush()
    return links


async def disable_objective(db: AsyncSession, *, object_type: str, object_id: str, campaign_id: str | None = None) -> list[AdsPromotionCampaignLink]:
    links = await active_links(db, object_type=object_type, object_id=object_id, campaign_id=campaign_id)
    timestamp = datetime.now(timezone.utc)
    for link in links:
        link.objective_status = DISABLED
        link.objective_updated_at = timestamp
    await db.flush()
    return links


async def objective_for_link(db: AsyncSession, *, object_type: str, object_id: str, campaign_id: str) -> dict[str, Any]:
    links = await active_links(db, object_type=object_type, object_id=object_id, campaign_id=campaign_id)
    link = links[0] if links else None
    if not link or link.objective_status != ACTIVE or link.objective_type != OBJECTIVE_TYPE or link.target_cpa is None:
        return {"objective_mode": "NONE", "objective_status": "UNSET", "primary_outcome": primary_outcome(object_type)}
    return {
        "objective_mode": OBJECTIVE_TYPE,
        "objective_type": link.objective_type,
        "target_cpa": float(link.target_cpa),
        "currency": link.currency or "RUB",
        "objective_status": link.objective_status,
        "objective_updated_at": link.objective_updated_at.isoformat() if link.objective_updated_at else None,
        "primary_outcome": primary_outcome(object_type),
        "promotion_object_type": object_type,
        "promotion_object_id": object_id,
        "campaign_id": campaign_id,
    }


def evaluate_objective(objective: dict[str, Any], *, cost: Any, confirmed_outcomes: Any, attribution_coverage: float | None = None) -> dict[str, Any]:
    """Evaluate target CPA without ever turning zero outcomes into CPA=0."""
    target = objective.get("target_cpa")
    if objective.get("objective_mode") != OBJECTIVE_TYPE or target is None:
        return {"objective_state": "NONE", "objective_mode": "NONE"}
    spend = float(cost or 0)
    outcomes = int(confirmed_outcomes or 0)
    if attribution_coverage is not None and attribution_coverage < 1.0:
        return {**objective, "objective_state": "INSUFFICIENT_DATA", "reason": "INSUFFICIENT_ATTRIBUTION", "actual_cpa": None, "absolute_gap": None, "relative_gap": None}
    if spend > 0 and outcomes == 0:
        return {**objective, "objective_state": "INSUFFICIENT_DATA", "reason": "NO_CONFIRMED_OUTCOMES", "actual_cpa": None, "absolute_gap": None, "relative_gap": None}
    if outcomes <= 0:
        return {**objective, "objective_state": "INSUFFICIENT_DATA", "reason": "NO_CONFIRMED_OUTCOMES", "actual_cpa": None, "absolute_gap": None, "relative_gap": None}
    actual = spend / outcomes
    absolute = actual - float(target)
    return {**objective, "objective_state": "ON_TARGET" if actual <= float(target) else "OFF_TARGET", "reason": None, "actual_cpa": actual, "absolute_gap": absolute, "relative_gap": absolute / float(target)}
