from __future__ import annotations
import json
import asyncio
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.ads.facts import build_campaign_facts
from app.ads.health import data_health, sufficiency
from app.ads.llm import PROMPT_VERSION, SCHEMA_VERSION, assert_safe_context, evidence_registry, policy, provider_from_settings, validate_output
from app.ads.memory import create_candidates, retrieve
from app.ads.codex_worker import WAITING
from app.ads.outcomes import ensure_outcome_checkpoints
from app.ads.analysis import build_v2_analysis_dataset, campaign_fact_set_from_v2
from app.ads.context_budget import bound_ai_context
from app.ads.attribution import build_maxibot_metrics
from app.core.config import settings
from app.db.models import Admin, AdsAccount, AdsAiRun, AdsAnalysisSnapshot, AdsCampaign, AdsCollectionRun, AdsDataHealth, AdsMemory, AdsObservation, AdsPromotionCampaignLink, AdsRecommendation, AdsAiOutcome, Channel, LandingPage, TrafficProfile
from app.integrations.yandex_direct import YandexDirectClient
from app.integrations.yandex_oauth import get_encrypted_token
from app.security_tokens import decrypt_token
from app.traffic.analytics import traffic_stats_for_channels

_CAMPAIGN_SCOPE_CACHE: dict[str, tuple[float, set[str]]] = {}
MOSCOW = ZoneInfo("Europe/Moscow")


def _campaign_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    return {key: raw.get(key) for key in (
        "Id", "Name", "Type", "State", "Status", "StatusPayment",
        "StartDate", "EndDate",
    )}


def _provider_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if value:
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            return None
    return None

def can_access_ads(admin_external_id: str) -> bool:
    owner = (settings.maxibot_owner_user_id or "").strip()
    return bool(settings.ads_enabled and owner and str(admin_external_id) == owner)

async def _account(db: AsyncSession, admin_id: str) -> AdsAccount:
    value = await db.scalar(select(AdsAccount).where(AdsAccount.admin_id == admin_id, AdsAccount.provider == "YANDEX_DIRECT"))
    if value: return value
    value = AdsAccount(admin_id=admin_id, connection_status="CONNECTED"); db.add(value); await db.flush(); return value

async def _direct_sync(db: AsyncSession, *, account: AdsAccount, encrypted: str, report_campaign_ids: set[str] | None = None) -> tuple[list[AdsCampaign], dict[str, list[dict[str, Any]]]]:
    client = YandexDirectClient(decrypt_token(encrypted)); reports: dict[str, list[dict[str, Any]]] = {}
    try:
        for raw in await client.get_campaigns():
            external_id = str(raw.get("Id") or "")
            if not external_id: continue
            campaign = await db.scalar(select(AdsCampaign).where(AdsCampaign.account_id == account.id, AdsCampaign.external_id == external_id))
            if not campaign:
                campaign = AdsCampaign(account_id=account.id, ads_account_id=account.id, external_id=external_id, external_campaign_id=external_id, name=str(raw.get("Name") or external_id))
                db.add(campaign)
                await db.flush()
            metadata = _campaign_metadata(raw)
            metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
            metadata_hash = __import__("hashlib").sha256(metadata_json.encode()).hexdigest()
            campaign.name, campaign.state, campaign.status = str(raw.get("Name") or external_id), raw.get("State"), raw.get("Status")
            campaign.ads_account_id, campaign.external_campaign_id = account.id, external_id
            campaign.campaign_type = raw.get("Type")
            campaign.start_date, campaign.end_date = _provider_date(raw.get("StartDate")), _provider_date(raw.get("EndDate"))
            campaign.raw_json = json.dumps(metadata, ensure_ascii=False)
            if campaign.config_hash != metadata_hash:
                campaign.provider_metadata_json = metadata_json
                campaign.config_hash = metadata_hash
                campaign.metadata_updated_at = datetime.now(timezone.utc)
        await db.flush()
        campaigns = list((await db.scalars(select(AdsCampaign).where(AdsCampaign.account_id == account.id))).all())
        # Statistics are owned by ADS Data Layer V2. This flow only discovers
        # campaign metadata and never re-fetches reports for the AI prompt.
        return campaigns, reports
    finally: await client.close()

