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

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ads.v2 import _entity, _upsert_creative, collect_granular_yandex_v2, collect_yandex_v2, enrich_periodic_ad_media
from app.ads.storage_governance import run_retention
from app.core.config import settings
from app.db.models import (
    Admin, AdsAccount, AdsCampaign, AdsDataHealth, AdsPromotionCampaignLink,
    AdsCollectionRun, BotPromotionProfile, Channel,
)
from app.integrations.yandex_oauth import get_encrypted_token
from app.security_tokens import decrypt_token
from app.security_tokens import decrypt_token

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
        .join(AdsAccount, AdsAccount.id == AdsCampaign.account_id)
        # Only running Direct campaigns produce new observations. Paused
        # campaigns retain history but must not receive fresh slots. The
        # promotion link is deliberately not part of this predicate: its
        # status controls analysis/autopilot scope, not data collection.
        .where(AdsAccount.provider == "YANDEX_DIRECT", AdsAccount.status == "CONNECTED", AdsCampaign.state == "ON")
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
            exists = await db.scalar(select(AdsCollectionRun.id).where(AdsCollectionRun.slot_key == key))
            if not exists:
                db.add(AdsCollectionRun(provider="YANDEX_DIRECT", account_id=campaign.account_id, campaign_id=campaign.id, collection_type=collection_type, run_type={"CAMPAIGN": "PERIODIC_CAMPAIGN", "GRANULAR": "PERIODIC_GRANULAR", "ENTITY_CONFIG": "ENTITY_SYNC", "SEARCH_QUERY": "PERIODIC_SEARCH_QUERY"}[collection_type], trigger="PERIODIC", scheduled_at=scheduled, slot_key=key))
                created += 1
    await db.commit()
    return created


async def claim_due_slots(db: AsyncSession, *, now: datetime | None = None, limit: int | None = None) -> list[AdsCollectionRun]:
    now = now or datetime.now(timezone.utc)
    limit = limit or settings.ads_collection_max_slots_per_cycle
    async with db.begin():
        rows = list((await db.scalars(
            select(AdsCollectionRun).where(
                AdsCollectionRun.scheduled_at <= now,
                or_(AdsCollectionRun.status == "PENDING", AdsCollectionRun.status == "RETRYABLE_FAILED"),
                or_(AdsCollectionRun.next_retry_at.is_(None), AdsCollectionRun.next_retry_at <= now),
            ).order_by(AdsCollectionRun.scheduled_at).with_for_update(skip_locked=True).limit(limit)
        )).all())
        for row in rows:
            row.status, row.started_at = "PROCESSING", now
            row.attempts += 1
    return rows


async def _health(db: AsyncSession, campaign: AdsCampaign, *, status: str, reason: str) -> None:
    links = list((await db.scalars(select(AdsPromotionCampaignLink.channel_id).where(AdsPromotionCampaignLink.campaign_id == campaign.id, AdsPromotionCampaignLink.object_type == "CHANNEL", AdsPromotionCampaignLink.channel_id.is_not(None)))).all())
    for channel_id in links:
        current = await db.scalar(select(AdsDataHealth).where(AdsDataHealth.channel_id == channel_id).order_by(AdsDataHealth.checked_at.desc()).limit(1))
        if current:
            current.status = status
            current.reasons_json = json.dumps({"periodic_collection": reason, "retention_mode": "CURRENT_STATE"}, ensure_ascii=False)
            current.checked_at = datetime.now(timezone.utc)
        else:
            db.add(AdsDataHealth(channel_id=channel_id, status=status, reasons_json=json.dumps({"periodic_collection": reason, "retention_mode": "CURRENT_STATE"}, ensure_ascii=False)))


async def _collection_context(db: AsyncSession, run: AdsCollectionRun):
    campaign = await db.get(AdsCampaign, run.campaign_id)
    if not campaign:
        raise ValueError("COLLECTION_CAMPAIGN_NOT_FOUND")
    # Any durable link supplies the owning object/token for read-only
    # collection. ACTIVE/INACTIVE is reserved for analysis/autopilot scope.
    link = await db.scalar(select(AdsPromotionCampaignLink).where(AdsPromotionCampaignLink.campaign_id == campaign.id))
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


