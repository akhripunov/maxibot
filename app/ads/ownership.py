"""Read-only ownership validation for regional ADS preparation."""
from __future__ import annotations

from dataclasses import dataclass
from sqlalchemy import func, select, exists
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdsAccount, AdsCampaign, AdsCollectionRun, AdsDataHealth, AdsEntity, AdsObservation, AdsReportRun


@dataclass(frozen=True)
class OwnershipCounts:
    total: int
    resolved: int
    ambiguous: int
    unowned: int


async def _count(db: AsyncSession, model, *, resolved, ambiguous=None) -> OwnershipCounts:
    total = int(await db.scalar(select(func.count()).select_from(model)) or 0)
    resolved_count = int(await db.scalar(select(func.count()).select_from(model).where(resolved)) or 0)
    ambiguous_count = int(await db.scalar(select(func.count()).select_from(model).where(ambiguous)) or 0) if ambiguous is not None else 0
    return OwnershipCounts(total, resolved_count, ambiguous_count, max(0, total - resolved_count - ambiguous_count))


async def validate_ads_account_ownership(db: AsyncSession) -> dict[str, OwnershipCounts]:
    """Return counts only; this helper never guesses or mutates ownership."""
    report = await _count(db, AdsReportRun, resolved=AdsReportRun.ads_account_id.is_not(None))
    collection = await _count(db, AdsCollectionRun, resolved=AdsCollectionRun.ads_account_id.is_not(None))
    health = await _count(db, AdsDataHealth, resolved=AdsDataHealth.ads_account_id.is_not(None))
    entity_owned = exists(select(1).select_from(AdsEntity).join(AdsCampaign, AdsCampaign.id == AdsEntity.campaign_id).where(AdsEntity.id == AdsObservation.entity_id, AdsCampaign.account_id.is_not(None)))
    report_owned = exists(select(1).select_from(AdsReportRun).where(AdsReportRun.id == AdsObservation.report_run_id, AdsReportRun.ads_account_id.is_not(None)))
    collection_owned = exists(select(1).select_from(AdsCollectionRun).where(AdsCollectionRun.id == AdsObservation.collection_run_id, AdsCollectionRun.ads_account_id.is_not(None)))
    observations = await _count(db, AdsObservation, resolved=entity_owned | report_owned | collection_owned)
    return {"report_runs": report, "collection_runs": collection, "data_health": health, "observations": observations}


async def conflicting_entity_accounts(db: AsyncSession) -> int:
    """Count provider entities whose same identity is attached to campaigns from multiple accounts."""
    rows = await db.execute(select(AdsEntity.provider, AdsEntity.entity_type, AdsEntity.provider_entity_id, func.count(func.distinct(AdsCampaign.account_id))).join(AdsCampaign, AdsCampaign.id == AdsEntity.campaign_id).group_by(AdsEntity.provider, AdsEntity.entity_type, AdsEntity.provider_entity_id).having(func.count(func.distinct(AdsCampaign.account_id)) > 1))
    return len(rows.all())
