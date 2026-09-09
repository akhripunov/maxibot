"""Bounded, read-only creative performance and intelligence queries."""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdsCampaign, AdsCreative, AdsEntity, AdsObservation


def _f(value: Any) -> float:
    return float(value or 0)


async def creative_performance(
    db: AsyncSession, *, campaign_id: str, date_from: date, date_to: date
) -> list[dict[str, Any]]:
    """Return deterministic creative metrics joined through canonical AD entities."""
    rows = (await db.execute(
        select(AdsCreative, AdsObservation)
        .join(AdsEntity, AdsEntity.id == AdsCreative.ad_entity_id)
        .join(AdsObservation, AdsObservation.entity_id == AdsEntity.id)
        .where(
            AdsCreative.campaign_id == campaign_id,
            AdsObservation.date_from >= date_from,
            AdsObservation.date_to <= date_to,
        )
    )).all()
    totals: dict[str, dict[str, Any]] = {}
    for creative, observation in rows:
        item = totals.setdefault(creative.id, {
            "creative_id": creative.id,
            "external_creative_id": creative.external_creative_id,
            "creative_type": creative.creative_type,
            "content_hash": creative.content_hash,
            "impressions": 0.0, "clicks": 0.0, "cost": 0.0, "provider_conversions": 0.0,
            "observation_count": 0,
        })
        item["impressions"] += _f(observation.impressions)
        item["clicks"] += _f(observation.clicks)
        item["cost"] += _f(observation.cost)
        item["provider_conversions"] += _f(observation.conversions)
        item["observation_count"] += 1
    for item in totals.values():
        item["ctr"] = item["clicks"] / item["impressions"] if item["impressions"] else None
        item["cpc"] = item["cost"] / item["clicks"] if item["clicks"] else None
        item["sample_size"] = item["impressions"]
        item["attribution_quality"] = "UNKNOWN"
        item["status"] = "INSUFFICIENT_DATA" if item["impressions"] < 100 or item["clicks"] < 10 else "OBSERVED"
        item["confirmed_paid_product_conversion"] = None
    return list(totals.values())


async def creative_intelligence(
    db: AsyncSession, *, campaign_id: str, date_from: date, date_to: date
) -> dict[str, Any]:
    creatives = await creative_performance(db, campaign_id=campaign_id, date_from=date_from, date_to=date_to)
    return {
        "campaign_id": campaign_id,
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "status": "INSUFFICIENT_DATA" if not creatives or sum(x["clicks"] for x in creatives) < 10 else "OBSERVED",
        "attribution_quality": "UNKNOWN",
        "confirmed_product_conversions": "UNKNOWN",
        "creatives": creatives,
        "guard": "No creative-level max_subscriber/bot_started attribution without granular evidence",
    }