async def collect_campaign_slot(db: AsyncSession, run: AdsCollectionRun) -> None:
    campaign, channel, encrypted, today = await _collection_context(db, run)
    date_from = today - timedelta(days=6)
    from app.ads.providers.yandex import YandexAdsProvider
    provider = YandexAdsProvider(decrypt_token(encrypted), client_login=settings.yandex_direct_client_login)
    try:
        report = await provider.campaign_performance(int(campaign.external_id), date_from=date_from, date_to=today)
    finally:
        await provider.close()
    fetched_at = report.fetched_at or datetime.now(timezone.utc)
    await collect_yandex_v2(db, token=decrypt_token(encrypted), campaign=campaign, snapshot=None, report_rows=report.rows, date_from=date_from, date_to=today, fetched_at=fetched_at, collection_run_id=run.id, include_entity_metadata=False)
    run.snapshot_id, run.status, run.completed_at = None, "COMPLETED", datetime.now(timezone.utc)
    run.freshness_caveat = "current calendar day may be incomplete or delayed"
    await _health(db, campaign, status="HEALTHY", reason="PERIODIC_COLLECTION_OK")


async def collect_granular_slot(db: AsyncSession, run: AdsCollectionRun) -> None:
    campaign, channel, encrypted, today = await _collection_context(db, run)
    date_from = today - timedelta(days=6)
    result = await collect_granular_yandex_v2(db, token=decrypt_token(encrypted), campaign=campaign, snapshot=None, date_from=date_from, date_to=today, fetched_at=datetime.now(timezone.utc), collection_run_id=run.id, dimensions=("SEARCH_QUERY",) if run.collection_type == "SEARCH_QUERY" else None)
    run.snapshot_id, run.completed_at = None, datetime.now(timezone.utc)
    run.status = "PARTIAL" if result["errors"] else "COMPLETED"
    run.freshness_caveat = "one or more granular subreports failed" if result["errors"] else "current calendar day may be incomplete or delayed"
    await _health(db, campaign, status="DEGRADED" if result["errors"] else "HEALTHY", reason="PERIODIC_GRANULAR_PARTIAL" if result["errors"] else "PERIODIC_GRANULAR_OK")


async def collect_entity_configuration(db: AsyncSession, run: AdsCollectionRun) -> None:
    campaign, channel, encrypted, _ = await _collection_context(db, run)
    from app.ads.providers.yandex import YandexAdsProvider
    provider = YandexAdsProvider(decrypt_token(encrypted), client_login=settings.yandex_direct_client_login)
    try:
        campaigns = await provider.campaigns([int(campaign.external_id)])
        ads_report = await provider.ads([int(campaign.external_id)])
        campaign_entity = await _entity(db, provider="YANDEX_DIRECT", entity_type="CAMPAIGN", provider_entity_id=campaign.external_id, parent_id=None, display_name=campaign.name, status=campaign.status, metadata={"state": campaign.state}, campaign_id=campaign.id)
        for raw in _entity_config_rows(ads_report):
            provider_entity_id = raw.get("Id")
            if provider_entity_id in (None, ""):
                raise ValueError("ENTITY_CONFIG_AD_ID_MISSING")
            await enrich_periodic_ad_media(db, campaign=campaign, raw=raw, provider_client=provider.client, fetched_at=ads_report.fetched_at or datetime.now(timezone.utc))
            ad_entity = await _entity(db, provider="YANDEX_DIRECT", entity_type="AD", provider_entity_id=str(provider_entity_id), parent_id=campaign_entity.id, display_name=None, status=raw.get("Status"), metadata={key: raw.get(key) for key in ads_report.requested_fields}, campaign_id=campaign.id)
            await _upsert_creative(db, campaign=campaign, ad_entity=ad_entity, raw=raw, fetched_at=ads_report.fetched_at or datetime.now(timezone.utc))
    finally:
        await provider.close()
    run.status, run.completed_at = "COMPLETED", datetime.now(timezone.utc)
    run.freshness_caveat = "metadata configuration refreshed"


async def collect_one_slot(db: AsyncSession, run: AdsCollectionRun) -> None:
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
        if settings.ads_retention_enabled:
            async with session_factory() as db:
                await run_retention(db)
        return 0
    async with session_factory() as db:
        if settings.ads_retention_enabled:
            await run_retention(db)
        created = await ensure_due_slots(db)
        due = await claim_due_slots(db)
        ids = [run.id for run in due]
    logger.info("ADS_V2_PERIODIC_COLLECTION_TICK created=%s claimed=%s", created, len(ids))
    processed = 0
    for run_id in ids:
        async with session_factory() as db:
            run = await db.get(AdsCollectionRun, run_id)
            if not run or run.status != "PROCESSING":
                continue
            # A slot may have been queued before Direct reported a campaign as
            # paused. Do not fetch provider data for that stale slot; history
            # remains available and the scheduler will resume after the state
            # returns to ON.
            campaign = await db.get(AdsCampaign, run.campaign_id) if run.campaign_id else None
            if run.trigger == "PERIODIC" and (not campaign or campaign.state != "ON"):
                run.status = "SKIPPED"
                run.error_code = "CAMPAIGN_NOT_ACTIVE"
                run.completed_at = datetime.now(timezone.utc)
                await db.commit()
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
    await trigger_continuous_analysis(session_factory)
    await trigger_autopilot_execution(session_factory)
    return processed