async def _mapped(db: AsyncSession, channel_id: str) -> list[str]:
    return list((await db.scalars(select(AdsPromotionCampaignLink.campaign_id).where(AdsPromotionCampaignLink.object_type == "CHANNEL", AdsPromotionCampaignLink.channel_id == channel_id, AdsPromotionCampaignLink.status == "ACTIVE"))).all())


async def campaigns_for_promotion_object(db: AsyncSession, *, object_type: str, object_id: str) -> list[AdsCampaign]:
    """Resolve active campaigns without coupling the ADS layer to one object domain."""
    if object_type not in {"CHANNEL", "BOT"}:
        raise ValueError("ADS_PROMOTION_OBJECT_TYPE_UNSUPPORTED")
    predicate = (
        AdsPromotionCampaignLink.channel_id == object_id
        if object_type == "CHANNEL"
        else AdsPromotionCampaignLink.bot_promotion_profile_id == object_id
    )
    return list((await db.scalars(
        select(AdsCampaign)
        .join(AdsPromotionCampaignLink, AdsPromotionCampaignLink.campaign_id == AdsCampaign.id)
        .where(
            AdsPromotionCampaignLink.object_type == object_type,
            predicate,
            AdsPromotionCampaignLink.status == "ACTIVE",
            AdsCampaign.state != "ARCHIVED",
        )
        .order_by(AdsCampaign.name)
    )).all())


async def campaigns_for_bot(db: AsyncSession, bot_promotion_profile_id: str) -> list[AdsCampaign]:
    return await campaigns_for_promotion_object(db, object_type="BOT", object_id=bot_promotion_profile_id)

def _context(campaign: AdsCampaign | None, facts: dict[str, Any], suff: str, missing: list[str], memories: list[AdsMemory]) -> dict[str, Any]:
    return {"context_version": 2, "CURRENT_CAMPAIGN": {"id": campaign.id, "name": campaign.name, "state": campaign.state} if campaign else None, "FACTS": facts, "DATA_SUFFICIENCY": suff, "RECENT_HISTORY": [], "RETRIEVED_PRIVATE_MEMORIES": [{"id": x.id, "status": x.status, "statement": x.content, "evidence_ids": json.loads(x.evidence_ids_json)} for x in memories], "MISSING_DATA": missing, "ALLOWED_RECOMMENDATION_TYPES": ["HOLD", "MONITOR", "INVESTIGATE", "TEST", "SCALE_CANDIDATE", "REDUCE_CANDIDATE", "PLACEMENT_REVIEW", "ATTRIBUTION_REVIEW", "CREATIVE_REVIEW", "TARGETING_REVIEW", "OBSERVE", "REVIEW_GOAL", "REVIEW_ATTRIBUTION", "REVIEW_CREATIVE", "REVIEW_TARGETING", "REVIEW_PLACEMENTS", "INSUFFICIENT_DATA"], "SYSTEM_CONSTRAINTS": {"direct_writes": False, "autopilot": False, "global_learning": False, "execution_allowed": False}}


