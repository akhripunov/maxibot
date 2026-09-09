"""Server-side, read-only follow-up collection for V2 outcomes."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ads.facts import build_campaign_facts
from app.ads.outcomes import backfill_missing_outcome_checkpoints, claim_due_outcomes, mark_outcome_completed, mark_outcome_retryable, outcome_metrics_sufficient
from app.ads.v2 import collect_yandex_v2
from app.db.models import Admin, AdsAiRun, AdsAnalysisSnapshot, AdsCampaign, AdsRecommendation, AdsAiOutcome, BotPromotionProfile, Channel
from app.core.config import settings
from app.integrations.yandex_oauth import get_encrypted_token
from app.security_tokens import decrypt_token
from app.traffic.analytics import traffic_stats_for_channels

MOSCOW = __import__("zoneinfo").ZoneInfo("Europe/Moscow")


async def collect_one_outcome(db: AsyncSession, outcome: AdsAiOutcome) -> None:
    if outcome.due_at > datetime.now(timezone.utc):
        raise ValueError("OUTCOME_NOT_DUE")
    recommendation = await db.get(AdsRecommendation, outcome.recommendation_id)
    ai = await db.get(AdsAiRun, recommendation.ai_run_id) if recommendation and recommendation.ai_run_id else None
    channel = await db.get(Channel, recommendation.channel_id) if recommendation else None
    campaign = await db.get(AdsCampaign, ai.campaign_id) if ai and ai.campaign_id else None
    admin_external_id = await db.scalar(select(Admin.external_id).where(Admin.id == channel.admin_id)) if channel else None
    bot_profile = await db.get(BotPromotionProfile, recommendation.promotion_object_id) if recommendation and recommendation.promotion_object_type == "BOT" else None
    encrypted = await get_encrypted_token(str(admin_external_id), channel.id, db=db) if channel and admin_external_id else bot_profile.yandex_oauth_token_encrypted if bot_profile else None
    if not campaign or not encrypted:
        raise ValueError("OUTCOME_PROVIDER_CREDENTIALS_UNAVAILABLE")
    # Prefer a real periodic observation near the checkpoint.  The fallback
    # provider fetch below remains available when the timeline has a gap.
    tolerance = timedelta(minutes=settings.ads_collection_outcome_tolerance_minutes)
    periodic = await db.scalar(select(AdsAnalysisSnapshot).where(
        AdsAnalysisSnapshot.campaign_id == campaign.id,
        AdsAnalysisSnapshot.source == "PERIODIC_COLLECTION",
        AdsAnalysisSnapshot.created_at >= outcome.due_at - tolerance,
        AdsAnalysisSnapshot.created_at <= outcome.due_at + tolerance,
        AdsAnalysisSnapshot.created_at <= datetime.now(timezone.utc),
    ).order_by(AdsAnalysisSnapshot.created_at))
    if periodic:
        baseline = await db.get(AdsAnalysisSnapshot, outcome.baseline_snapshot_id) if outcome.baseline_snapshot_id else None
        before = json.loads(baseline.facts_json).get("facts", {}) if baseline else {}
        after = json.loads(periodic.facts_json).get("facts", {})
        if not baseline or not outcome_metrics_sufficient(after):
            raise ValueError("OUTCOME_AFTER_METRICS_INSUFFICIENT")
        delta = {key: {"before": before.get(key), "after": after.get(key)} for key in ("impressions_7d", "clicks_7d", "cost_7d", "conversions_7d", "actual_cpa_7d", "ctr_7d", "conversion_rate_7d")}
        delta.update({"causal_interpretation": "NOT_AVAILABLE", "interpretation": "OBSERVED_AFTER_RECOMMENDATION", "followup_source": "PERIODIC_COLLECTION", "observation_at": periodic.created_at.isoformat()})
        await mark_outcome_completed(db, outcome, followup_snapshot_id=periodic.id, delta=delta)
        return
    date_to = datetime.now(timezone.utc).astimezone(MOSCOW).date()
    date_from = date_to - timedelta(days=6)
    from app.ads.providers.yandex import YandexAdsProvider
    provider = YandexAdsProvider(decrypt_token(encrypted))
    try:
        report = await provider.campaign_performance(int(campaign.external_id), date_from=date_from, date_to=date_to)
        traffic = await traffic_stats_for_channels(db, [channel.id]) if channel else {}
    finally:
        await provider.close()
    fetched_at = report.fetched_at or datetime.now(timezone.utc)
    snapshot_at = datetime.now(timezone.utc)
    fact_set = build_campaign_facts(report.rows, campaign_start=None, traffic=traffic, now=snapshot_at)
    facts = {"campaign": {"id": campaign.id, "name": campaign.name}, **fact_set.context(report_date_from=date_from, report_date_to=date_to, report_fetched_at=fetched_at, snapshot_at=snapshot_at), "followup": True}
    snapshot = AdsAnalysisSnapshot(channel_id=channel.id if channel else None, promotion_object_type="BOT" if bot_profile else "CHANNEL", promotion_object_id=bot_profile.id if bot_profile else channel.id, campaign_id=campaign.id, source="V2_OUTCOME_FOLLOWUP", period_from=date_from, period_to=date_to, facts_json=json.dumps(facts, ensure_ascii=False))
    db.add(snapshot)
    await db.flush()
    await collect_yandex_v2(db, token=decrypt_token(encrypted), campaign=campaign, snapshot=snapshot, report_rows=report.rows, date_from=date_from, date_to=date_to, fetched_at=fetched_at)
    baseline = await db.get(AdsAnalysisSnapshot, outcome.baseline_snapshot_id) if outcome.baseline_snapshot_id else None
    before = json.loads(baseline.facts_json).get("facts", {}) if baseline else {}
    after = facts.get("facts", {})
    if not baseline or not outcome_metrics_sufficient(after):
        raise ValueError("OUTCOME_AFTER_METRICS_INSUFFICIENT")
    delta = {key: {"before": before.get(key), "after": after.get(key)} for key in ("impressions_7d", "clicks_7d", "cost_7d", "conversions_7d", "actual_cpa_7d", "ctr_7d", "conversion_rate_7d")}
    delta.update({"causal_interpretation": "NOT_AVAILABLE", "interpretation": "OBSERVED_AFTER_RECOMMENDATION", "followup_source": "ON_DEMAND_FETCH"})
    await mark_outcome_completed(db, outcome, followup_snapshot_id=snapshot.id, delta=delta)


async def process_due_outcomes(session_factory) -> int:
    async with session_factory() as db:
        await backfill_missing_outcome_checkpoints(db)
    async with session_factory() as db:
        due = await claim_due_outcomes(db)
        ids = [item.id for item in due]
    processed = 0
    for outcome_id in ids:
        async with session_factory() as db:
            outcome = await db.get(AdsAiOutcome, outcome_id)
            if not outcome or outcome.status != "PROCESSING":
                continue
            try:
                await collect_one_outcome(db, outcome)
                processed += 1
            except Exception as exc:
                await mark_outcome_retryable(db, outcome, type(exc).__name__)
    return processed