def analysis_due(
    *, last_analysis_at: datetime | None, latest_data_at: datetime | None,
    latest_outcome_at: datetime | None, objective_updated_at: datetime | None,
    now: datetime, cadence_minutes: int,
) -> bool:
    """Bound automatic analysis to the initial cycle or a meaningful change."""
    if last_analysis_at is None:
        return True
    if latest_outcome_at and latest_outcome_at > last_analysis_at:
        return True
    if objective_updated_at and objective_updated_at > last_analysis_at:
        return True
    cadence_elapsed = (now - last_analysis_at).total_seconds() >= max(1, cadence_minutes) * 60
    return bool(cadence_elapsed and (latest_data_at is None or latest_data_at > last_analysis_at))


def _object_link_predicate(object_type: str, object_id: str):
    return (AdsPromotionCampaignLink.channel_id == object_id) if object_type == "CHANNEL" else (
        AdsPromotionCampaignLink.bot_promotion_profile_id == object_id
    )


async def trigger_continuous_analysis(session_factory, *, now: datetime | None = None) -> int:
    """Use the existing analysis service once for every due promotion object."""
    if not settings.ads_periodic_collection_enabled or not settings.ads_background_analysis_enabled:
        return 0
    now = now or datetime.now(timezone.utc)
    from app.ads.service import run_bot_check, run_check
    from app.db.models import AdsAiOutcome, AdsAiRun, AdsRecommendation

    async with session_factory() as db:
        links = list((await db.scalars(select(AdsPromotionCampaignLink).where(
            AdsPromotionCampaignLink.status == "ACTIVE"
        ))).all())
        objects = sorted({
            (link.object_type, link.channel_id or link.bot_promotion_profile_id)
            for link in links if link.object_type in {"CHANNEL", "BOT"}
            and (link.channel_id or link.bot_promotion_profile_id)
        })

    triggered = 0
    for object_type, object_id in objects:
        async with session_factory() as db:
            predicate = _object_link_predicate(object_type, object_id)
            ai_scope = (AdsAiRun.channel_id == object_id,) if object_type == "CHANNEL" else (
                AdsAiRun.promotion_object_type == "BOT", AdsAiRun.promotion_object_id == object_id,
            )
            latest = await db.scalar(select(AdsAiRun).where(*ai_scope)
                                     .order_by(AdsAiRun.created_at.desc()).limit(1))
            pending = await db.scalar(select(AdsAiRun.id).where(
                *ai_scope, AdsAiRun.status.in_(("WAITING_FOR_CODEX", "CODEX_PROCESSING"))
            ).limit(1))
            if pending:
                continue
            latest_data = await db.scalar(select(func.max(AdsCollectionRun.completed_at)).join(
                AdsPromotionCampaignLink, AdsPromotionCampaignLink.campaign_id == AdsCollectionRun.campaign_id
            ).where(
                AdsPromotionCampaignLink.object_type == object_type, predicate,
                AdsPromotionCampaignLink.status == "ACTIVE", AdsCollectionRun.status == "COMPLETED",
            ))
            latest_outcome = await db.scalar(select(func.max(AdsAiOutcome.completed_at))
                .join(AdsRecommendation, AdsRecommendation.id == AdsAiOutcome.recommendation_id)
                .join(AdsAiRun, AdsAiRun.id == AdsRecommendation.ai_run_id)
                .where(*ai_scope, AdsAiOutcome.evaluation_status.in_(("SUCCESS", "FAIL"))))
            objective_updated_at = await db.scalar(select(func.max(AdsPromotionCampaignLink.objective_updated_at)).where(
                AdsPromotionCampaignLink.object_type == object_type, predicate,
                AdsPromotionCampaignLink.status == "ACTIVE",
            ))
            if not analysis_due(
                last_analysis_at=latest.created_at if latest else None,
                latest_data_at=latest_data,
                latest_outcome_at=latest_outcome,
                objective_updated_at=objective_updated_at,
                now=now,
                cadence_minutes=settings.ads_analysis_cadence_minutes,
            ):
                continue
            try:
                if object_type == "CHANNEL":
                    external_id = await db.scalar(select(Admin.external_id).join(
                        Channel, Channel.admin_id == Admin.id
                    ).where(Channel.id == object_id))
                    if not external_id:
                        continue
                    result = await run_check(db, admin_external_id=str(external_id), channel_id=object_id)
                else:
                    profile = await db.get(BotPromotionProfile, object_id)
                    owner_id = (settings.maxibot_owner_user_id or "").strip()
                    if not profile or not owner_id:
                        continue
                    result = await run_bot_check(db, admin_external_id=owner_id, bot_key=profile.bot_key)
                if not result.get("reused"):
                    triggered += 1
            except Exception:
                logger.exception("ADS_CONTINUOUS_ANALYSIS_FAILED object_type=%s object_id=%s", object_type, object_id)
    return triggered