async def _closed_loop_context(db: AsyncSession, channel_id: str, campaign_id: str) -> dict[str, list[dict[str, Any]]]:
    """Return bounded recommendation/outcome history without attribution claims."""
    recommendations = list((await db.scalars(
        select(AdsRecommendation)
        .join(AdsAiRun, AdsAiRun.id == AdsRecommendation.ai_run_id)
        .where(AdsRecommendation.channel_id == channel_id, AdsAiRun.campaign_id == campaign_id)
        .order_by(AdsRecommendation.created_at.desc())
        .limit(5)
    )).all())
    outcomes = list((await db.scalars(
        select(AdsAiOutcome)
        .join(AdsRecommendation, AdsRecommendation.id == AdsAiOutcome.recommendation_id)
        .join(AdsAiRun, AdsAiRun.id == AdsRecommendation.ai_run_id)
        .where(AdsRecommendation.channel_id == channel_id, AdsAiRun.campaign_id == campaign_id)
        .order_by(AdsAiOutcome.due_at.desc())
        .limit(15)
    )).all())
    previous = [{
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "status": item.status,
        "priority": item.priority,
        "action_code": item.action_code,
        "confidence": item.confidence,
        "data_sufficiency": item.data_sufficiency,
        "execution_status": "RECOMMENDATION_ONLY",
    } for item in recommendations]
    measured = []
    for item in outcomes:
        delta = None
        if item.observed_delta_json:
            try:
                delta = json.loads(item.observed_delta_json)
            except (TypeError, ValueError):
                delta = None
        measured.append({
            "horizon": item.horizon,
            "due_at": item.due_at.isoformat() if item.due_at else None,
            "status": item.status,
            "availability": "COMPLETED" if item.status == "COMPLETED" and delta is not None else "PENDING" if item.status in {"PENDING", "PROCESSING", "RETRYABLE_FAILED"} else "NOT_AVAILABLE",
            "execution_status": item.execution_status,
            "observed_delta": delta,
        })
    return {"PREVIOUS_RECOMMENDATIONS": previous, "MEASURED_OUTCOMES": measured}

