"""Small provider-independent V2 persistence layer for read-only observations."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ads.providers.yandex import GRANULAR_REPORTS, YandexAdsProvider
from app.db.models import AdsCampaign, AdsSnapshot, AdsV2DailyAggregate, AdsV2Entity, AdsV2Observation, AdsV2ReportRun

WINDOWS = {"1d": 1, "3d": 3, "7d": 7}


async def _upsert_daily_aggregate(db: AsyncSession, *, campaign_id: str, entity_id: str | None, dimension_type: str, dimension_value: str | None, aggregate_date: date, values: dict[str, float], source_ids: list[str], source_from: datetime | None, source_to: datetime | None, source_kind: str = "PERIODIC") -> str:
    aggregate = await db.scalar(select(AdsV2DailyAggregate).where(
        AdsV2DailyAggregate.provider == "YANDEX_DIRECT",
        AdsV2DailyAggregate.campaign_id == campaign_id,
        AdsV2DailyAggregate.entity_id == entity_id,
        AdsV2DailyAggregate.dimension_type == dimension_type,
        AdsV2DailyAggregate.dimension_value == dimension_value,
        AdsV2DailyAggregate.aggregate_date == aggregate_date,
    ))
    payload = {metric: float(values.get(metric) or 0) for metric in ("impressions", "clicks", "cost", "conversions")}
    provenance = json.dumps({"provider": "YANDEX_DIRECT", "source": source_kind, "campaign_id": campaign_id, "entity_type": dimension_type, "entity_id": entity_id, "dimension_value": dimension_value, "date": aggregate_date.isoformat(), "timezone": "Europe/Moscow", "source_report_run_ids": list(dict.fromkeys(source_ids)), "metric_values": payload, "fetched_at": (source_to or source_from).isoformat() if (source_to or source_from) else None, "aggregation": "sum exact Date rows; current day excluded from confirmed trends", "aggregation_version": "trend_intelligence_v1"}, ensure_ascii=False)
    result = "updated" if aggregate else "inserted"
    if not aggregate:
        aggregate = AdsV2DailyAggregate(provider="YANDEX_DIRECT", campaign_id=campaign_id, entity_id=entity_id, dimension_type=dimension_type, dimension_value=dimension_value, aggregate_date=aggregate_date, source_observation_count=0)
        db.add(aggregate)
    aggregate.impressions, aggregate.clicks, aggregate.cost, aggregate.conversions = payload["impressions"], payload["clicks"], payload["cost"], payload["conversions"]
    aggregate.source_observation_count = len(source_ids)
    aggregate.source_from, aggregate.source_to, aggregate.provenance_json = source_from, source_to, provenance
    return result


async def _entity(db: AsyncSession, *, provider: str, entity_type: str, provider_entity_id: str, parent_id: str | None, display_name: str | None, status: str | None, metadata: dict[str, Any]) -> AdsV2Entity:
    entity = await db.scalar(select(AdsV2Entity).where(AdsV2Entity.provider == provider, AdsV2Entity.entity_type == entity_type, AdsV2Entity.provider_entity_id == provider_entity_id))
    if not entity:
        entity = AdsV2Entity(provider=provider, entity_type=entity_type, provider_entity_id=provider_entity_id, parent_id=parent_id, display_name=display_name, status=status, provider_metadata_json=json.dumps(metadata, ensure_ascii=False))
        db.add(entity)
    else:
        entity.parent_id, entity.display_name, entity.status = parent_id, display_name, status
        entity.provider_metadata_json = json.dumps(metadata, ensure_ascii=False)
    await db.flush()
    return entity


async def collect_yandex_v2(db: AsyncSession, *, token: str, campaign: AdsCampaign, snapshot: AdsSnapshot, report_rows: list[dict[str, Any]], date_from: date, date_to: date, fetched_at: datetime, collection_run_id: str | None = None) -> dict[str, Any]:
    """Persist available campaign/ad metadata and campaign observations.

    Yandex's currently used integration exposes campaign performance and ad
    metadata only. Unsupported dimensions are reported explicitly rather than
    represented by invented rows.
    """
    provider = YandexAdsProvider(token)
    fields = ("Date", "CampaignId", "CampaignName", "Impressions", "Clicks", "Cost", "Conversions")
    request_hash = hashlib.sha256(json.dumps({"campaign": campaign.external_id, "from": date_from.isoformat(), "to": date_to.isoformat(), "fields": fields}, sort_keys=True).encode()).hexdigest()
    report_run = AdsV2ReportRun(provider="YANDEX_DIRECT", report_type="CAMPAIGN_PERFORMANCE_REPORT", campaign_provider_id=campaign.external_id, date_from=date_from, date_to=date_to, timezone="Europe/Moscow", requested_fields_json=json.dumps(fields), request_hash=request_hash, fetched_at=fetched_at, row_count=len(report_rows), parser_version="v1", status="SUCCESS", freshness_caveat="current calendar day may be incomplete or delayed")
    db.add(report_run)
    await db.flush()
    campaign_entity = await _entity(db, provider="YANDEX_DIRECT", entity_type="CAMPAIGN", provider_entity_id=campaign.external_id, parent_id=None, display_name=campaign.name, status=campaign.status, metadata={"state": campaign.state})
    daily: dict[date, dict[str, float]] = {}
    for row in report_rows:
        if not row.get("Date"):
            continue
        day = date.fromisoformat(str(row["Date"]))
        daily.setdefault(day, {metric: 0.0 for metric in ("impressions", "clicks", "cost", "conversions")})
        for source, target in (("Impressions", "impressions"), ("Clicks", "clicks"), ("Cost", "cost"), ("Conversions", "conversions")):
            if row.get(source) not in (None, "", "-"):
                daily[day][target] += float(row[source])
    for day, values in daily.items():
        await _upsert_daily_aggregate(db, campaign_id=campaign.id, entity_id=campaign_entity.id, dimension_type="CAMPAIGN", dimension_value=campaign.external_id, aggregate_date=day, values=values, source_ids=[report_run.id], source_from=fetched_at, source_to=fetched_at)
    for window, days in WINDOWS.items():
        start = date_to - timedelta(days=days - 1)
        selected = [row for row in report_rows if start.isoformat() <= str(row.get("Date")) <= date_to.isoformat()]
        def number(key: str) -> float | None:
            values = [float(row[key]) for row in selected if row.get(key) not in (None, "", "-")]
            return sum(values) if values else None
        logical_key = hashlib.sha256(f"YANDEX_DIRECT|CAMPAIGN_PERFORMANCE_REPORT|{collection_run_id or snapshot.id}|CAMPAIGN|{campaign.external_id}|{start}|{date_to}|{window}".encode()).hexdigest()
        db.add(AdsV2Observation(snapshot_id=snapshot.id, entity_id=campaign_entity.id, dimension_type="CAMPAIGN", dimension_value=campaign.external_id, window=window, date_from=start, date_to=date_to, impressions=number("Impressions"), clicks=number("Clicks"), cost=number("Cost"), conversions=number("Conversions"), report_run_id=report_run.id, collection_run_id=collection_run_id, logical_key=logical_key, provenance_json=json.dumps({"provider": "YANDEX_DIRECT", "source_fields": fields, "date_from": start.isoformat(), "date_to": date_to.isoformat(), "timezone": "Europe/Moscow", "aggregation": "sum by Date", "report_run_id": report_run.id, "collection_run_id": collection_run_id, "fetched_at": fetched_at.isoformat(), "snapshot_at": snapshot.created_at.isoformat() if snapshot.created_at else None}, ensure_ascii=False)))
    available_ads = 0
    try:
        ads_report = await provider.ads([int(campaign.external_id)])
        ads_report_run = AdsV2ReportRun(provider="YANDEX_DIRECT", report_type="ADS", campaign_provider_id=campaign.external_id, timezone="Europe/Moscow", requested_fields_json=json.dumps(ads_report.requested_fields), fetched_at=ads_report.fetched_at or fetched_at, row_count=len(ads_report.rows), parser_version="v1", status=ads_report.status, error_code=ads_report.error_code)
        db.add(ads_report_run)
        await db.flush()
        for raw in ads_report.rows:
            await _entity(db, provider="YANDEX_DIRECT", entity_type="AD", provider_entity_id=str(raw.get("Id")), parent_id=campaign_entity.id, display_name=None, status=raw.get("Status"), metadata={key: raw.get(key) for key in ads_report.requested_fields})
        available_ads = len(ads_report.rows)
    finally:
        await provider.close()
    return {"entities": {"campaigns": 1, "ad_groups": 0, "ads": available_ads, "creatives": 0}, "dimensions": {"placements": "NOT_SUPPORTED", "targeting": "NOT_SUPPORTED", "devices": "NOT_SUPPORTED", "regions": "NOT_SUPPORTED", "time": "NOT_SUPPORTED"}, "report_runs": 2, "missing_data": ["AD_GROUPS", "CREATIVES", "PLACEMENTS", "TARGETING", "DEVICES", "REGIONS", "TIME"], "ai_context": {"rows_available": len(report_rows) + available_ads, "rows_included": min(len(report_rows), 50), "truncated": len(report_rows) > 50, "selection": "campaign metrics plus all available ad metadata"}}


def _metric(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value in (None, "", "-"):
        return None
    return float(value)


def _identity(dimension: str, row: dict[str, Any]) -> tuple[str, str | None, str | None]:
    """Return (entity type, provider identity, observation dimension value)."""
    if dimension == "AD_GROUP":
        value = str(row.get("AdGroupId") or "")
        return "AD_GROUP", value or None, value or None
    if dimension == "AD":
        value = str(row.get("AdId") or "")
        return "AD", value or None, value or None
    if dimension == "PLACEMENT":
        value = str(row.get("Placement") or "")
        return "PLACEMENT", f"placement:{value}" if value else None, value or None
    if dimension == "REGION":
        value = str(row.get("LocationOfPresenceId") or row.get("LocationOfPresenceName") or "")
        return "REGION", f"region:{value}" if value else None, value or None
    if dimension == "CRITERIA":
        value = str(row.get("CriteriaId") or "")
        return "CRITERION", value or None, value or None
    return dimension, None, "|".join(str(row.get(field) or "") for field in {
        "DEVICE": ("Device", "MobilePlatform"),
        "DEMOGRAPHICS": ("Age", "Gender"),
        "SEARCH_QUERY": ("Query", "CriteriaId"),
    }.get(dimension, ())) or None


async def collect_granular_yandex_v2(db: AsyncSession, *, token: str, campaign: AdsCampaign, snapshot: AdsSnapshot, date_from: date, date_to: date, fetched_at: datetime, collection_run_id: str, dimensions: tuple[str, ...] | None = None) -> dict[str, Any]:
    """Collect independent documented reports and persist daily observations."""
    provider = YandexAdsProvider(token)
    results: dict[str, Any] = {"reports": {}, "rows": 0, "errors": []}
    try:
        selected_dimensions = dimensions or tuple(d for d in GRANULAR_REPORTS if d != "SEARCH_QUERY")
        for dimension in selected_dimensions:
            spec = GRANULAR_REPORTS[dimension]
            try:
                report = await provider.granular_report(dimension, int(campaign.external_id), date_from=date_from, date_to=date_to)
                report_run = AdsV2ReportRun(provider="YANDEX_DIRECT", report_type=report.report_type, campaign_provider_id=campaign.external_id, date_from=date_from, date_to=date_to, timezone="Europe/Moscow", requested_fields_json=json.dumps(report.requested_fields), request_hash=hashlib.sha256(f"{collection_run_id}|{dimension}".encode()).hexdigest(), fetched_at=report.fetched_at or fetched_at, row_count=len(report.rows), parser_version="v1", status=report.status, error_code=report.error_code, freshness_caveat=report.freshness_caveat)
                db.add(report_run)
                await db.flush()
                results["reports"][dimension] = {"report_type": report.report_type, "rows": len(report.rows), "accepted": True, "provider_report_id": report.provider_report_id}
                daily: dict[tuple[str | None, str | None, date], dict[str, float]] = {}
                for row in report.rows:
                    day = row.get("Date")
                    if not day:
                        continue
                    entity_type, provider_id, dimension_value = _identity(dimension, row)
                    entity_id = None
                    if provider_id:
                        parent_id = None
                        if entity_type == "AD_GROUP":
                            parent_id = (await db.scalar(select(AdsV2Entity.id).where(AdsV2Entity.provider == "YANDEX_DIRECT", AdsV2Entity.entity_type == "CAMPAIGN", AdsV2Entity.provider_entity_id == campaign.external_id)))
                        elif entity_type == "AD" and row.get("AdGroupId"):
                            group = await _entity(db, provider="YANDEX_DIRECT", entity_type="AD_GROUP", provider_entity_id=str(row["AdGroupId"]), parent_id=(await db.scalar(select(AdsV2Entity.id).where(AdsV2Entity.provider == "YANDEX_DIRECT", AdsV2Entity.entity_type == "CAMPAIGN", AdsV2Entity.provider_entity_id == campaign.external_id))), display_name=None, status=None, metadata={})
                            parent_id = group.id
                        entity = await _entity(db, provider="YANDEX_DIRECT", entity_type=entity_type, provider_entity_id=provider_id, parent_id=parent_id, display_name=row.get("AdGroupName") or row.get("LocationOfPresenceName") or dimension_value, status=None, metadata={key: row.get(key) for key in spec["fields"] if key in row and key not in {"Impressions", "Clicks", "Cost", "Conversions"}})
                        entity_id = entity.id
                    logical_key = hashlib.sha256(f"YANDEX_DIRECT|{report.report_type}|{collection_run_id}|{dimension}|{entity_id or ''}|{dimension_value or ''}|{day}".encode()).hexdigest()
                    db.add(AdsV2Observation(snapshot_id=snapshot.id, entity_id=entity_id, dimension_type=dimension, dimension_value=dimension_value, window="1d", date_from=date.fromisoformat(str(day)), date_to=date.fromisoformat(str(day)), impressions=_metric(row, "Impressions"), clicks=_metric(row, "Clicks"), cost=_metric(row, "Cost"), conversions=_metric(row, "Conversions"), report_run_id=report_run.id, collection_run_id=collection_run_id, logical_key=logical_key, provenance_json=json.dumps({"provider": "YANDEX_DIRECT", "report_type": report.report_type, "provider_report_id": report.provider_report_id, "source_fields": report.requested_fields, "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "timezone": "Europe/Moscow", "collection_run_id": collection_run_id, "fetched_at": (report.fetched_at or fetched_at).isoformat(), "snapshot_at": snapshot.created_at.isoformat() if snapshot.created_at else None, "freshness_caveat": report.freshness_caveat}, ensure_ascii=False)))
                    key = (entity_id, dimension_value, date.fromisoformat(str(day)))
                    daily.setdefault(key, {metric: 0.0 for metric in ("impressions", "clicks", "cost", "conversions")})
                    for source, target in (("Impressions", "impressions"), ("Clicks", "clicks"), ("Cost", "cost"), ("Conversions", "conversions")):
                        if row.get(source) not in (None, "", "-"):
                            daily[key][target] += float(row[source])
                    results["rows"] += 1
                for (entity_id, dimension_value, day), values in daily.items():
                    await _upsert_daily_aggregate(db, campaign_id=campaign.id, entity_id=entity_id, dimension_type=dimension, dimension_value=dimension_value, aggregate_date=day, values=values, source_ids=[report_run.id], source_from=report.fetched_at or fetched_at, source_to=report.fetched_at or fetched_at)
            except Exception as exc:
                failed_run = AdsV2ReportRun(provider="YANDEX_DIRECT", report_type=spec["report_type"], campaign_provider_id=campaign.external_id, date_from=date_from, date_to=date_to, timezone="Europe/Moscow", requested_fields_json=json.dumps(spec["fields"]), request_hash=hashlib.sha256(f"{collection_run_id}|{dimension}".encode()).hexdigest(), fetched_at=datetime.now(timezone.utc), row_count=0, parser_version="v1", status="FAILED", error_code=type(exc).__name__)
                db.add(failed_run)
                results["errors"].append({"dimension": dimension, "error_code": type(exc).__name__})
                results["reports"][dimension] = {"accepted": False, "error_code": type(exc).__name__}
    finally:
        await provider.close()
    return results