async def trigger_autopilot_execution(session_factory) -> int:
    """Run bounded execution in the existing periodic worker, never a new scheduler.

    The global feature flags are intentionally the first gate: production can
    deploy this code with zero provider writes until the owner enables both
    flags and one exact campaign has been authorized in the UI.
    """
    if not settings.ads_autopilot_enabled or not settings.ads_write_actions_enabled:
        return 0
    from app.ads.autopilot import AUTOPILOT_ACTION_ALLOWLIST, execute_autonomous_action
    from app.ads.yandex_autopilot import yandex_autopilot_adapter
    from app.db.models import AdsAiOutcome, AdsAiRun, AdsDataHealth, AdsRecommendation

    executed = 0
    async with session_factory() as db:
        candidates = list((await db.scalars(select(AdsRecommendation)
            .join(AdsAiRun, AdsAiRun.id == AdsRecommendation.ai_run_id)
            .where(AdsRecommendation.status == "OPEN", AdsRecommendation.experiment_status == "planned")
            .order_by(AdsRecommendation.created_at)
            .limit(20))).all())
        for recommendation in candidates:
            object_type = recommendation.promotion_object_type or "CHANNEL"
            object_id = recommendation.promotion_object_id or recommendation.channel_id
            if not object_id:
                continue
            ai_run = await db.get(AdsAiRun, recommendation.ai_run_id)
            if not ai_run or not ai_run.campaign_id:
                continue
            try:
                payload = json.loads(recommendation.policy_json or "{}")
            except (TypeError, ValueError):
                payload = {}
            action_type = str(payload.get("action_type") or recommendation.action_code or "").upper()
            target = payload.get("action_target")
            if action_type not in AUTOPILOT_ACTION_ALLOWLIST or not isinstance(target, dict):
                continue
            health_scope = (AdsDataHealth.channel_id == object_id,) if object_type == "CHANNEL" else (
                AdsDataHealth.promotion_object_type == "BOT", AdsDataHealth.promotion_object_id == object_id,
            )
            health = await db.scalar(select(AdsDataHealth).where(*health_scope).order_by(AdsDataHealth.checked_at.desc()).limit(1))
            # The ADS health model is older than the Control Plane naming;
            # HEALTHY is the only state mapped to write-capable GOOD.
            if not health or str(health.status).upper() != "HEALTHY":
                continue
            outcome = await db.scalar(select(AdsAiOutcome).where(
                AdsAiOutcome.recommendation_id == recommendation.id
            ).order_by(AdsAiOutcome.due_at).limit(1))
            if not outcome:
                continue
            encrypted = None
            if object_type == "CHANNEL":
                external_id = await db.scalar(select(Admin.external_id).join(Channel, Channel.admin_id == Admin.id).where(Channel.id == object_id))
                encrypted = await get_encrypted_token(str(external_id), object_id, db=db) if external_id else None
            else:
                profile = await db.get(BotPromotionProfile, object_id)
                encrypted = profile.yandex_oauth_token_encrypted if profile else None
            if not encrypted:
                continue
            adapter = yandex_autopilot_adapter(decrypt_token(encrypted))
            try:
                result = await execute_autonomous_action(
                    db, object_type=object_type, object_id=object_id, campaign_id=ai_run.campaign_id,
                    action_type=action_type, target=target, data_trust="GOOD",
                    proposed_spend=float(payload.get("proposed_spend") or 0),
                    provider_execute=adapter.execute, outcome=outcome,
                )
                await db.commit()
                executed += int(result.status == "IMPLEMENTED")
            finally:
                await adapter.client.close()
    return executed