async def _run_campaign_check(db: AsyncSession, *, run: AdsCollectionRun, channel: Channel, campaign: AdsCampaign, reports: dict[str, list[dict]], traffic: dict[str, Any], encrypted: str | None, error: str | None, report_date_from: date, report_date_to: date, report_fetched_at: datetime) -> AdsAiRun:
    closed_loop = await _closed_loop_context(db, channel.id, campaign.id)
    dataset = await build_v2_analysis_dataset(
        db,
        campaign=campaign,
        previous_recommendations=closed_loop["PREVIOUS_RECOMMENDATIONS"],
        measured_outcomes=closed_loop["MEASURED_OUTCOMES"],
    )
    maxibot_metrics = await build_maxibot_metrics(db, channel_id=channel.id, end_date=report_date_to)
    aligned = {**traffic, "visits": maxibot_metrics["7d"]["visits"], "EXACT": 0, "HIGH": 0, "new_members": 0}
    aligned["EXACT"] = int(maxibot_metrics["7d"]["qualified_memberships"])
    fact_set = campaign_fact_set_from_v2(dataset, campaign_start=json.loads(campaign.raw_json or "{}").get("StartDate"), traffic=aligned)
    snapshot_at = datetime.now(timezone.utc)
    v2_cutoff = datetime.fromisoformat(dataset["data_cutoff"])
    fact_context = fact_set.context(report_date_from=report_date_from, report_date_to=report_date_to, report_fetched_at=v2_cutoff, snapshot_at=snapshot_at)
    # Register the stable semantic MaxiBot metric ID before the final facts
    # envelope is assembled; the provider may cite this field directly.
    evidence_ids = evidence_registry({"FACTS": {**fact_context, "MAXIBOT_QUALIFIED_MEMBERSHIP_RATE": True, "V2_ANALYSIS": dataset}})
    suff, missing = sufficiency(mapped_campaigns=1, report_days=int(dataset["data_quality"].get("history_days", 0)), conversions=fact_set.conversion_sample, attribution_available=bool(traffic.get("visits")), direct_available=bool(dataset["data_quality"].get("rows_loaded")))
    health = data_health(direct_available=bool(dataset["data_quality"].get("rows_loaded")), mapped_campaigns=1, attribution_available=bool(traffic.get("visits")), snapshot_fresh=bool(dataset["data_quality"].get("rows_loaded")), ai_available=bool(provider_from_settings()), memory_db_available=True, unmatched_attribution=int(traffic.get("UNATTRIBUTED", 0)))
    # Keep one canonical copy of trend features in FACTS.TRENDS.  V2_ANALYSIS
    # retains the evidence registry and selected granular observations, but
    # does not repeat the same trend object a second time.
    context_dataset = json.loads(json.dumps(dataset, ensure_ascii=False))
    context_dataset.pop("trends", None)
    facts = {"campaign": {"id": campaign.id, "name": campaign.name}, **fact_context, "data_sufficiency": suff, "missing_data": missing, "data_health": health, "MAXIBOT_METRICS": maxibot_metrics, "TRENDS": dataset.get("trends", {}), "DIRECTOLOGIST": dataset.get("DIRECTOLOGIST"), "V2_ANALYSIS": context_dataset, "QUALITY_DECISION_SNAPSHOT": dataset.get("QUALITY_DECISION_SNAPSHOT")}
    facts.update(closed_loop)
    membership_rate = fact_set.maxibot_qualified_membership_rate
    facts["confidence_axes"] = {
        "performance_confidence": "HIGH" if fact_set.conversion_sample is not None and fact_set.conversion_sample >= 10 and dataset["campaign_summary"]["metrics"].get("clicks", 0) >= 100 else "MEDIUM" if fact_set.conversion_sample is not None and fact_set.conversion_sample >= 3 else "LOW",
        "attribution_confidence": "INSUFFICIENT" if membership_rate is None else "LOW" if membership_rate < 0.10 else "MEDIUM" if membership_rate < 0.50 else "HIGH",
        "attribution_semantics": "campaign-specific сопоставление с рекламной кампанией недоступно; это не membership rate",
        "campaign_specific_matching": False,
    }
    facts["MAXIBOT_QUALIFIED_MEMBERSHIP_RATE"] = {
        "value": membership_rate,
        "numerator": "qualified_memberships",
        "denominator": "tracked_landing_visits",
        "meaning": "visit_to_qualified_membership_conversion",
        "qualified_memberships": int(fact_set.qualified_subscriptions) if fact_set.qualified_subscriptions is not None else None,
        "tracked_landing_visits": int(fact_set.landing_visits) if fact_set.landing_visits is not None else None,
        "campaign_specific": False,
        "campaign_specific_matching": "UNKNOWN",
        "window": "7d",
        "scope": "CHANNEL",
    }
    facts["report_trace"] = {"source": "ADS Data Layer V2", "source_fields": ["Date", "Impressions", "Clicks", "Cost", "Conversions"], "date_from": report_date_from.isoformat(), "date_to": report_date_to.isoformat(), "report_timezone": "Europe/Moscow", "aggregation": "deterministic V2 dataset; dimensions analyzed independently", "snapshot_timestamp_utc": snapshot_at.isoformat(), "freshness": "based on latest persisted V2 observations"}
    facts["current_day_quality"] = {"status": "POSSIBLY_INCOMPLETE_CURRENT_DAY", "window": "1d", "reason": "current calendar-day report values can be partial or delayed", "ai_guidance": "do not treat current-day zero cost/conversions as strong deterioration evidence; prefer confirmed 3d/7d data"}
    snapshot = AdsAnalysisSnapshot(channel_id=channel.id, campaign_id=campaign.id, source="MANUAL_CHECK", period_from=report_date_from, period_to=report_date_to, facts_json=json.dumps(facts, ensure_ascii=False))
    db.add(snapshot); await db.flush()
    snapshot.facts_json = json.dumps(facts, ensure_ascii=False)
    current_health = await db.scalar(select(AdsDataHealth).where(AdsDataHealth.channel_id == channel.id).order_by(AdsDataHealth.checked_at.desc()).limit(1))
    if current_health:
        current_health.status = health["status"]
        current_health.reasons_json = json.dumps({**health, "retention_mode": "CURRENT_STATE"}, ensure_ascii=False)
        current_health.checked_at = datetime.now(timezone.utc)
    else:
        db.add(AdsDataHealth(channel_id=channel.id, status=health["status"], reasons_json=json.dumps({**health, "retention_mode": "CURRENT_STATE"}, ensure_ascii=False)))
    memories = await retrieve(db, channel_id=channel.id, campaign_id=campaign.id)
    context = _context(campaign, facts, suff, missing, memories)
    context, final_budget = bound_ai_context(
        context,
        target_bytes=min(60000, settings.ads_ai_context_max_bytes - 1000),
        max_bytes=settings.ads_ai_context_max_bytes,
        max_evidence=settings.ads_ai_evidence_max_entries,
    )
    worker_mode = settings.ads_llm_provider == "codex_worker" and settings.ads_codex_worker_enabled
    context_bytes = len(json.dumps(context, ensure_ascii=False).encode("utf-8"))
    context["FACTS"]["V2_ANALYSIS"]["context_budget"].update({**final_budget, "context_bytes": context_bytes, "context_fact_count": len(context["FACTS"]["V2_ANALYSIS"].get("evidence", {}))})
    if context_bytes > settings.ads_ai_context_max_bytes or facts["V2_ANALYSIS"]["context_budget"].get("budget_exceeded"):
        ai = AdsAiRun(channel_id=channel.id, campaign_id=campaign.id, status="CODEX_FAILED", provider="CODEX_WORKER", model="CODEX_CLI", input_snapshot_id=snapshot.id, prompt_version=PROMPT_VERSION, schema_version=SCHEMA_VERSION, safe_context_json=json.dumps(context, ensure_ascii=False), validation_error="AI_CONTEXT_BUDGET_EXCEEDED", is_test=True)
        db.add(ai); await db.flush()
        if worker_mode:
            run.status, run.error_code = "COMPLETED", error
        return ai
    ai = AdsAiRun(channel_id=channel.id, campaign_id=campaign.id, status=WAITING if worker_mode else "SKIPPED", provider="CODEX_WORKER" if worker_mode else settings.ads_llm_provider, model="CODEX_CLI" if worker_mode else settings.ads_llm_model, input_snapshot_id=snapshot.id, prompt_version=PROMPT_VERSION, schema_version=SCHEMA_VERSION, safe_context_json=json.dumps(context, ensure_ascii=False), is_test=worker_mode)
    db.add(ai); await db.flush()
    if worker_mode:
        run.status, run.error_code = "COMPLETED", error
        return ai
    provider = provider_from_settings()
    if suff == "INSUFFICIENT": ai.status, ai.validation_error = "INSUFFICIENT_DATA", "critical data is missing"
    elif not provider: ai.status, ai.validation_error = "PROVIDER_UNAVAILABLE", "AI_PROVIDER_UNAVAILABLE"
    else:
        try:
            assert_safe_context(context)
            output = await provider.analyze_campaign(context)
            ai.raw_structured_output_json = json.dumps(output, ensure_ascii=False)
            valid, invalid = validate_output(output, evidence_ids)
            if not valid: ai.status, ai.validation_error = "SCHEMA_INVALID", invalid
            else:
                verdict = policy(valid, suff)
                ai.status, ai.validated_output_json, ai.policy_result_json = "COMPLETED", json.dumps(valid, ensure_ascii=False), json.dumps(verdict)
                rec = valid["recommendation"]
                recommendation = AdsRecommendation(channel_id=channel.id, ai_run_id=ai.id, status="OPEN" if verdict["displayable"] else "HIDDEN", priority=verdict["risk_level"], title=valid["summary"][:512], rationale=rec.get("reason"), evidence_ids_json=json.dumps([x["evidence_id"] for x in valid["evidence"]]), action_code=rec["action_code"], confidence=valid["confidence"], data_sufficiency=valid["data_sufficiency"], policy_json=json.dumps(verdict), problem_json=json.dumps({"diagnosis_code": valid["diagnosis_code"], "summary": valid["summary"]}, ensure_ascii=False), root_cause_json=json.dumps(dataset.get("DIRECTOLOGIST", {}).get("root_cause"), ensure_ascii=False), hypothesis=rec.get("hypothesis") or rec.get("reason"), expected_impact_json=json.dumps(rec.get("expected_impact"), ensure_ascii=False), evaluation_window_days=7 if valid["data_sufficiency"] == "SUFFICIENT" else 14, evaluation_at=datetime.now(timezone.utc) + timedelta(days=7 if valid["data_sufficiency"] == "SUFFICIENT" else 14), promotion_object_type=dataset.get("DIRECTOLOGIST", {}).get("object_type"))
                db.add(recommendation); await db.flush(); await ensure_outcome_checkpoints(db, recommendation, baseline_snapshot_id=snapshot.id)
                await create_candidates(db, channel_id=channel.id, campaign_id=campaign.id, ai_run_id=ai.id, snapshot_id=snapshot.id, candidates=valid["memory_candidates"], known_evidence=evidence_ids)
        except Exception as exc:
            ai.status, ai.validation_error = "FAILED", type(exc).__name__
    ai.completed_at = datetime.now(timezone.utc)
    return ai

