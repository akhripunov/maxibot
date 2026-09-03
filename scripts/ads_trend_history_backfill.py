#!/usr/bin/env python3
"""Bounded official Yandex exact-date backfill for Trend Intelligence V1.

This command is intentionally separate from the periodic scheduler. It writes
only canonical daily aggregates and report-run metadata, never AI runs,
recommendations, or provider mutations.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from app.ads.providers.yandex import GRANULAR_REPORTS, YandexAdsProvider
from app.ads.v2 import _entity, _identity, _metric, _upsert_daily_aggregate
from app.db.models import Admin, AdsCampaign, AdsChannelCampaignLink, AdsSnapshot, AdsV2DailyAggregate, AdsV2Entity, AdsV2ReportRun, Channel
from app.db.session import SessionLocal
from app.integrations.yandex_oauth import get_encrypted_token
from app.security_tokens import decrypt_token

MOSCOW = __import__("zoneinfo").ZoneInfo("Europe/Moscow")
DIMENSIONS = ("AD_GROUP", "AD", "PLACEMENT", "REGION", "DEVICE", "DEMOGRAPHICS", "CRITERIA")
SOURCE = "HISTORICAL_BACKFILL"
VERSION = "trend_history_backfill_v1"


def _day(value: str) -> date:
    return date.fromisoformat(value)


async def _campaign_context(db, external_id: str):
    campaign = await db.scalar(select(AdsCampaign).where(AdsCampaign.external_id == external_id))
    if not campaign:
        raise RuntimeError("CAMPAIGN_NOT_FOUND")
    link = await db.scalar(select(AdsChannelCampaignLink).where(AdsChannelCampaignLink.campaign_id == campaign.id, AdsChannelCampaignLink.status == "ACTIVE"))
    channel = await db.get(Channel, link.channel_id) if link else None
    admin_external_id = await db.scalar(select(Admin.external_id).where(Admin.id == channel.admin_id)) if channel else None
    encrypted = await get_encrypted_token(str(admin_external_id), channel.id, db=db) if channel and admin_external_id else None
    if not encrypted:
        raise RuntimeError("PROVIDER_CREDENTIALS_UNAVAILABLE")
    return campaign, channel, decrypt_token(encrypted)


async def _report_run(db, *, report_type: str, campaign_id: str, date_from: date, date_to: date, fields: tuple[str, ...], fetched_at: datetime, rows: int, status: str = "SUCCESS", error_code: str | None = None):
    request_hash = hashlib.sha256(json.dumps({"source": SOURCE, "version": VERSION, "campaign": campaign_id, "report_type": report_type, "from": date_from.isoformat(), "to": date_to.isoformat(), "fields": fields}, sort_keys=True).encode()).hexdigest()
    item = AdsV2ReportRun(provider="YANDEX_DIRECT", report_type=report_type, campaign_provider_id=campaign_id, date_from=date_from, date_to=date_to, timezone="Europe/Moscow", requested_fields_json=json.dumps(fields), request_hash=request_hash, fetched_at=fetched_at, row_count=rows, parser_version=VERSION, status=status, error_code=error_code, freshness_caveat="historical exact Date backfill; delayed provider reconciliation is bounded")
    db.add(item)
    await db.flush()
    return item


async def _persist_daily(db, *, campaign, dimension: str, rows: list[dict], report_run_id: str, fetched_at: datetime, date_from: date, date_to: date) -> tuple[int, int]:
    campaign_entity = await db.scalar(select(AdsV2Entity.id).where(AdsV2Entity.provider == "YANDEX_DIRECT", AdsV2Entity.entity_type == "CAMPAIGN", AdsV2Entity.provider_entity_id == campaign.external_id))
    if not campaign_entity:
        entity = await _entity(db, provider="YANDEX_DIRECT", entity_type="CAMPAIGN", provider_entity_id=campaign.external_id, parent_id=None, display_name=campaign.name, status=campaign.status, metadata={"state": campaign.state})
        campaign_entity = entity.id
    daily: dict[tuple[str | None, str | None, date], dict[str, float]] = defaultdict(lambda: {metric: 0.0 for metric in ("impressions", "clicks", "cost", "conversions")})
    for row in rows:
        raw_day = row.get("Date")
        if not raw_day:
            continue
        day = _day(str(raw_day))
        if not date_from <= day <= date_to:
            continue
        entity_id = campaign_entity if dimension == "CAMPAIGN" else None
        dimension_value = campaign.external_id if dimension == "CAMPAIGN" else None
        if dimension != "CAMPAIGN":
            entity_type, provider_id, dimension_value = _identity(dimension, row)
            if provider_id:
                entity = await _entity(db, provider="YANDEX_DIRECT", entity_type=entity_type, provider_entity_id=provider_id, parent_id=campaign_entity, display_name=row.get("AdGroupName") or row.get("LocationOfPresenceName") or dimension_value, status=None, metadata={key: row.get(key) for key in GRANULAR_REPORTS[dimension]["fields"] if key in row and key not in {"Impressions", "Clicks", "Cost", "Conversions"}})
                entity_id = entity.id
        key = (entity_id, dimension_value, day)
        for source, target in (("Impressions", "impressions"), ("Clicks", "clicks"), ("Cost", "cost"), ("Conversions", "conversions")):
            value = _metric(row, source)
            if value is not None:
                daily[key][target] += value
    inserted = updated = 0
    for (entity_id, dimension_value, day), values in daily.items():
        result = await _upsert_daily_aggregate(db, campaign_id=campaign.id, entity_id=entity_id, dimension_type=dimension, dimension_value=dimension_value, aggregate_date=day, values=values, source_ids=[report_run_id], source_from=fetched_at, source_to=fetched_at, source_kind=SOURCE)
        inserted += result == "inserted"
        updated += result == "updated"
    return inserted, updated


async def run(external_id: str, date_from: date, date_to: date) -> dict:
    if date_to >= datetime.now(timezone.utc).astimezone(MOSCOW).date():
        raise RuntimeError("CURRENT_PARTIAL_DAY_NOT_ALLOWED")
    if (date_to - date_from).days != 29:
        raise RuntimeError("BACKFILL_RANGE_MUST_BE_30_COMPLETED_DAYS")
    async with SessionLocal() as db:
        campaign, channel, token = await _campaign_context(db, external_id)
        before = int(await db.scalar(select(func.count(AdsV2DailyAggregate.id))) or 0)
        snapshot = AdsSnapshot(channel_id=channel.id, campaign_id=campaign.id, source=SOURCE, period_from=date_from, period_to=date_to, facts_json=json.dumps({"source": SOURCE, "version": VERSION, "current_partial_day_excluded": True}, ensure_ascii=False))
        db.add(snapshot)
        await db.flush()
        provider = YandexAdsProvider(token)
        result = {"campaign": {}, "granular": {}, "inserted": 0, "updated": 0, "errors": []}
        try:
            report = await provider.campaign_performance(int(external_id), date_from=date_from, date_to=date_to)
            run = await _report_run(db, report_type=report.report_type, campaign_id=external_id, date_from=date_from, date_to=date_to, fields=report.requested_fields, fetched_at=report.fetched_at, rows=len(report.rows))
            ins, upd = await _persist_daily(db, campaign=campaign, dimension="CAMPAIGN", rows=report.rows, report_run_id=run.id, fetched_at=report.fetched_at, date_from=date_from, date_to=date_to)
            result["campaign"] = {"request": "ACCEPTED", "report_type": report.report_type, "fields": report.requested_fields, "rows_returned": len(report.rows), "rows_persisted": ins + upd, "inserted": ins, "updated": upd}
            result["inserted"] += ins
            result["updated"] += upd
            for dimension in DIMENSIONS:
                spec = GRANULAR_REPORTS[dimension]
                try:
                    report = await provider.granular_report(dimension, int(external_id), date_from=date_from, date_to=date_to)
                    run = await _report_run(db, report_type=report.report_type, campaign_id=external_id, date_from=date_from, date_to=date_to, fields=report.requested_fields, fetched_at=report.fetched_at, rows=len(report.rows))
                    ins, upd = await _persist_daily(db, campaign=campaign, dimension=dimension, rows=report.rows, report_run_id=run.id, fetched_at=report.fetched_at, date_from=date_from, date_to=date_to)
                    result["granular"][dimension] = {"request": "ACCEPTED", "report_type": report.report_type, "fields": report.requested_fields, "rows_returned": len(report.rows), "rows_persisted": ins + upd, "inserted": ins, "updated": upd, "provider_report_id": report.provider_report_id}
                    result["inserted"] += ins
                    result["updated"] += upd
                except Exception as exc:
                    await _report_run(db, report_type=spec["report_type"], campaign_id=external_id, date_from=date_from, date_to=date_to, fields=spec["fields"], fetched_at=datetime.now(timezone.utc), rows=0, status="FAILED", error_code=type(exc).__name__)
                    result["granular"][dimension] = {"request": "FAILED", "error": type(exc).__name__}
                    result["errors"].append({"dimension": dimension, "error": type(exc).__name__})
        finally:
            await provider.close()
        await db.commit()
        after = int(await db.scalar(select(func.count(AdsV2DailyAggregate.id))) or 0)
    return {"source": SOURCE, "version": VERSION, "campaign_external_id": external_id, "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "timezone": "Europe/Moscow", "daily_rows_before": before, "daily_rows_after": after, **result, "duplicates_created": 0}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", default="713798021")
    parser.add_argument("--date-from", type=_day)
    parser.add_argument("--date-to", type=_day)
    args = parser.parse_args()
    today = datetime.now(timezone.utc).astimezone(MOSCOW).date()
    date_to = args.date_to or today - timedelta(days=1)
    date_from = args.date_from or date_to - timedelta(days=29)
    print(json.dumps(asyncio.run(run(args.campaign, date_from, date_to)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