async def collection_health(db: AsyncSession, *, now: datetime | None = None) -> dict[str, object]:
    """Return sanitized scheduler diagnostics for an admin/status surface."""
    now = now or datetime.now(timezone.utc)
    # Some legacy/manual rows can carry COMPLETED without a completion
    # timestamp.  PostgreSQL sorts NULLs first for DESC, so exclude those
    # rows or the admin screen would incorrectly display "н/д" forever.
    last = await db.scalar(select(AdsCollectionRun).where(
        AdsCollectionRun.status == "COMPLETED",
        AdsCollectionRun.completed_at.is_not(None),
        AdsCollectionRun.collection_type == "CAMPAIGN",
    ).order_by(AdsCollectionRun.completed_at.desc()).limit(1))
    next_run = await db.scalar(select(AdsCollectionRun).where(
        AdsCollectionRun.collection_type == "CAMPAIGN",
        AdsCollectionRun.status.in_(("PENDING", "RETRYABLE_FAILED")),
    ).order_by(AdsCollectionRun.scheduled_at).limit(1))
    if not next_run:
        # The scheduler materializes due slots only.  For a compact admin
        # diagnostic, calculate the next campaign slot without inserting it.
        eligible_campaigns = list((await db.scalars(select(AdsCampaign).join(AdsAccount, AdsAccount.id == AdsCampaign.account_id).where(AdsAccount.provider == "YANDEX_DIRECT", AdsAccount.status == "CONNECTED", AdsCampaign.state == "ON").distinct())).all())
        candidates = [_slot_time(now if _slot_time(now, c.id, "CAMPAIGN") > now else now + timedelta(minutes=settings.ads_collection_campaign_interval_minutes), c.id, "CAMPAIGN") for c in eligible_campaigns]
        if candidates:
            next_run = type("NextSlot", (), {"scheduled_at": min(candidates)})()
    from sqlalchemy import func
    eligible = await db.scalar(
        select(func.count(func.distinct(AdsCampaign.id)))
        .select_from(AdsCampaign)
        .join(AdsAccount, AdsAccount.id == AdsCampaign.account_id)
        .where(AdsAccount.provider == "YANDEX_DIRECT", AdsAccount.status == "CONNECTED", AdsCampaign.state == "ON")
    )
    retry_backlog = await db.scalar(select(func.count(AdsCollectionRun.id)).where(AdsCollectionRun.status == "RETRYABLE_FAILED"))
    failed_runs = await db.scalar(select(func.count(AdsCollectionRun.id)).where(AdsCollectionRun.status == "FAILED"))
    by_type: dict[str, dict[str, object]] = {}
    for collection_type in ("CAMPAIGN", "GRANULAR", "ENTITY_CONFIG", "SEARCH_QUERY"):
        type_last = await db.scalar(select(AdsCollectionRun).where(AdsCollectionRun.collection_type == collection_type, AdsCollectionRun.status == "COMPLETED").order_by(AdsCollectionRun.completed_at.desc()).limit(1))
        type_next = await db.scalar(select(AdsCollectionRun).where(AdsCollectionRun.collection_type == collection_type, AdsCollectionRun.status.in_(("PENDING", "RETRYABLE_FAILED"))).order_by(AdsCollectionRun.scheduled_at).limit(1))
        by_type[collection_type] = {
            "last_successful": type_last.completed_at.isoformat() if type_last and type_last.completed_at else None,
            "next_due": type_next.scheduled_at.isoformat() if type_next else None,
        }
    # Staleness is a campaign-level health signal.  Do not join arbitrary
    # collection rows here: an older GRANULAR or ENTITY_CONFIG row must not
    # make a fresh hourly CAMPAIGN collection look stale.  The correlated
    # existence check also avoids counting a campaign once per historical run.
    recent_campaign = select(AdsCollectionRun.id).where(
        AdsCollectionRun.campaign_id == AdsCampaign.id,
        AdsCollectionRun.collection_type == "CAMPAIGN",
        AdsCollectionRun.status == "COMPLETED",
        AdsCollectionRun.completed_at >= now - timedelta(minutes=settings.ads_collection_campaign_interval_minutes * 2),
    )
    stale = await db.scalar(
        select(func.count(func.distinct(AdsCampaign.id)))
        .select_from(AdsCampaign)
        .join(AdsAccount, AdsAccount.id == AdsCampaign.account_id)
        .where(AdsAccount.provider == "YANDEX_DIRECT", AdsAccount.status == "CONNECTED", AdsCampaign.state == "ON", ~exists(recent_campaign))
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