async def run_check(db: AsyncSession, *, admin_external_id: str, channel_id: str, ui_message_id: str | None = None) -> dict[str, Any]:
    if not can_access_ads(admin_external_id): raise PermissionError("ADS access denied")
    channel = await db.scalar(select(Channel).join(Admin).where(Channel.id == channel_id, Admin.external_id == str(admin_external_id)))
    if not channel: raise PermissionError("channel is not owned")
    run = AdsCollectionRun(provider="INTERNAL", channel_id=channel.id, collection_type="MANUAL_AI_SYNC", run_type="MANUAL_AI_SYNC", trigger="MANUAL", scheduled_at=datetime.now(timezone.utc), slot_key=f"manual-ai-sync:{channel.id}:{datetime.now(timezone.utc).isoformat()}"); db.add(run); await db.flush()
    profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == channel.id)); traffic = await traffic_stats_for_channels(db, [channel.id])
    mapped = await _mapped(db, channel.id)
    encrypted = await get_encrypted_token(str(admin_external_id), channel.id, db=db); campaigns: list[AdsCampaign] = []; reports: dict[str, list[dict]] = {}; error = None
    report_date_to = datetime.now(timezone.utc).astimezone(MOSCOW).date(); report_date_from = report_date_to - timedelta(days=6); report_fetched_at = datetime.now(timezone.utc)
    if encrypted:
        try: campaigns, reports = await _direct_sync(db, account=await _account(db, channel.admin_id), encrypted=encrypted, report_campaign_ids=set(mapped))
        except Exception as exc: error = type(exc).__name__
    selected = [item for item in campaigns if item.id in mapped]
    if not selected:
        run.status, run.error_code, run.finished_at = "COMPLETED", error, datetime.now(timezone.utc)
        await db.commit()
        return {"campaign_count": 0, "ai_status": "SKIPPED"}
    ai_runs = [await _run_campaign_check(db, run=run, channel=channel, campaign=item, reports=reports, traffic=traffic, encrypted=encrypted, error=error, report_date_from=report_date_from, report_date_to=report_date_to, report_fetched_at=report_fetched_at) for item in selected]
    for ai in ai_runs:
        ai.ui_admin_external_id = str(admin_external_id)
        ai.ui_message_id = ui_message_id
    run.status, run.error_code, run.summary_json, run.finished_at = ("PARTIAL" if error else "COMPLETED"), error, json.dumps({"campaign_count": len(ai_runs), "ai": WAITING if settings.ads_llm_provider == "codex_worker" and settings.ads_codex_worker_enabled else "COMPLETED"}), datetime.now(timezone.utc)
    await db.commit()
    return {"campaign_count": len(ai_runs), "ai_status": WAITING if settings.ads_llm_provider == "codex_worker" and settings.ads_codex_worker_enabled else "COMPLETED", "ai_run_ids": [item.id for item in ai_runs]}

