"""Fail-closed, server-side, read-only ADS timeline collector.

This module intentionally has no AI/Codex imports and never creates
``AdsAiRun`` or recommendations.  A collection run is an idempotent slot,
so retries cannot create duplicate logical collection points.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ads.facts import build_campaign_facts
from app.ads.v2 import _entity, collect_granular_yandex_v2, collect_yandex_v2
from app.core.config import settings
from app.db.models import (
    Admin, AdsAccount, AdsCampaign, AdsDataHealth, AdsPromotionCampaignLink,
    AdsSnapshot, AdsV2CollectionRun, BotPromotionProfile, Channel,
)
from app.integrations.yandex_oauth import get_encrypted_token
from app.security_tokens import decrypt_token
from app.traffic.analytics import traffic_stats_for_channels

MOSCOW = __import__("zoneinfo").ZoneInfo("Europe/Moscow")
MAX_ATTEMPTS = 3
logger = logging.getLogger(__name__)


def _entity_config_rows(report) -> list[dict]:
    """Extract the provider rows without treating ProviderReport as iterable."""
    rows = getattr(report, "rows", None)
    if not isinstance(rows, list):
        raise TypeError("ENTITY_CONFIG_ROWS_NOT_LIST")
    if any(not isinstance(row, dict) for row in rows):
        raise TypeError("ENTITY_CONFIG_ROW_NOT_OBJECT")
    return rows


def _interval(collection_type: str) -> int:
    return {
        "CAMPAIGN": settings.ads_collection_campaign_interval_minutes,
        "GRANULAR": settings.ads_collection_granular_interval_minutes,
        "SEARCH_QUERY": settings.ads_collection_search_query_interval_minutes,
        "ENTITY_CONFIG": settings.ads_collection_entity_sync_interval_minutes,
    }[collection_type]


def _slot_time(now: datetime, campaign_id: str, collection_type: str) -> datetime:
    """Return a deterministic cadence slot with stable intra-window jitter."""
    minutes = max(1, _interval(collection_type))
    epoch = int(now.timestamp()) // (minutes * 60) * minutes * 60
    base = datetime.fromtimestamp(epoch, timezone.utc)
    # Spread campaigns through the window; this is deterministic across worker
    # restarts and avoids an HH:00 burst.
    jitter = int(hashlib.sha256(f"{campaign_id}:{collection_type}".encode()).hexdigest()[:8], 16) % max(1, minutes)
    return base + timedelta(minutes=jitter)


async def ensure_due_slots(db: AsyncSession, *, now: datetime | None = None, limit: int = 200) -> int:
    if not settings.ads_periodic_collection_enabled:
        return 0
    now = now or datetime.now(timezone.utc)
    rows = list((await db.scalars(
        select(AdsCampaign)
        .join(AdsPromotionCampaignLink, AdsPromotionCampaignLink.campaign_id == AdsCampaign.id)
        .join(AdsAccount, AdsAccount.id == AdsCampaign.account_id)
        .where(AdsPromotionCampaignLink.status == "ACTIVE", AdsAccount.provider == "YANDEX_DIRECT", AdsAccount.status == "CONNECTED", AdsCampaign.state != "ARCHIVED")
        .distinct().limit(limit)
    )).all())
    created = 0
    for campaign in rows:
        collection_types = ["CAMPAIGN", "GRANULAR", "ENTITY_CONFIG"]
        if settings.ads_collection_search_query_enabled:
            collection_types.append("SEARCH_QUERY")
        for collection_type in collection_types:
            scheduled = _slot_time(now, campaign.id, collection_type)
            # Only materialize slots that are due; future slots are created on
            # the next scheduler tick, keeping the table bounded.
            if scheduled > now:
                continue
            key = f"YANDEX_DIRECT:{campaign.account_id}:{campaign.id}:{collection_type}:{scheduled.isoformat()}:v1"
            exists = await db.scalar(select(AdsV2CollectionRun.id).where(AdsV2CollectionRun.slot_key == key))
            if not exists:
                db.add(AdsV2CollectionRun(provider="YANDEX_DIRECT", account_id=campaign.account_id, campaign_id=campaign.id, collection_type=collection_type, scheduled_at=scheduled, slot_key=key))
                created += 1
    await db.commit()
    return created


async def claim_due_slots(db: AsyncSession, *, now: datetime | None = None, limit: int | None = None) -> list[AdsV2CollectionRun]:
    now = now or datetime.now(timezone.utc)
    limit = limit or settings.ads_collection_max_slots_per_cycle
    async with db.begin():
        rows = list((await db.scalars(
            select(AdsV2CollectionRun).where(
                AdsV2CollectionRun.scheduled_at <= now,
                or_(AdsV2CollectionRun.status == "PENDING", AdsV2CollectionRun.status == "RETRYABLE_FAILED"),
                or_(AdsV2CollectionRun.next_retry_at.is_(None), AdsV2CollectionRun.next_retry_at <= now),
            ).order_by(AdsV2CollectionRun.scheduled_at).with_for_update(skip_locked=True).limit(limit)
        )).all())
        for row in rows:
            row.status, row.started_at = "PROCESSING", now
            row.attempts += 1
    return rows


async def _health(db: AsyncSession, campaign: AdsCampaign, *, status: str, reason: str) -> None:
    links = list((await db.scalars(select(AdsPromotionCampaignLink.channel_id).where(AdsPromotionCampaignLink.campaign_id == campaign.id, AdsPromotionCampaignLink.object_type == "CHANNEL", AdsPromotionCampaignLink.status == "ACTIVE", AdsPromotionCampaignLink.channel_id.is_not(None)))).all())
    for channel_id in links:
        db.add(AdsDataHealth(channel_id=channel_id, status=status, reasons_json=json.dumps({"periodic_collection": reason}, ensure_ascii=False)))


async def _collection_context(db: AsyncSession, run: AdsV2CollectionRun):
    campaign = await db.get(AdsCampaign, run.campaign_id)
    if not campaign:
        raise ValueError("COLLECTION_CAMPAIGN_NOT_FOUND")
    link = await db.scalar(select(AdsPromotionCampaignLink).where(AdsPromotionCampaignLink.campaign_id == campaign.id, AdsPromotionCampaignLink.status == "ACTIVE"))
    channel = await db.get(Channel, link.channel_id) if link and link.object_type == "CHANNEL" and link.channel_id else None
    admin_external_id = await db.scalar(select(Admin.external_id).where(Admin.id == channel.admin_id)) if channel else None
    encrypted = await get_encrypted_token(str(admin_external_id), channel.id, db=db) if channel and admin_external_id else None
    if link and link.object_type == "BOT" and link.bot_promotion_profile_id:
        profile = await db.get(BotPromotionProfile, link.bot_promotion_profile_id)
        encrypted = profile.yandex_oauth_token_encrypted if profile else None
    if not encrypted or not link:
        raise ValueError("COLLECTION_PROVIDER_CREDENTIALS_UNAVAILABLE")
    today = datetime.now(timezone.utc).astimezone(MOSCOW).date()
    return campaign, channel, encrypted, today


async def collect_campaign_slot(db: AsyncSession, run: AdsV2CollectionRun) -> None:
    campaign, channel, encrypted, today = await _collection_context(db, run)
    date_from = today - timedelta(days=6)
    from app.ads.providers.yandex import YandexAdsProvider
    provider = YandexAdsProvider(decrypt_token(encrypted))
    try:
        report = await provider.campaign_performance(int(campaign.external_id), date_from=date_from, date_to=today)
    finally:
        await provider.close()
    fetched_at = report.fetched_at or datetime.now(timezone.utc)
    traffic = await traffic_stats_for_channels(db, [channel.id]) if channel else {}
    facts = build_campaign_facts(report.rows, campaign_start=None, traffic=traffic, now=fetched_at)
    snapshot = AdsSnapshot(channel_id=channel.id if channel else None, campaign_id=campaign.id, source="PERIODIC_COLLECTION", period_from=date_from, period_to=today, facts_json=json.dumps({"campaign": {"id": campaign.id, "name": campaign.name}, "facts": facts.context(report_date_from=date_from, report_date_to=today, report_fetched_at=fetched_at, snapshot_at=fetched_at), "collection_run_id": run.id}, ensure_ascii=False))
    db.add(snapshot)
    await db.flush()
    await collect_yandex_v2(db, token=decrypt_token(encrypted), campaign=campaign, snapshot=snapshot, report_rows=report.rows, date_from=date_from, date_to=today, fetched_at=fetched_at, collection_run_id=run.id)
    run.snapshot_id, run.status, run.completed_at = snapshot.id, "COMPLETED", datetime.now(timezone.utc)
    run.freshness_caveat = "current calendar day may be incomplete or delayed"
    await _health(db, campaign, status="HEALTHY", reason="PERIODIC_COLLECTION_OK")


async def collect_granular_slot(db: AsyncSession, run: AdsV2CollectionRun) -> None:
    campaign, channel, encrypted, today = await _collection_context(db, run)
    date_from = today - timedelta(days=6)
    snapshot = AdsSnapshot(channel_id=channel.id if channel else None, campaign_id=campaign.id, source="PERIODIC_GRANULAR", period_from=date_from, period_to=today, facts_json=json.dumps({"campaign": {"id": campaign.id, "name": campaign.name}, "collection_run_id": run.id}, ensure_ascii=False))
    db.add(snapshot)
    await db.flush()
    result = await collect_granular_yandex_v2(db, token=decrypt_token(encrypted), campaign=campaign, snapshot=snapshot, date_from=date_from, date_to=today, fetched_at=datetime.now(timezone.utc), collection_run_id=run.id, dimensions=("SEARCH_QUERY",) if run.collection_type == "SEARCH_QUERY" else None)
    run.snapshot_id, run.completed_at = snapshot.id, datetime.now(timezone.utc)
    run.status = "PARTIAL" if result["errors"] else "COMPLETED"
    run.freshness_caveat = "one or more granular subreports failed" if result["errors"] else "current calendar day may be incomplete or delayed"
    await _health(db, campaign, status="DEGRADED" if result["errors"] else "HEALTHY", reason="PERIODIC_GRANULAR_PARTIAL" if result["errors"] else "PERIODIC_GRANULAR_OK")


async def collect_entity_configuration(db: AsyncSession, run: AdsV2CollectionRun) -> None:
    campaign, channel, encrypted, _ = await _collection_context(db, run)
    from app.ads.providers.yandex import YandexAdsProvider
    provider = YandexAdsProvider(decrypt_token(encrypted))
    try:
        campaigns = await provider.campaigns([int(campaign.external_id)])
        ads_report = await provider.ads([int(campaign.external_id)])
    finally:
        await provider.close()
    campaign_entity = await _entity(db, provider="YANDEX_DIRECT", entity_type="CAMPAIGN", provider_entity_id=campaign.external_id, parent_id=None, display_name=campaign.name, status=campaign.status, metadata={"state": campaign.state})
    for raw in _entity_config_rows(ads_report):
        provider_entity_id = raw.get("Id")
        if provider_entity_id in (None, ""):
            raise ValueError("ENTITY_CONFIG_AD_ID_MISSING")
        await _entity(db, provider="YANDEX_DIRECT", entity_type="AD", provider_entity_id=str(provider_entity_id), parent_id=campaign_entity.id, display_name=None, status=raw.get("Status"), metadata=raw)
    run.status, run.completed_at = "COMPLETED", datetime.now(timezone.utc)
    run.freshness_caveat = "metadata configuration refreshed"


async def collect_one_slot(db: AsyncSession, run: AdsV2CollectionRun) -> None:
    if run.collection_type == "CAMPAIGN":
        await collect_campaign_slot(db, run)
    elif run.collection_type in {"GRANULAR", "SEARCH_QUERY"}:
        await collect_granular_slot(db, run)
    elif run.collection_type == "ENTITY_CONFIG":
        await collect_entity_configuration(db, run)
    else:
        raise ValueError("COLLECTION_TYPE_UNSUPPORTED")


async def process_periodic_collection(session_factory) -> int:
    if not settings.ads_periodic_collection_enabled:
        return 0
    async with session_factory() as db:
        created = await ensure_due_slots(db)
        due = await claim_due_slots(db)
        ids = [run.id for run in due]
    logger.info("ADS_V2_PERIODIC_COLLECTION_TICK created=%s claimed=%s", created, len(ids))
    processed = 0
    for run_id in ids:
        async with session_factory() as db:
            run = await db.get(AdsV2CollectionRun, run_id)
            if not run or run.status != "PROCESSING":
                continue
            try:
                await collect_one_slot(db, run)
                await db.commit()
                processed += 1
            except Exception as exc:
                run.status = "RETRYABLE_FAILED" if run.attempts < MAX_ATTEMPTS else "FAILED"
                run.error_code = type(exc).__name__
                run.next_retry_at = datetime.now(timezone.utc) + timedelta(minutes=min(60, 5 * run.attempts)) if run.status == "RETRYABLE_FAILED" else None
                await db.commit()
    return processed


async def collection_health(db: AsyncSession, *, now: datetime | None = None) -> dict[str, object]:
    """Return sanitized scheduler diagnostics for an admin/status surface."""
    now = now or datetime.now(timezone.utc)
    last = await db.scalar(select(AdsV2CollectionRun).where(AdsV2CollectionRun.status == "COMPLETED").order_by(AdsV2CollectionRun.completed_at.desc()).limit(1))
    next_run = await db.scalar(select(AdsV2CollectionRun).where(AdsV2CollectionRun.status.in_(("PENDING", "RETRYABLE_FAILED"))).order_by(AdsV2CollectionRun.scheduled_at).limit(1))
    if not next_run:
        # The scheduler materializes due slots only.  For a compact admin
        # diagnostic, calculate the next campaign slot without inserting it.
        eligible_campaigns = list((await db.scalars(select(AdsCampaign).join(AdsPromotionCampaignLink, AdsPromotionCampaignLink.campaign_id == AdsCampaign.id).join(AdsAccount, AdsAccount.id == AdsCampaign.account_id).where(AdsPromotionCampaignLink.status == "ACTIVE", AdsAccount.provider == "YANDEX_DIRECT", AdsAccount.status == "CONNECTED", AdsCampaign.state != "ARCHIVED").distinct())).all())
        candidates = [_slot_time(now if _slot_time(now, c.id, "CAMPAIGN") > now else now + timedelta(minutes=settings.ads_collection_campaign_interval_minutes), c.id, "CAMPAIGN") for c in eligible_campaigns]
        if candidates:
            next_run = type("NextSlot", (), {"scheduled_at": min(candidates)})()
    from sqlalchemy import func
    eligible = await db.scalar(
        select(func.count(func.distinct(AdsCampaign.id)))
        .select_from(AdsCampaign)
        .join(AdsPromotionCampaignLink, AdsPromotionCampaignLink.campaign_id == AdsCampaign.id)
        .join(AdsAccount, AdsAccount.id == AdsCampaign.account_id)
        .where(AdsPromotionCampaignLink.status == "ACTIVE", AdsAccount.provider == "YANDEX_DIRECT", AdsAccount.status == "CONNECTED", AdsCampaign.state != "ARCHIVED")
    )
    retry_backlog = await db.scalar(select(func.count(AdsV2CollectionRun.id)).where(AdsV2CollectionRun.status == "RETRYABLE_FAILED"))
    failed_runs = await db.scalar(select(func.count(AdsV2CollectionRun.id)).where(AdsV2CollectionRun.status == "FAILED"))
    by_type: dict[str, dict[str, object]] = {}
    for collection_type in ("CAMPAIGN", "GRANULAR", "ENTITY_CONFIG", "SEARCH_QUERY"):
        type_last = await db.scalar(select(AdsV2CollectionRun).where(AdsV2CollectionRun.collection_type == collection_type, AdsV2CollectionRun.status == "COMPLETED").order_by(AdsV2CollectionRun.completed_at.desc()).limit(1))
        type_next = await db.scalar(select(AdsV2CollectionRun).where(AdsV2CollectionRun.collection_type == collection_type, AdsV2CollectionRun.status.in_(("PENDING", "RETRYABLE_FAILED"))).order_by(AdsV2CollectionRun.scheduled_at).limit(1))
        by_type[collection_type] = {
            "last_successful": type_last.completed_at.isoformat() if type_last and type_last.completed_at else None,
            "next_due": type_next.scheduled_at.isoformat() if type_next else None,
        }
    # Staleness is a campaign-level health signal.  Do not join arbitrary
    # collection rows here: an older GRANULAR or ENTITY_CONFIG row must not
    # make a fresh hourly CAMPAIGN collection look stale.  The correlated
    # existence check also avoids counting a campaign once per historical run.
    recent_campaign = select(AdsV2CollectionRun.id).where(
        AdsV2CollectionRun.campaign_id == AdsCampaign.id,
        AdsV2CollectionRun.collection_type == "CAMPAIGN",
        AdsV2CollectionRun.status == "COMPLETED",
        AdsV2CollectionRun.completed_at >= now - timedelta(minutes=settings.ads_collection_campaign_interval_minutes * 2),
    )
    stale = await db.scalar(
        select(func.count(func.distinct(AdsCampaign.id)))
        .select_from(AdsCampaign)
        .join(AdsPromotionCampaignLink, AdsPromotionCampaignLink.campaign_id == AdsCampaign.id)
        .join(AdsAccount, AdsAccount.id == AdsCampaign.account_id)
        .where(AdsPromotionCampaignLink.status == "ACTIVE", AdsAccount.provider == "YANDEX_DIRECT", AdsAccount.status == "CONNECTED", AdsCampaign.state != "ARCHIVED", ~exists(recent_campaign))
    )
    return {
        "enabled": settings.ads_periodic_collection_enabled,
        "last_successful_collection": last.completed_at.isoformat() if last and last.completed_at else None,
        "next_due_collection": next_run.scheduled_at.isoformat() if next_run else None,
        "eligible_campaigns": int(eligible or 0),
        "stale_campaigns": int(stale or 0),
        "retry_backlog": int(retry_backlog or 0),
        "failed_runs": int(failed_runs or 0),
        "by_type": by_type,
        "now": now.isoformat(),
    }
