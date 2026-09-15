"""Small provider-independent V2 persistence layer for read-only observations."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ads.providers.yandex import GRANULAR_REPORTS, YandexAdsProvider
from app.ads.creative_storage import store_media_if_available
from app.ads.creative_storage import MailCloudWebDav, media_key
from app.ads.yandex_creative_media import archive_resolved_media, extract_media_references, resolve_media
from app.core.config import settings
from app.db.models import AdsAnalysisSnapshot, AdsCampaign, AdsCreative, AdsDailyAggregate, AdsEntity, AdsObservation, AdsReportRun

WINDOWS = {"1d": 1, "3d": 3, "7d": 7}


def _media_identity(raw: dict[str, Any]) -> dict[str, Any]:
    refs = extract_media_references(raw)
    return {
        "ad_image_hash": refs.get("ad_image_hash"),
        "ad_image_hashes": refs.get("ad_image_hashes") or [],
        "provider_creative_id": refs.get("provider_creative_id"),
        "video_extension": refs.get("video_extension"),
        "media_kind": refs.get("media_kind"),
    }


def _stored_media_matches(creative: AdsCreative | None, raw: dict[str, Any]) -> bool:
    if not creative or creative.media_storage_state != "STORED" or not creative.storage_key:
        return False
    try:
        metadata = json.loads(creative.provider_metadata_json or "{}")
    except (TypeError, ValueError):
        return False
    previous = metadata.get("media_identity") or {}
    current = _media_identity(raw)
    return bool(previous) and previous == current


def _retry_allowed(creative: AdsCreative | None, *, now: datetime) -> tuple[bool, int]:
    if not creative or creative.media_storage_state != "FAILED":
        return True, 0
    try:
        metadata = json.loads(creative.provider_metadata_json or "{}")
        retry = metadata.get("media_retry") or {}
        attempts = int(retry.get("attempts") or 0)
        retry_after = datetime.fromisoformat(retry["retry_after"]) if retry.get("retry_after") else None
    except (TypeError, ValueError, KeyError):
        return True, 0
    return attempts < 3 and (retry_after is None or retry_after <= now), attempts


async def enrich_periodic_ad_media(db: AsyncSession, *, campaign: AdsCampaign, raw: dict[str, Any], provider_client: Any, fetched_at: datetime) -> None:
    """Reuse the canonical resolver for periodic entity collection.

    Existing stored media is skipped by provider identity; missing/failed media
    is resolved in the same bounded path used by the normal ADS collector.
    """
    external_id = str(raw.get("Id") or "")
    if not external_id or not any(_media_identity(raw).get(key) for key in ("ad_image_hash", "ad_image_hashes", "provider_creative_id", "video_extension")):
        return
    current_identity = _media_identity(raw)
    creative = await db.scalar(select(AdsCreative).where(AdsCreative.campaign_id == campaign.id, AdsCreative.external_creative_id == external_id))
    retry_allowed, retry_attempts = _retry_allowed(creative, now=fetched_at)
    if not retry_allowed:
        return
    if creative and creative.media_storage_state == "STORED" and creative.storage_key:
        try:
            metadata = json.loads(creative.provider_metadata_json or "{}")
        except (TypeError, ValueError):
            metadata = {}
        identity_matches = _stored_media_matches(creative, raw)
        if not identity_matches and not metadata.get("media_identity"):
            identity_matches = creative.image_ref == current_identity.get("ad_image_hash") or creative.video_ref == current_identity.get("provider_creative_id")
        if identity_matches and "/sha256/" in creative.storage_key:
            match = re.search(r"/([0-9a-f]{64})\.([A-Za-z0-9_-]+)$", creative.storage_key)
            if match:
                digest, extension = match.groups()
                new_key = media_key(digest, extension, mime_type=creative.mime_type)
                storage = MailCloudWebDav()
                try:
                    migrated = await storage.copy_legacy_verified(creative.storage_key, new_key, expected_hash=digest, expected_size=int(creative.file_size or 0), content_type=creative.mime_type or "application/octet-stream")
                    raw["_media_archive"] = {"state": "STORED", "storage_key": migrated.key, "file_size": migrated.size, "content_hash": migrated.content_hash}
                    raw["_media_retrieval"] = {**current_identity, "provider": "YANDEX_DIRECT", "result": "MIGRATED", "media_source_type": "ORIGINAL"}
                except Exception as exc:
                    raw["_storage_migration_error"] = type(exc).__name__
                finally:
                    await storage.close()
                if raw.get("_media_archive", {}).get("state") == "STORED":
                    return
        if identity_matches:
            return
    try:
        raw["_media_retrieval"] = await resolve_media(provider_client, raw)
        raw["_media_archive"] = await archive_resolved_media(raw["_media_retrieval"])
        if raw["_media_archive"].get("state") == "FAILED":
            raw["_media_retry"] = {"attempts": retry_attempts + 1, "retry_after": (fetched_at + timedelta(minutes=15 * (retry_attempts + 1))).isoformat()}
        else:
            raw["_media_retry"] = {"attempts": 0, "retry_after": None}
    except Exception as exc:
        raw["_media_retrieval"] = {"provider": "YANDEX_DIRECT", "provider_ad_id": raw.get("Id"), "result": "FAILED", "error": type(exc).__name__}
        raw["_media_archive"] = {"state": "FAILED", "storage_key": None, "file_size": None, "error": type(exc).__name__}
        raw["_media_retry"] = {"attempts": retry_attempts + 1, "retry_after": (fetched_at + timedelta(minutes=15 * (retry_attempts + 1))).isoformat()}


async def _upsert_daily_aggregate(db: AsyncSession, *, campaign_id: str, entity_id: str | None, dimension_type: str, dimension_value: str | None, aggregate_date: date, values: dict[str, float], source_ids: list[str], source_from: datetime | None, source_to: datetime | None, source_kind: str = "PERIODIC") -> str:
    aggregate = await db.scalar(select(AdsDailyAggregate).where(
        AdsDailyAggregate.provider == "YANDEX_DIRECT",
        AdsDailyAggregate.campaign_id == campaign_id,
        AdsDailyAggregate.entity_id == entity_id,
        AdsDailyAggregate.dimension_type == dimension_type,
        AdsDailyAggregate.dimension_value == dimension_value,
        AdsDailyAggregate.aggregate_date == aggregate_date,
    ))
    payload = {metric: float(values.get(metric) or 0) for metric in ("impressions", "clicks", "cost", "conversions")}
    provenance = json.dumps({"provider": "YANDEX_DIRECT", "source": source_kind, "campaign_id": campaign_id, "entity_type": dimension_type, "entity_id": entity_id, "dimension_value": dimension_value, "date": aggregate_date.isoformat(), "timezone": "Europe/Moscow", "source_report_run_ids": list(dict.fromkeys(source_ids)), "metric_values": payload, "fetched_at": (source_to or source_from).isoformat() if (source_to or source_from) else None, "aggregation": "sum exact Date rows; current day excluded from confirmed trends", "aggregation_version": "trend_intelligence_v1"}, ensure_ascii=False)
    result = "updated" if aggregate else "inserted"
    if not aggregate:
        aggregate = AdsDailyAggregate(provider="YANDEX_DIRECT", campaign_id=campaign_id, entity_id=entity_id, dimension_type=dimension_type, dimension_value=dimension_value, aggregate_date=aggregate_date, source_observation_count=0)
        db.add(aggregate)
    aggregate.impressions, aggregate.clicks, aggregate.cost, aggregate.conversions = payload["impressions"], payload["clicks"], payload["cost"], payload["conversions"]
    aggregate.source_observation_count = len(source_ids)
    aggregate.source_from, aggregate.source_to, aggregate.provenance_json = source_from, source_to, provenance
    return result


async def _entity(db: AsyncSession, *, provider: str, entity_type: str, provider_entity_id: str, parent_id: str | None, display_name: str | None, status: str | None, metadata: dict[str, Any], campaign_id: str | None = None) -> AdsEntity:
    entity = await db.scalar(select(AdsEntity).where(AdsEntity.provider == provider, AdsEntity.entity_type == entity_type, AdsEntity.provider_entity_id == provider_entity_id))
    metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    content_hash = hashlib.sha256(metadata_json.encode()).hexdigest()
    if not entity:
        entity = AdsEntity(provider=provider, entity_type=entity_type, provider_entity_id=provider_entity_id, external_entity_id=provider_entity_id, campaign_id=campaign_id, parent_id=parent_id, display_name=display_name, status=status, content_hash=content_hash, metadata_updated_at=datetime.now(timezone.utc), provider_metadata_json=metadata_json)
        db.add(entity)
    else:
        entity.parent_id, entity.display_name, entity.status, entity.campaign_id = parent_id, display_name, status, campaign_id or entity.campaign_id
        if entity.content_hash != content_hash:
            entity.provider_metadata_json = metadata_json
            entity.content_hash = content_hash
            entity.metadata_updated_at = datetime.now(timezone.utc)
    await db.flush()
    return entity


async def _upsert_creative(db: AsyncSession, *, campaign: AdsCampaign, ad_entity: AdsEntity, raw: dict[str, Any], fetched_at: datetime) -> AdsCreative:
    text_ad = raw.get("TextAd") if isinstance(raw.get("TextAd"), dict) else raw
    values = {
        "title": text_ad.get("Title"), "title2": text_ad.get("Title2"),
        "body": text_ad.get("Text"), "href": text_ad.get("Href"),
        "display_url": text_ad.get("DisplayUrlPath"),
        "creative_type": raw.get("Type") or raw.get("Subtype"),
        "cta": text_ad.get("Callout") or text_ad.get("ButtonText"),
        "image_ref": text_ad.get("ImageHash") or text_ad.get("Image"),
        "video_ref": text_ad.get("VideoId") or text_ad.get("Video"),
        "source_type": "PROVIDER_IMPORTED",
        "storage_provider": "MAIL_RU_WEBDAV",
        "media_storage_state": "SOURCE_UNAVAILABLE" if (text_ad.get("ImageHash") or text_ad.get("Image") or text_ad.get("VideoId") or text_ad.get("Video")) else "AVAILABLE_NOT_STORED",
        "ai_features_json": "{}",
        "last_seen_at": fetched_at,
        "provider_state": raw.get("State") or raw.get("Status"),
    }
    retrieval = raw.get("_media_retrieval") if isinstance(raw.get("_media_retrieval"), dict) else None
    archive = raw.get("_media_archive") if isinstance(raw.get("_media_archive"), dict) else None
    if retrieval:
        values["image_ref"] = retrieval.get("ad_image_hash") or values["image_ref"]
        values["video_ref"] = retrieval.get("provider_creative_id") or values["video_ref"]
        values["media_storage_state"] = (archive or {}).get("state") or (
            "SOURCE_UNAVAILABLE" if retrieval.get("result") == "UNAVAILABLE" else "FAILED" if retrieval.get("result") == "FAILED" else "AVAILABLE_NOT_STORED"
        )
    media_bytes = raw.get("media_bytes") if isinstance(raw.get("media_bytes"), bytes) else None
    if media_bytes:
        media_result = await store_media_if_available(
            media_bytes,
            mime_type=raw.get("mime_type") or "application/octet-stream",
            extension=str(raw.get("media_extension") or "bin"),
        )
        values.update({
            "media_storage_state": media_result["state"],
            "storage_key": media_result.get("storage_key"),
            "file_size": media_result.get("file_size"),
        })
    if archive:
        values.update({"storage_key": archive.get("storage_key"), "file_size": archive.get("file_size"), "media_storage_state": archive.get("state")})
        if archive.get("content_hash"):
            values["image_ref"] = values.get("image_ref") or archive.get("content_hash")
        if raw.get("_media_retrieval", {}).get("mime_type"):
            values["mime_type"] = raw["_media_retrieval"]["mime_type"]
    content_hash = hashlib.sha256(json.dumps(
        {key: values[key] for key in ("title", "title2", "body", "href", "display_url", "creative_type", "cta", "image_ref", "video_ref")},
        ensure_ascii=False,
        sort_keys=True,
    ).encode()).hexdigest()
    external_id = str(raw.get("Id") or "") or None
    creative_query = select(AdsCreative).where(AdsCreative.campaign_id == campaign.id)
    if external_id:
        creative_query = creative_query.where(AdsCreative.external_creative_id == external_id)
    else:
        creative_query = creative_query.where(AdsCreative.content_hash == content_hash, AdsCreative.external_creative_id.is_(None))
    creative = await db.scalar(creative_query)
    if not creative:
        metadata = {key: raw.get(key) for key in ("Id", "CampaignId", "Type", "Subtype", "State", "Status", "StatusClarification")}
        if retrieval:
            metadata["media_provenance"] = {key: retrieval.get(key) for key in ("provider", "provider_ad_id", "provider_creative_id", "ad_image_hash", "video_extension", "provider_media_reference", "media_source_type", "result", "original_retrievable", "preview_retrievable", "error")}
            metadata["media_identity"] = _media_identity(raw)
            if raw.get("_media_retry"):
                metadata["media_retry"] = raw["_media_retry"]
        creative = AdsCreative(campaign_id=campaign.id, ad_entity_id=ad_entity.id, external_creative_id=external_id, provider_metadata_json=json.dumps(metadata, ensure_ascii=False), content_hash=content_hash, metadata_updated_at=fetched_at, created_at=fetched_at, updated_at=fetched_at, **values)
        db.add(creative)
    elif creative.content_hash != content_hash:
        for key, value in values.items():
            setattr(creative, key, value)
        creative.ad_entity_id = ad_entity.id
        creative.content_hash = content_hash
        creative.metadata_updated_at = fetched_at
        creative.updated_at = fetched_at
        if retrieval:
            metadata = json.loads(creative.provider_metadata_json or "{}")
            metadata["media_provenance"] = {key: retrieval.get(key) for key in ("provider", "provider_ad_id", "provider_creative_id", "ad_image_hash", "video_extension", "provider_media_reference", "media_source_type", "result", "original_retrievable", "preview_retrievable", "error")}
            metadata["media_identity"] = _media_identity(raw)
            creative.provider_metadata_json = json.dumps(metadata, ensure_ascii=False)
    else:
        creative.last_seen_at = fetched_at
        creative.provider_state = values["provider_state"]
        if retrieval or archive:
            for key in ("storage_key", "file_size", "media_storage_state", "mime_type"):
                if key in values and values[key] is not None:
                    setattr(creative, key, values[key])
            metadata = json.loads(creative.provider_metadata_json or "{}")
            if retrieval:
                metadata["media_provenance"] = {key: retrieval.get(key) for key in ("provider", "provider_ad_id", "provider_creative_id", "ad_image_hash", "video_extension", "provider_media_reference", "media_source_type", "result", "original_retrievable", "preview_retrievable", "error")}
                metadata["media_identity"] = _media_identity(raw)
            if raw.get("_media_retry"):
                metadata["media_retry"] = raw["_media_retry"]
            creative.provider_metadata_json = json.dumps(metadata, ensure_ascii=False)
    await db.flush()
    return creative


async def collect_yandex_v2(db: AsyncSession, *, token: str, campaign: AdsCampaign, snapshot: AdsAnalysisSnapshot | None, report_rows: list[dict[str, Any]], date_from: date, date_to: date, fetched_at: datetime, collection_run_id: str | None = None, include_entity_metadata: bool = True) -> dict[str, Any]:
    """Persist available campaign/ad metadata and campaign observations.

    Yandex's currently used integration exposes campaign performance and ad
    metadata only. Unsupported dimensions are reported explicitly rather than
    represented by invented rows.
    """
    provider = YandexAdsProvider(token, client_login=settings.yandex_direct_client_login)
    fields = ("Date", "CampaignId", "CampaignName", "Impressions", "Clicks", "Cost", "Conversions")
    request_hash = hashlib.sha256(json.dumps({"campaign": campaign.external_id, "from": date_from.isoformat(), "to": date_to.isoformat(), "fields": fields}, sort_keys=True).encode()).hexdigest()
    report_run = AdsReportRun(provider="YANDEX_DIRECT", ads_account_id=campaign.account_id, report_type="CAMPAIGN_PERFORMANCE_REPORT", campaign_provider_id=campaign.external_id, date_from=date_from, date_to=date_to, timezone="Europe/Moscow", requested_fields_json=json.dumps(fields), request_hash=request_hash, fetched_at=fetched_at, row_count=len(report_rows), parser_version="v1", status="SUCCESS", freshness_caveat="current calendar day may be incomplete or delayed")
    db.add(report_run)
    await db.flush()
    campaign_entity = await _entity(db, provider="YANDEX_DIRECT", entity_type="CAMPAIGN", provider_entity_id=campaign.external_id, parent_id=None, display_name=campaign.name, status=campaign.status, metadata={"state": campaign.state}, campaign_id=campaign.id)
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
        db.add(AdsObservation(snapshot_id=snapshot.id if snapshot else None, entity_id=campaign_entity.id, dimension_type="CAMPAIGN", dimension_value=campaign.external_id, window=window, date_from=start, date_to=date_to, impressions=number("Impressions"), clicks=number("Clicks"), cost=number("Cost"), conversions=number("Conversions"), report_run_id=report_run.id, collection_run_id=collection_run_id, logical_key=logical_key, provenance_json=json.dumps({"provider": "YANDEX_DIRECT", "source_fields": fields, "date_from": start.isoformat(), "date_to": date_to.isoformat(), "timezone": "Europe/Moscow", "aggregation": "sum by Date", "report_run_id": report_run.id, "collection_run_id": collection_run_id, "fetched_at": fetched_at.isoformat(), "snapshot_at": snapshot.created_at.isoformat() if snapshot and snapshot.created_at else None}, ensure_ascii=False)))
    available_ads = 0
    if not include_entity_metadata:
        await provider.close()
        return {"entities": {"campaigns": 1, "ad_groups": 0, "ads": 0, "creatives": 0}, "dimensions": {"placements": "NOT_SUPPORTED", "targeting": "NOT_SUPPORTED", "devices": "NOT_SUPPORTED", "regions": "NOT_SUPPORTED", "time": "NOT_SUPPORTED"}, "report_runs": 1, "missing_data": ["AD_GROUPS", "PLACEMENTS", "TARGETING", "DEVICES", "REGIONS", "TIME"], "ai_context": {"rows_available": len(report_rows), "rows_included": min(len(report_rows), 50), "truncated": len(report_rows) > 50, "selection": "campaign metrics only"}}
    try:
        ads_report = await provider.ads([int(campaign.external_id)])
        ads_report_run = AdsReportRun(provider="YANDEX_DIRECT", ads_account_id=campaign.account_id, report_type="ADS", campaign_provider_id=campaign.external_id, timezone="Europe/Moscow", requested_fields_json=json.dumps(ads_report.requested_fields), fetched_at=ads_report.fetched_at or fetched_at, row_count=len(ads_report.rows), parser_version="v1", status=ads_report.status, error_code=ads_report.error_code)
        db.add(ads_report_run)
        await db.flush()
        for raw in ads_report.rows:
            media_refs = extract_media_references(raw)
            if any(media_refs.get(key) for key in ("AdImageHash", "CreativeId", "VideoExtension", "ThumbnailUrl", "PreviewUrl", "OriginalUrl")):
                try:
                    raw["_media_retrieval"] = await resolve_media(provider.client, raw)
                    raw["_media_archive"] = await archive_resolved_media(raw["_media_retrieval"])
                except Exception as exc:
                    raw["_media_retrieval"] = {"provider": "YANDEX_DIRECT", "provider_ad_id": raw.get("Id"), "result": "FAILED", "error": type(exc).__name__}
                    raw["_media_archive"] = {"state": "FAILED", "storage_key": None, "file_size": None}
            ad_entity = await _entity(db, provider="YANDEX_DIRECT", entity_type="AD", provider_entity_id=str(raw.get("Id")), parent_id=campaign_entity.id, display_name=None, status=raw.get("Status"), metadata={key: raw.get(key) for key in ads_report.requested_fields}, campaign_id=campaign.id)
            await _upsert_creative(db, campaign=campaign, ad_entity=ad_entity, raw=raw, fetched_at=ads_report.fetched_at or fetched_at)
        available_ads = len(ads_report.rows)
    finally:
        await provider.close()
    return {"entities": {"campaigns": 1, "ad_groups": 0, "ads": available_ads, "creatives": available_ads}, "dimensions": {"placements": "NOT_SUPPORTED", "targeting": "NOT_SUPPORTED", "devices": "NOT_SUPPORTED", "regions": "NOT_SUPPORTED", "time": "NOT_SUPPORTED"}, "report_runs": 2, "missing_data": ["AD_GROUPS", "PLACEMENTS", "TARGETING", "DEVICES", "REGIONS", "TIME"], "ai_context": {"rows_available": len(report_rows) + available_ads, "rows_included": min(len(report_rows), 50), "truncated": len(report_rows) > 50, "selection": "campaign metrics plus normalized creative metadata"}}


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


async def collect_granular_yandex_v2(db: AsyncSession, *, token: str, campaign: AdsCampaign, snapshot: AdsAnalysisSnapshot | None, date_from: date, date_to: date, fetched_at: datetime, collection_run_id: str, dimensions: tuple[str, ...] | None = None) -> dict[str, Any]:
    """Collect independent documented reports and persist daily observations."""
    provider = YandexAdsProvider(token, client_login=settings.yandex_direct_client_login)
    results: dict[str, Any] = {"reports": {}, "rows": 0, "errors": []}
    try:
        selected_dimensions = dimensions or tuple(d for d in GRANULAR_REPORTS if d != "SEARCH_QUERY")
        for dimension in selected_dimensions:
            spec = GRANULAR_REPORTS[dimension]
            try:
                report = await provider.granular_report(dimension, int(campaign.external_id), date_from=date_from, date_to=date_to)
                report_run = AdsReportRun(provider="YANDEX_DIRECT", ads_account_id=campaign.account_id, report_type=report.report_type, campaign_provider_id=campaign.external_id, date_from=date_from, date_to=date_to, timezone="Europe/Moscow", requested_fields_json=json.dumps(report.requested_fields), request_hash=hashlib.sha256(f"{collection_run_id}|{dimension}".encode()).hexdigest(), fetched_at=report.fetched_at or fetched_at, row_count=len(report.rows), parser_version="v1", status=report.status, error_code=report.error_code, freshness_caveat=report.freshness_caveat)
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
                            parent_id = (await db.scalar(select(AdsEntity.id).where(AdsEntity.provider == "YANDEX_DIRECT", AdsEntity.entity_type == "CAMPAIGN", AdsEntity.provider_entity_id == campaign.external_id)))
                        elif entity_type == "AD" and row.get("AdGroupId"):
                            group = await _entity(db, provider="YANDEX_DIRECT", entity_type="AD_GROUP", provider_entity_id=str(row["AdGroupId"]), parent_id=(await db.scalar(select(AdsEntity.id).where(AdsEntity.provider == "YANDEX_DIRECT", AdsEntity.entity_type == "CAMPAIGN", AdsEntity.provider_entity_id == campaign.external_id))), display_name=None, status=None, metadata={}, campaign_id=campaign.id)
                            parent_id = group.id
                        entity = await _entity(db, provider="YANDEX_DIRECT", entity_type=entity_type, provider_entity_id=provider_id, parent_id=parent_id, display_name=row.get("AdGroupName") or row.get("LocationOfPresenceName") or dimension_value, status=None, metadata={key: row.get(key) for key in spec["fields"] if key in row and key not in {"Impressions", "Clicks", "Cost", "Conversions"}}, campaign_id=campaign.id)
                        entity_id = entity.id
                    logical_key = hashlib.sha256(f"YANDEX_DIRECT|{report.report_type}|{collection_run_id}|{dimension}|{entity_id or ''}|{dimension_value or ''}|{day}".encode()).hexdigest()
                    db.add(AdsObservation(snapshot_id=snapshot.id if snapshot else None, entity_id=entity_id, dimension_type=dimension, dimension_value=dimension_value, window="1d", date_from=date.fromisoformat(str(day)), date_to=date.fromisoformat(str(day)), impressions=_metric(row, "Impressions"), clicks=_metric(row, "Clicks"), cost=_metric(row, "Cost"), conversions=_metric(row, "Conversions"), report_run_id=report_run.id, collection_run_id=collection_run_id, logical_key=logical_key, provenance_json=json.dumps({"provider": "YANDEX_DIRECT", "report_type": report.report_type, "provider_report_id": report.provider_report_id, "source_fields": report.requested_fields, "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "timezone": "Europe/Moscow", "collection_run_id": collection_run_id, "fetched_at": (report.fetched_at or fetched_at).isoformat(), "snapshot_at": snapshot.created_at.isoformat() if snapshot and snapshot.created_at else None, "freshness_caveat": report.freshness_caveat}, ensure_ascii=False)))
                    key = (entity_id, dimension_value, date.fromisoformat(str(day)))
                    daily.setdefault(key, {metric: 0.0 for metric in ("impressions", "clicks", "cost", "conversions")})
                    for source, target in (("Impressions", "impressions"), ("Clicks", "clicks"), ("Cost", "cost"), ("Conversions", "conversions")):
                        if row.get(source) not in (None, "", "-"):
                            daily[key][target] += float(row[source])
                    results["rows"] += 1
                for (entity_id, dimension_value, day), values in daily.items():
                    await _upsert_daily_aggregate(db, campaign_id=campaign.id, entity_id=entity_id, dimension_type=dimension, dimension_value=dimension_value, aggregate_date=day, values=values, source_ids=[report_run.id], source_from=report.fetched_at or fetched_at, source_to=report.fetched_at or fetched_at)
            except Exception as exc:
                failed_run = AdsReportRun(provider="YANDEX_DIRECT", ads_account_id=campaign.account_id, report_type=spec["report_type"], campaign_provider_id=campaign.external_id, date_from=date_from, date_to=date_to, timezone="Europe/Moscow", requested_fields_json=json.dumps(spec["fields"]), request_hash=hashlib.sha256(f"{collection_run_id}|{dimension}".encode()).hexdigest(), fetched_at=datetime.now(timezone.utc), row_count=0, parser_version="v1", status="FAILED", error_code=type(exc).__name__)
                db.add(failed_run)
                results["errors"].append({"dimension": dimension, "error_code": type(exc).__name__})
                results["reports"][dimension] = {"accepted": False, "error_code": type(exc).__name__}
    finally:
        await provider.close()
    return results
