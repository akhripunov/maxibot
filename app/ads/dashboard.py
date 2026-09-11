"""Read-only object-level projection for the unified AI Promotion screen.

The dashboard deliberately derives state from the canonical ADS tables. It
does not persist UI state, invoke an AI provider, or write to an ad platform.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AdsAiOutcome, AdsAiRun, AdsAnalysisSnapshot, AdsCampaign, AdsCreative,
    AdsDataHealth, AdsMemory, AdsMemoryEvidence, AdsPromotionCampaignLink,
    AdsRecommendation,
)
from app.ads.autopilot import promotion_policy

INACTIVE_CAMPAIGN_STATES = {"ARCHIVED", "SUSPENDED", "DISABLED", "OFF", "ENDED"}
RUNNING_AI_STATES = {"WAITING_FOR_CODEX", "CODEX_PROCESSING"}
VERIFIED_MEMORY_STATES = {"VALIDATED", "TRUSTED"}
_PRIORITY = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
_ROOT_CAUSE_ICONS = {
    "PLACEMENT": "📍 Площадка", "CREATIVE": "🎨 Креатив",
    "REGION": "🌍 Регион", "DEVICE": "📱 Устройство",
    "CAMPAIGN": "⚙️ Кампания", "AD_GROUP": "🗂 Группа объявлений",
    "AD": "📣 Объявление", "DEMOGRAPHICS": "👥 Аудитория",
    "CRITERIA": "🔎 Условие показа",
}


def _json(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


def _scope(model: Any, object_type: str, object_id: str) -> tuple[Any, ...]:
    if object_type == "CHANNEL":
        return (model.channel_id == object_id,)
    if object_type == "BOT":
        return (model.promotion_object_type == "BOT", model.promotion_object_id == object_id)
    raise ValueError("ADS_PROMOTION_OBJECT_TYPE_UNSUPPORTED")


def _link_scope(object_type: str, object_id: str) -> tuple[Any, ...]:
    if object_type == "CHANNEL":
        return (AdsPromotionCampaignLink.object_type == "CHANNEL", AdsPromotionCampaignLink.channel_id == object_id)
    if object_type == "BOT":
        return (AdsPromotionCampaignLink.object_type == "BOT", AdsPromotionCampaignLink.bot_promotion_profile_id == object_id)
    raise ValueError("ADS_PROMOTION_OBJECT_TYPE_UNSUPPORTED")


def business_cpa(*, spend: float | None, confirmed_outcomes: int | None) -> float | None:
    """Never turn unknown data or zero confirmed outcomes into a zero CPA."""
    if spend is None or confirmed_outcomes is None or confirmed_outcomes <= 0:
        return None
    return float(spend) / confirmed_outcomes


def stage_for_state(
    *, recommendation: Any, outcome: Any, health: str, has_data: bool,
    learning_proven: bool, analysis_running: bool = False,
) -> str:
    """Map canonical Closed-Loop state to the five user-facing stages."""
    evaluation = getattr(outcome, "evaluation_status", None) if outcome else None
    implementation = getattr(outcome, "implementation_confirmed", None) if outcome else None
    if learning_proven and evaluation == "SUCCESS":
        return "SCALE"
    if evaluation in {"SUCCESS", "FAIL"}:
        return "RESULT"
    if implementation == "YES" or evaluation == "READY":
        return "CHECK"
    if (recommendation and getattr(recommendation, "status", None) == "OPEN"
            and getattr(recommendation, "data_sufficiency", None) not in {"INSUFFICIENT", "UNKNOWN"}):
        return "RECOMMENDATION"
    if analysis_running or not has_data or health not in {"HEALTHY", "PARTIAL"}:
        return "ANALYSIS"
    return "ANALYSIS"


def _objective(links: list[AdsPromotionCampaignLink], object_type: str) -> dict[str, Any]:
    configured = [link for link in links if link.status == "ACTIVE"
                  and link.objective_status == "ACTIVE" and link.objective_type
                  and link.target_cpa is not None]
    primary_outcome = "max_subscriber" if object_type == "CHANNEL" else "bot_started"
    if not configured:
        return {"type": primary_outcome, "mode": "NONE", "status": "UNSET",
                "target_cpa": None, "currency": "RUB", "updated_at": None}
    values = {(link.objective_type, float(link.target_cpa), link.currency or "RUB") for link in configured}
    updated_at = max((link.objective_updated_at for link in configured if link.objective_updated_at), default=None)
    if len(values) != 1:
        return {"type": primary_outcome, "mode": "TARGET_CPA", "status": "CONFLICT",
                "target_cpa": None, "currency": "RUB", "updated_at": updated_at}
    objective_type, target_cpa, currency = next(iter(values))
    return {"type": primary_outcome, "mode": objective_type, "status": "ACTIVE",
            "target_cpa": target_cpa, "currency": currency, "updated_at": updated_at}


def _root_cause(recommendation: AdsRecommendation | None) -> tuple[str | None, str]:
    if not recommendation or recommendation.data_sufficiency in {"INSUFFICIENT", "UNKNOWN"}:
        return None, "NOT_PROVEN"
    payload = _json(recommendation.root_cause_json, {})
    if not isinstance(payload, dict) or payload.get("status") != "ROOT_CAUSE_FOUND":
        return None, "NOT_PROVEN"
    contributors = payload.get("top_contributors") or []
    primary = contributors[0] if contributors and isinstance(contributors[0], dict) else None
    if not primary:
        return None, "NOT_PROVEN"
    dimension = str(primary.get("dimension") or "").upper()
    entity = primary.get("entity") or primary.get("label")
    if not entity:
        return None, "NOT_PROVEN"
    return f"{_ROOT_CAUSE_ICONS.get(dimension, dimension.title() or 'Причина')} {entity}", "PROVEN"


def _creative_attention(creative: AdsCreative) -> bool:
    features = _json(creative.ai_features_json, {})
    return bool(isinstance(features, dict) and features.get("requires_attention") is True
                and (features.get("evidence_ids") or features.get("reason") or features.get("evidence")))


def _implementation_actor(outcome: AdsAiOutcome | None) -> str | None:
    evidence = _json(outcome.implementation_evidence_json, {}) if outcome else {}
    if isinstance(evidence, list):
        evidence = evidence[0] if evidence else {}
    actor = evidence.get("executed_by") if isinstance(evidence, dict) else None
    return str(actor).upper() if actor else None


def _experiment_view(recommendation: AdsRecommendation, outcome: AdsAiOutcome | None) -> dict[str, Any]:
    delta = _json(outcome.observed_delta_json, {}) if outcome else {}
    delta = delta if isinstance(delta, dict) else {}
    before = delta.get("before") or delta.get("baseline") or delta.get("before_cpa")
    after = delta.get("after") or delta.get("actual") or delta.get("after_cpa")
    if before is None or after is None:
        before = delta.get("actual_cpa_7d", {}).get("before") if isinstance(delta.get("actual_cpa_7d"), dict) else before
        after = delta.get("actual_cpa_7d", {}).get("after") if isinstance(delta.get("actual_cpa_7d"), dict) else after
    return {
        "experiment_id": recommendation.experiment_id,
        "experiment_seq": recommendation.experiment_seq,
        "experiment_status": recommendation.experiment_status,
        "title": recommendation.title,
        "action": recommendation.hypothesis or recommendation.title,
        "executed_by": _implementation_actor(outcome),
        "implementation_confirmed": outcome.implementation_confirmed if outcome else "UNKNOWN",
        "evaluation_status": outcome.evaluation_status if outcome else None,
        "before": before,
        "after": after,
        "rollback": bool(outcome and outcome.execution_status == "ROLLED_BACK"),
    }


async def build_ads_dashboard(db: AsyncSession, *, object_type: str, object_id: str) -> dict[str, Any]:
    """Build a bounded dashboard for one CHANNEL or BOT promotion object."""
    object_type = object_type.upper()
    links = list((await db.scalars(select(AdsPromotionCampaignLink)
        .where(*_link_scope(object_type, object_id)).order_by(AdsPromotionCampaignLink.created_at))).all())
    campaign_ids = list(dict.fromkeys(link.campaign_id for link in links))
    campaigns = list((await db.scalars(select(AdsCampaign).where(AdsCampaign.id.in_(campaign_ids))
        .order_by(AdsCampaign.name))).all()) if campaign_ids else []
    campaign_by_id = {campaign.id: campaign for campaign in campaigns}
    active_link_ids = {link.campaign_id for link in links if link.status == "ACTIVE"}
    active_campaigns = [campaign_by_id[campaign_id] for campaign_id in campaign_ids
        if campaign_id in active_link_ids and campaign_id in campaign_by_id
        and str(campaign_by_id[campaign_id].state or "").upper() not in INACTIVE_CAMPAIGN_STATES
        and str(campaign_by_id[campaign_id].status or "").upper() not in INACTIVE_CAMPAIGN_STATES]
    active_ids = {campaign.id for campaign in active_campaigns}

    runs = list((await db.scalars(select(AdsAiRun).where(
        *_scope(AdsAiRun, object_type, object_id), AdsAiRun.campaign_id.in_(active_ids))
        .order_by(AdsAiRun.created_at.desc()).limit(100))).all()) if active_ids else []
    run_by_id = {run.id: run for run in runs}
    running = any(run.status in RUNNING_AI_STATES for run in runs)
    all_recommendations = list((await db.scalars(select(AdsRecommendation).where(
        AdsRecommendation.ai_run_id.in_(list(run_by_id)))
        .order_by(AdsRecommendation.created_at.desc()).limit(100))).all()) if run_by_id else []
    recommendations = [item for item in all_recommendations if item.status == "OPEN"]
    recommendations.sort(key=lambda item: (_PRIORITY.get(str(item.priority or "").upper(), 9),
                                             -(item.created_at.timestamp() if item.created_at else 0)))
    recommendation = next((item for item in recommendations
                           if item.data_sufficiency not in {"INSUFFICIENT", "UNKNOWN"}), None)
    all_outcomes = list((await db.scalars(select(AdsAiOutcome).where(
        AdsAiOutcome.recommendation_id.in_([item.id for item in all_recommendations]))
        .order_by(AdsAiOutcome.due_at.desc()))).all()) if all_recommendations else []
    outcomes_by_recommendation: dict[str, list[AdsAiOutcome]] = {}
    for item in all_outcomes:
        outcomes_by_recommendation.setdefault(item.recommendation_id, []).append(item)
    outcome = outcomes_by_recommendation.get(recommendation.id, [None])[0] if recommendation else None

    snapshots: list[AdsAnalysisSnapshot] = []
    for campaign in active_campaigns:
        snapshot = await db.scalar(select(AdsAnalysisSnapshot).where(
            AdsAnalysisSnapshot.campaign_id == campaign.id,
            AdsAnalysisSnapshot.source != "PERIODIC_GRANULAR",
            *_scope(AdsAnalysisSnapshot, object_type, object_id),
        ).order_by(AdsAnalysisSnapshot.created_at.desc()).limit(1))
        if snapshot:
            snapshots.append(snapshot)
    snapshots.sort(key=lambda item: item.created_at, reverse=True)
    parsed_snapshots = [(snapshot, _json(snapshot.facts_json, {})) for snapshot in snapshots]
    spend_values = [(payload.get("facts") or {}).get("cost_7d")
                    for _, payload in parsed_snapshots if isinstance(payload, dict)]
    spend = (sum(float(value) for value in spend_values if value is not None)
             if any(value is not None for value in spend_values) else None)
    latest_payload = parsed_snapshots[0][1] if parsed_snapshots else {}
    metrics = latest_payload.get("MAXIBOT_METRICS") or {}
    window = metrics.get("7d") or {}
    confirmed = window.get("qualified_memberships")
    if confirmed is None:
        confirmed = window.get("qualified_outcomes")
    confirmed = int(confirmed) if confirmed is not None else None
    campaign_mapping = metrics.get("campaign_specific") if isinstance(metrics, dict) else None
    # Mapping scope is determined by active promotion links, not provider
    # delivery state. A linked campaign may have a transient/unknown provider
    # state while its deterministic UTM attribution remains valid.
    active_link_campaign_ids = [campaign_id for campaign_id in campaign_ids if campaign_id in active_link_ids]
    if object_type == "CHANNEL" and len(active_link_campaign_ids) == 1 and active_link_campaign_ids[0] in campaign_by_id:
        # Recompute this small deterministic flag from canonical visits so an
        # older persisted snapshot cannot keep showing stale unavailable copy.
        from app.ads.attribution import build_maxibot_metrics
        mapping_metrics = await build_maxibot_metrics(
            db, channel_id=object_id,
            end_date=(snapshots[0].period_to if snapshots else datetime.now(timezone.utc).date()),
            windows=("7d",),
        )
        campaign_mapping = mapping_metrics.get("campaign_specific")

    health = await db.scalar(select(AdsDataHealth).where(*_scope(AdsDataHealth, object_type, object_id))
                             .order_by(AdsDataHealth.checked_at.desc()).limit(1))
    health_status = health.status if health else "UNKNOWN"
    memories = list((await db.scalars(select(AdsMemory).where(
        *_scope(AdsMemory, object_type, object_id), AdsMemory.status.in_(VERIFIED_MEMORY_STATES))
        .order_by(AdsMemory.updated_at.desc()).limit(12))).all())
    evidence_counts: dict[str, int] = {}
    if memories:
        evidence_rows = list((await db.scalars(select(AdsMemoryEvidence).where(
            AdsMemoryEvidence.memory_id.in_([item.id for item in memories])))).all())
        for evidence in evidence_rows:
            evidence_counts[evidence.memory_id] = evidence_counts.get(evidence.memory_id, 0) + 1
    learning_proven = any(item.status == "TRUSTED" for item in memories)
    creatives = list((await db.scalars(select(AdsCreative).where(AdsCreative.campaign_id.in_(active_ids))
        .order_by(AdsCreative.updated_at.desc()))).all()) if active_ids else []
    active_creatives = [item for item in creatives
                        if str(item.provider_state or "").upper() not in INACTIVE_CAMPAIGN_STATES]
    attention = [item for item in active_creatives if _creative_attention(item)]
    root_cause, root_cause_status = _root_cause(recommendation)
    stage = stage_for_state(recommendation=recommendation, outcome=outcome, health=health_status,
                            has_data=bool(parsed_snapshots), learning_proven=learning_proven,
                            analysis_running=running)
    if stage == "CHECK":
        next_action = "Ничего не менять до завершения проверки."
    elif stage == "SCALE":
        next_action = "Рассмотреть безопасное масштабирование подтверждённого подхода."
    else:
        next_action = recommendation.title if recommendation else "Продолжать сбор данных."
    if root_cause_status != "PROVEN" and recommendation:
        root_cause = "Рекламная причина пока не доказана."
    objective = _objective(links, object_type)
    autopilot = await promotion_policy(db, object_type=object_type, object_id=object_id)
    why_summary = recommendation.rationale if recommendation else None
    if campaign_mapping and campaign_mapping.get("supported") and why_summary:
        stale_markers = ("campaign-specific", "сопоставление с конкретной", "конкретной рекламной кампанией недоступно")
        if any(marker in str(why_summary).lower() for marker in stale_markers):
            why_summary = "Подтверждённые подписчики сопоставлены с кампанией по UTM; рекламная причина пока не доказана."

    campaign_rows = []
    for campaign in campaigns:
        link = next((item for item in links if item.campaign_id == campaign.id and item.status == "ACTIVE"), None)
        campaign_mode = str(link.execution_mode or "ADVISORY").upper() if link else "ADVISORY"
        pair = next(((snapshot, payload) for snapshot, payload in parsed_snapshots
                     if snapshot.campaign_id == campaign.id), None)
        facts = (pair[1].get("facts") or {}) if pair else {}
        campaign_rows.append({
            "id": campaign.id, "external_id": campaign.external_id, "name": campaign.name,
            "provider": "YANDEX_DIRECT", "state": campaign.state or campaign.status or "UNKNOWN",
            # Provider delivery state is independent from whether the owner
            # selected the campaign for MaxiBot analysis.
            "active": (
                str(campaign.state or "").upper() not in INACTIVE_CAMPAIGN_STATES
                and str(campaign.status or "").upper() not in INACTIVE_CAMPAIGN_STATES
            ),
            "linked": campaign.id in active_link_ids, "spend": facts.get("cost_7d"),
            "clicks": facts.get("clicks_7d"), "confirmed_business_outcomes": None,
            "execution_mode": campaign_mode,
            "write_scope": "CAMPAIGN_SCOPED" if campaign_mode == "AUTONOMOUS" else "READ_ONLY",
            "actual_business_cpa": None, "last_analysis_at": pair[0].created_at if pair else None,
            "recommendation": next((item for item in recommendations if run_by_id.get(item.ai_run_id)
                                    and run_by_id[item.ai_run_id].campaign_id == campaign.id), None),
        })

    history = []
    for item in all_recommendations[:20]:
        measured = next((value for value in outcomes_by_recommendation.get(item.id, [])
                         if value.status == "COMPLETED"), None)
        history.append({
            "experiment_id": item.experiment_id,
            "experiment_seq": item.experiment_seq,
            "experiment_status": item.experiment_status,
            "created_at": item.created_at,
            "title": item.title,
            "implementation_status": measured.implementation_confirmed if measured else "UNKNOWN",
            "evaluation_status": measured.evaluation_status if measured else "PENDING",
            "delta": _json(measured.observed_delta_json, {}) if measured else {},
            "mode": "Autopilot" if _implementation_actor(measured) == "MAXIBOT" else "Recommendations",
            "executed_by": _implementation_actor(measured),
            "before": _experiment_view(item, measured).get("before"),
            "after": _experiment_view(item, measured).get("after"),
            "rollback": bool(measured and measured.execution_status == "ROLLED_BACK"),
        })

    experiment_rows = []
    for item in all_recommendations:
        measured = next((value for value in outcomes_by_recommendation.get(item.id, [])
                         if value.implementation_confirmed == "YES"), None)
        if measured:
            experiment_rows.append(_experiment_view(item, measured))
    active_experiment = next((item for item in experiment_rows if item.get("evaluation_status") not in {"SUCCESS", "FAIL"}), None)
    completed_experiment = next((item for item in experiment_rows if item.get("evaluation_status") in {"SUCCESS", "FAIL", "INCONCLUSIVE"}), None)
    total_experiments = len(experiment_rows)
    successful_experiments = sum(item.get("evaluation_status") == "SUCCESS" for item in experiment_rows)

    return {
        "object": {"type": object_type, "id": object_id}, "objective": objective,
        "execution_mode": autopilot["execution_mode"], "autopilot": autopilot,
        "target_cpa": objective["target_cpa"], "currency": objective["currency"],
        "actual_business_cpa": business_cpa(spend=spend, confirmed_outcomes=confirmed),
        "confirmed_business_outcomes": confirmed, "business_outcome_name": objective["type"],
        "campaign_specific": campaign_mapping or {"supported": False, "reason": "mapping is unknown"},
        "creative_summary": {"active": len(active_creatives), "attention": len(attention),
                             "status": "ATTENTION" if attention else "OK" if active_creatives else "NONE"},
        "current_stage": stage, "analysis_running": running,
        "primary_root_cause": root_cause, "root_cause_status": root_cause_status,
        "why_summary": why_summary,
        "next_best_action": next_action, "last_updated_at": snapshots[0].created_at if snapshots else None,
        "data_trust": health_status, "data_trust_reasons": _json(health.reasons_json, {}) if health else {},
        "active_campaigns": campaign_rows, "current_recommendation": recommendation,
        "current_outcome": outcome,
        "learning_state": {"status": "PROVEN" if learning_proven else "PROMISING" if memories else "EXPLORE",
                           "verified_count": len(memories)},
        "memories": memories, "memory_evidence_counts": evidence_counts, "creatives": active_creatives,
        "history": history,
        "current_experiment": active_experiment,
        "last_completed_experiment": completed_experiment,
        "learning_summary": {
            "total_experiments": total_experiments,
            "successful_experiments": successful_experiments,
            "proven_memories": sum(item.status == "TRUSTED" for item in memories),
        },
    }