async def latest_view(db: AsyncSession, channel_id: str) -> dict[str, Any]:
    ai = await db.scalar(select(AdsAiRun).where(AdsAiRun.channel_id == channel_id).order_by(AdsAiRun.created_at.desc()))
    # A periodic granular snapshot may be newer but intentionally contains
    # only collection metadata. Prefer the snapshot that produced the current
    # AI run so the dashboard keeps its campaign facts and provenance.
    snapshot = await db.get(AdsAnalysisSnapshot, ai.input_snapshot_id) if ai and ai.input_snapshot_id else None
    if snapshot is None:
        snapshot = await db.scalar(select(AdsAnalysisSnapshot).where(AdsAnalysisSnapshot.channel_id == channel_id, AdsAnalysisSnapshot.source != "PERIODIC_GRANULAR").order_by(AdsAnalysisSnapshot.created_at.desc()))
    health = await db.scalar(select(AdsDataHealth).where(AdsDataHealth.channel_id == channel_id).order_by(AdsDataHealth.checked_at.desc())); memories = list((await db.scalars(select(AdsMemory).where(AdsMemory.channel_id == channel_id).order_by(AdsMemory.updated_at.desc()).limit(20))).all())
    recommendation = await db.scalar(select(AdsRecommendation).where(AdsRecommendation.channel_id == channel_id, AdsRecommendation.status == "OPEN").order_by(AdsRecommendation.created_at.desc()))
    return {"facts": json.loads(snapshot.facts_json) if snapshot else None, "health": json.loads(health.reasons_json) if health else {"status": "UNKNOWN", "checks": {}}, "memories": memories, "checked_at": snapshot.created_at if snapshot else None, "ai": ai, "recommendation": recommendation}

