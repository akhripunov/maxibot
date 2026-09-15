"""Generic persistence for read-only Telegram Ads reports."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ads.telegram_normalization import logical_observation_key, normalize_stats
from app.db.models import AdsAccount, AdsCampaign, AdsCollectionRun, AdsDataHealth, AdsDailyAggregate, AdsObservation, AdsReportRun


async def persist_telegram_report(
    db: AsyncSession,
    *,
    account: AdsAccount,
    campaign: AdsCampaign,
    collection_run: AdsCollectionRun,
    rows: list[dict],
    date_from: date | None,
    date_to: date | None,
    fetched_at: datetime,
    report_type: str = "TELEGRAM_AD_STATS",
) -> dict[str, int | str]:
    """Persist one source report idempotently using canonical ADS tables."""
    request_hash = logical_observation_key(account_id=account.id, external_scope_id=str(campaign.external_id), report_type=report_type, date_from=date_from, date_to=date_to)
    report = await db.scalar(select(AdsReportRun).where(AdsReportRun.request_hash == request_hash))
    if report is None:
        report = AdsReportRun(provider="TELEGRAM_ADS", ads_account_id=account.id, report_type=report_type, campaign_provider_id=str(campaign.external_id), date_from=date_from, date_to=date_to, requested_fields_json=json.dumps(sorted(rows[0].keys()) if rows else []), request_hash=request_hash, fetched_at=fetched_at, row_count=len(rows), parser_version="telegram-v1", status="SUCCESS")
        db.add(report)
        await db.flush()
    else:
        report.fetched_at = fetched_at
        report.row_count = len(rows)
        report.status = "SUCCESS"
    created = 0
    for raw in rows:
        values = normalize_stats(raw)
        logical_key = logical_observation_key(account_id=account.id, external_scope_id=str(raw.get("CampaignId") or campaign.external_id), report_type=report_type, date_from=date_from, date_to=date_to)
        observation = await db.scalar(select(AdsObservation).where(AdsObservation.logical_key == logical_key))
        payload = dict(snapshot_id=None, entity_id=None, dimension_type="CAMPAIGN", dimension_value=str(raw.get("CampaignId") or campaign.external_id), window="7d", date_from=date_from, date_to=date_to, impressions=values["impressions"], clicks=values["clicks"], cost=values["cost"], conversions=values["conversions"], report_run_id=report.id, collection_run_id=collection_run.id, logical_key=logical_key, provenance_json=json.dumps({"provider": "TELEGRAM_ADS", "account_id": account.id, "fetched_at": fetched_at.isoformat(), "parser_version": "telegram-v1", "unavailable_metrics": [key for key, value in values.items() if value is None]}, ensure_ascii=False, sort_keys=True))
        if observation is None:
            db.add(AdsObservation(**payload))
            created += 1
        else:
            for key, value in payload.items():
                if key not in {"logical_key", "created_at"}:
                    setattr(observation, key, value)
        aggregate = await db.scalar(select(AdsDailyAggregate).where(AdsDailyAggregate.provider == "TELEGRAM_ADS", AdsDailyAggregate.campaign_id == campaign.id, AdsDailyAggregate.entity_id.is_(None), AdsDailyAggregate.dimension_type == "CAMPAIGN", AdsDailyAggregate.dimension_value == str(raw.get("CampaignId") or campaign.external_id), AdsDailyAggregate.aggregate_date == (date_to or date.today())))
        aggregate_values = dict(provider="TELEGRAM_ADS", campaign_id=campaign.id, entity_id=None, dimension_type="CAMPAIGN", dimension_value=str(raw.get("CampaignId") or campaign.external_id), aggregate_date=(date_to or date.today()), impressions=values["impressions"], clicks=values["clicks"], cost=values["cost"], conversions=values["conversions"], source_observation_count=1, source_from=fetched_at, source_to=fetched_at, aggregation_version="telegram-v1", provenance_json=json.dumps({"report_run_id": report.id}, sort_keys=True))
        if aggregate is None:
            db.add(AdsDailyAggregate(**aggregate_values))
        else:
            for key, value in aggregate_values.items():
                if key not in {"provider", "campaign_id", "entity_id", "dimension_type", "dimension_value", "aggregate_date"}:
                    setattr(aggregate, key, value)
    collection_run.status = "COMPLETED"
    collection_run.completed_at = fetched_at
    collection_run.freshness_caveat = None
    health = await db.scalar(select(AdsDataHealth).where(AdsDataHealth.promotion_object_id == account.id, AdsDataHealth.destination_platform == "TELEGRAM_ADS").order_by(AdsDataHealth.checked_at.desc()).limit(1))
    if health is None:
        health = AdsDataHealth(ads_account_id=account.id, promotion_object_id=account.id, promotion_object_type="ACCOUNT", destination_platform="TELEGRAM_ADS", status="HEALTHY", reasons_json="[]", checked_at=fetched_at)
        db.add(health)
    else:
        health.status = "HEALTHY"
        health.reasons_json = "[]"
        health.checked_at = fetched_at
    await db.flush()
    return {"report_run_id": report.id, "observations_created": created, "rows": len(rows)}