async def campaigns_for_channel(db: AsyncSession, channel_id: str) -> list[tuple[AdsCampaign, bool]]:
    channel = await db.scalar(select(Channel).join(Admin, Channel.admin_id == Admin.id).where(Channel.id == channel_id))
    landing = await db.scalar(select(LandingPage).where(LandingPage.channel_id == channel_id, LandingPage.is_active.is_(True)).order_by(LandingPage.updated_at.desc()))
    landing_url = f"https://go.maxibot.pro/{landing.public_id}" if landing else ""
    matching_external_ids: set[str] | None = None
    cached_scope = _CAMPAIGN_SCOPE_CACHE.get(channel_id)
    cache_fresh = bool(cached_scope and time.monotonic() - cached_scope[0] < 60)
    if cache_fresh:
        matching_external_ids = cached_scope[1]
    if channel:
        admin_external_id = await db.scalar(select(Admin.external_id).where(Admin.id == channel.admin_id))
        encrypted = await get_encrypted_token(str(admin_external_id), channel_id, db=db)
        if encrypted and not cache_fresh:
            # Campaign discovery is read-only and must not request reports.
            # Report calls are rate limited and belong to explicit AI checks.
            client = YandexDirectClient(decrypt_token(encrypted))
            try:
                account = await _account(db, channel.admin_id)
                direct_campaigns = await client.get_campaigns()
                direct_campaign_ids: list[int] = []
                for raw in direct_campaigns:
                    external_id = str(raw.get("Id") or "")
                    if not external_id:
                        continue
                    direct_campaign_ids.append(int(external_id))
                    campaign = await db.scalar(select(AdsCampaign).where(
                        AdsCampaign.account_id == account.id,
                        AdsCampaign.external_id == external_id,
                    ))
                    if not campaign:
                        campaign = AdsCampaign(account_id=account.id, external_id=external_id, name=str(raw.get("Name") or external_id))
                        db.add(campaign)
                    campaign.name = str(raw.get("Name") or external_id)
                    campaign.state = raw.get("State")
                    campaign.status = raw.get("Status")
                if landing_url:
                    ads = await client.get_ads(direct_campaign_ids)
                    matching_external_ids = {
                        str(ad.get("CampaignId"))
                        for ad in ads
                        if landing_url.rstrip("/") in json.dumps(ad, ensure_ascii=False)
                    }
                    _CAMPAIGN_SCOPE_CACHE[channel_id] = (time.monotonic(), matching_external_ids)
                await db.commit()
            finally:
                await client.close()
    rows = list((await db.scalars(
        select(AdsCampaign)
        .join(AdsAccount)
        .join(Channel, AdsAccount.admin_id == Channel.admin_id)
        .where(Channel.id == channel_id, AdsCampaign.state != "ARCHIVED")
        .order_by(AdsCampaign.name)
    )).all())
    if matching_external_ids is not None:
        rows = [row for row in rows if row.external_id in matching_external_ids]
    active = set(await _mapped(db, channel_id))
    return [(row, row.id in active) for row in rows]

async def toggle_campaign_link(db: AsyncSession, *, channel_id: str, campaign_id: str) -> None:
    link = await db.scalar(select(AdsPromotionCampaignLink).where(AdsPromotionCampaignLink.object_type == "CHANNEL", AdsPromotionCampaignLink.channel_id == channel_id, AdsPromotionCampaignLink.campaign_id == campaign_id));
    if link: link.status = "INACTIVE" if link.status == "ACTIVE" else "ACTIVE"
    else: db.add(AdsPromotionCampaignLink(object_type="CHANNEL", channel_id=channel_id, campaign_id=campaign_id))
    await db.commit()

async def persist_development_result(db: AsyncSession, *, channel_id: str, output: dict[str, Any], ai: AdsAiRun | None = None, worker: bool = False) -> AdsAiRun:
    """Persist a Codex development result through the same validation/policy path.

    This is deliberately not a provider and has no HTTP/session access.  It is
    restricted to an already-created manual run and marked test-only.
    """
    ai = ai or await db.scalar(select(AdsAiRun).where(AdsAiRun.channel_id == channel_id).order_by(AdsAiRun.created_at.desc()))
    if not ai or not ai.safe_context_json or not ai.input_snapshot_id: raise ValueError("manual sync is required first")
    context = json.loads(ai.safe_context_json); assert_safe_context(context)
    evidence_ids = evidence_registry(context)
    valid, error = validate_output(output, evidence_ids)
    if worker:
        ai.provider, ai.model, ai.is_test = "CODEX_WORKER", "CODEX_CLI", True
    else:
        ai.provider, ai.model, ai.is_test = "DEVELOPMENT_MANUAL", "CODEX_SESSION", True
    ai.raw_structured_output_json = json.dumps(output, ensure_ascii=False)
    if not valid:
        ai.status, ai.validation_error, ai.completed_at = ("CODEX_FAILED" if worker else "SCHEMA_INVALID"), error, datetime.now(timezone.utc); await db.commit(); return ai
    verdict = policy(valid, context.get("DATA_SUFFICIENCY", "INSUFFICIENT")); ai.status, ai.validated_output_json, ai.policy_result_json, ai.validation_error, ai.completed_at = ("CODEX_COMPLETED" if worker else "COMPLETED"), json.dumps(valid, ensure_ascii=False), json.dumps(verdict), None, datetime.now(timezone.utc)
    rec = valid["recommendation"]
    recommendation = AdsRecommendation(channel_id=channel_id, ai_run_id=ai.id, status="OPEN" if verdict["displayable"] else "HIDDEN", priority=verdict["risk_level"], title=valid["summary"][:512], rationale=rec.get("reason"), evidence_ids_json=json.dumps([row["evidence_id"] for row in valid["evidence"]]), action_code=rec["action_code"], confidence=valid["confidence"], data_sufficiency=valid["data_sufficiency"], policy_json=json.dumps(verdict), hypothesis=rec.get("hypothesis") or rec.get("reason"), expected_impact_json=json.dumps(rec.get("expected_impact"), ensure_ascii=False), evaluation_window_days=7 if valid["data_sufficiency"] == "SUFFICIENT" else 14, evaluation_at=datetime.now(timezone.utc) + timedelta(days=7 if valid["data_sufficiency"] == "SUFFICIENT" else 14))
    db.add(recommendation)
    await db.flush()
    await ensure_outcome_checkpoints(db, recommendation, baseline_snapshot_id=ai.input_snapshot_id)
    campaign_id = (context.get("CURRENT_CAMPAIGN") or {}).get("id")
    await create_candidates(db, channel_id=channel_id, campaign_id=campaign_id, ai_run_id=ai.id, snapshot_id=ai.input_snapshot_id, candidates=valid["memory_candidates"], known_evidence=evidence_ids)
    await db.commit(); return ai
