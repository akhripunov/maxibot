"""Read-only object-level projection for the unified AI Promotion screen.

The dashboard deliberately derives state from the canonical ADS tables. It
does not persist UI state, invoke an AI provider, or write to an ad platform.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AdsAiOutcome, AdsAiRun, AdsAnalysisSnapshot, AdsCampaign, AdsCreative, AdsEntity,
    AdsDataHealth, AdsMemory, AdsMemoryEvidence, AdsPromotionCampaignLink,
    AdsRecommendation, TrafficMembership, TrafficVisit,
)
from app.ads.autopilot import promotion_policy
from app.ads.platform import outcome_type_for
from app.traffic.funnel_reporting import campaign_matches

INACTIVE_CAMPAIGN_STATES = {"ARCHIVED", "SUSPENDED", "DISABLED", "OFF", "ENDED"}
RUNNING_AI_STATES = {"WAITING_FOR_CODEX", "CODEX_PROCESSING"}
VERIFIED_MEMORY_STATES = {"SUPPORTED", "VALIDATED", "TRUSTED", "REJECTED", "STALE", "SUPERSEDED"}
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
    configured = [link for link in links if link.objective_status == "ACTIVE" and link.objective_type
                  and link.target_cpa is not None]
    platforms = {str(getattr(link, "destination_platform", None) or "MAX").upper() for link in links}
    platform = next(iter(platforms), "MAX")
    primary_outcome = outcome_type_for(platform, object_type)
    if not configured:
        return {"type": primary_outcome, "mode": "NONE", "status": "UNSET",
                "target_cpa": None, "currency": "RUB", "updated_at": None,
                "destination_platform": platform}
    values = {(link.objective_type, float(link.target_cpa), link.currency or "RUB",
               str(getattr(link, "destination_platform", None) or "MAX").upper()) for link in configured}
    updated_at = max((link.objective_updated_at for link in configured if link.objective_updated_at), default=None)
    if len(values) != 1:
        return {"type": primary_outcome, "mode": "TARGET_CPA", "status": "CONFLICT",
                "target_cpa": None, "currency": "RUB", "updated_at": updated_at,
                "destination_platform": platform}
    objective_type, target_cpa, currency, platform = next(iter(values))
    return {"type": primary_outcome, "mode": objective_type, "status": "ACTIVE",
            "target_cpa": target_cpa, "currency": currency, "updated_at": updated_at,
            "destination_platform": platform}


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
    evidence_policy = _json(getattr(recommendation, "evidence_policy_json", None), {})
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
        "experiment_kind": recommendation.experiment_kind,
        "evidence_policy": evidence_policy if isinstance(evidence_policy, dict) else {},
        "technical_label": "Техническая проверка" if recommendation.experiment_kind == "TECHNICAL_CANARY" else None,
    }


def _experiment_status_label(item: dict[str, Any]) -> str:
    evaluation = str(item.get("evaluation_status") or "").upper()
    lifecycle = str(item.get("experiment_status") or "").lower()
    if evaluation == "SUCCESS":
        return "✅ Победа"
    if evaluation == "FAIL":
        return "❌ Неудача"
    if evaluation == "INCONCLUSIVE":
        return "⚪ Недостаточно данных"
    if lifecycle in {"running", "executing", "evaluating", "rolling_back"} or item.get("implementation_confirmed") == "YES":
        return "🟡 Идёт"
    return "🟡 Запланирован"


def _history_metric(delta: dict[str, Any], key: str) -> tuple[Any, Any] | None:
    value = delta.get(key)
    if isinstance(value, dict) and value.get("before") is not None and value.get("after") is not None:
        return value["before"], value["after"]
    return None


async def build_ads_dashboard(db: AsyncSession, *, object_type: str, object_id: str) -> dict[str, Any]:
    """Build a bounded dashboard for one CHANNEL or BOT promotion object."""
    object_type = object_type.upper()
    links = list((await db.scalars(select(AdsPromotionCampaignLink)
        .where(*_link_scope(object_type, object_id)).order_by(AdsPromotionCampaignLink.created_at))).all())
    destination_platforms = {str(getattr(link, "destination_platform", None) or "MAX").upper() for link in links} or {"MAX"}
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

    # Current recommendation/analysis state is active-scope only, while
    # experiment history must remain reconstructable after a campaign is
    # suspended or unlinked.  Keep both bounded run sets separate.
    scoped_ids = set(campaign_ids)
    runs = list((await db.scalars(select(AdsAiRun).where(
        *_scope(AdsAiRun, object_type, object_id), AdsAiRun.campaign_id.in_(active_ids))
        .order_by(AdsAiRun.created_at.desc()).limit(100))).all()) if active_ids else []
    history_runs = list((await db.scalars(select(AdsAiRun).where(
        *_scope(AdsAiRun, object_type, object_id), AdsAiRun.campaign_id.in_(scoped_ids))
        .order_by(AdsAiRun.created_at.desc()).limit(200))).all()) if scoped_ids else []
    run_by_id = {run.id: run for run in runs}
    history_run_by_id = {run.id: run for run in history_runs}
    running = any(run.status in RUNNING_AI_STATES for run in runs)
    all_recommendations = list((await db.scalars(select(AdsRecommendation).where(or_(
        AdsRecommendation.ai_run_id.in_(list(history_run_by_id)) if history_run_by_id else False,
        *(_scope(AdsRecommendation, object_type, object_id)),
    ))
        .order_by(AdsRecommendation.created_at.desc()).limit(100))).all())
    current_recommendations = [item for item in all_recommendations if item.ai_run_id in run_by_id]
    recommendations = [item for item in current_recommendations if item.status == "OPEN"]
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
    # The object-level screen must keep showing the last canonical campaign
    # evidence even when the provider campaign is currently suspended.  The
    # active-only snapshot set above remains authoritative for current
    # recommendations; these metric snapshots are read-only display inputs.
    metric_snapshots: list[tuple[AdsAnalysisSnapshot, dict[str, Any]]] = []
    for campaign in campaigns:
        snapshot = next((item for item, _ in parsed_snapshots if item.campaign_id == campaign.id), None)
        if snapshot is None:
            snapshot = await db.scalar(select(AdsAnalysisSnapshot).where(
                AdsAnalysisSnapshot.campaign_id == campaign.id,
                AdsAnalysisSnapshot.source != "PERIODIC_GRANULAR",
                *_scope(AdsAnalysisSnapshot, object_type, object_id),
            ).order_by(AdsAnalysisSnapshot.created_at.desc()).limit(1))
        if snapshot:
            metric_snapshots.append((snapshot, _json(snapshot.facts_json, {})))
    spend_values = [(payload.get("facts") or {}).get("cost_7d")
                    for _, payload in metric_snapshots if isinstance(payload, dict)]
    spend = (sum(float(value) for value in spend_values if value is not None)
             if any(value is not None for value in spend_values) else None)
    latest_payload = (metric_snapshots[0][1] if metric_snapshots else
                      (parsed_snapshots[0][1] if parsed_snapshots else {}))
    metrics = latest_payload.get("MAXIBOT_METRICS") or {}
    window = metrics.get("7d") or {}
    confirmed = window.get("qualified_memberships")
    if confirmed is None:
        confirmed = window.get("qualified_outcomes")
    confirmed = int(confirmed) if confirmed is not None else None
    # For a single linked CHANNEL campaign, use the canonical exact
    # membership population rather than channel-wide qualified totals or
    # provider Direct conversions.  UTM matching is deterministic and keeps
    # the UI scoped to the campaign currently being displayed.
    if object_type == "CHANNEL" and len(campaigns) == 1 and metric_snapshots:
        campaign = campaigns[0]
        snapshot = metric_snapshots[0][0]
        if snapshot.period_to:
            from app.ads.attribution import window_bounds
            start, end = window_bounds(end_date=snapshot.period_to, window="7d")
            memberships = list((await db.scalars(select(TrafficMembership).join(
                TrafficVisit, TrafficVisit.id == TrafficMembership.attributed_visit_id
            ).where(
                TrafficMembership.channel_id == object_id,
                TrafficMembership.attribution_status == "EXACT",
                TrafficMembership.joined_at >= start,
                TrafficMembership.joined_at < end,
            ))).all())
            matched_exact = 0
            for membership in memberships:
                visit = await db.get(TrafficVisit, membership.attributed_visit_id)
                if visit and campaign_matches(visit.utm_campaign, campaign.external_id):
                    matched_exact += 1
            confirmed = matched_exact
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
        *_scope(AdsMemory, object_type, object_id), AdsMemory.destination_platform.in_(destination_platforms), AdsMemory.status.in_(VERIFIED_MEMORY_STATES))
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
    memory_rows = list((await db.scalars(select(AdsMemory).where(
        *_scope(AdsMemory, object_type, object_id),
        AdsMemory.destination_platform.in_(destination_platforms),
        AdsMemory.experiment_id.is_not(None),
    ).order_by(AdsMemory.updated_at.desc()).limit(100))).all())
    memories_by_experiment: dict[str, list[AdsMemory]] = {}
    for memory in memory_rows:
        memories_by_experiment.setdefault(memory.experiment_id, []).append(memory)
    memory_by_id = {memory.id: memory for memory in memory_rows}
    evidence_rows = list((await db.scalars(select(AdsMemoryEvidence).where(
        AdsMemoryEvidence.memory_id.in_(list(memory_by_id))
    ))).all()) if memory_by_id else []
    evidence_by_memory: dict[str, list[AdsMemoryEvidence]] = {}
    for evidence in evidence_rows:
        evidence_by_memory.setdefault(evidence.memory_id, []).append(evidence)
    experiment_seq_by_id = {
        item.experiment_id: item.experiment_seq for item in all_recommendations
        if item.experiment_id and item.experiment_seq is not None
    }

    def _memory_ids(value: str | None) -> list[str]:
        parsed = _json(value, [])
        return [str(item) for item in parsed] if isinstance(parsed, list) else []

    def _memory_summary(memory: AdsMemory) -> dict[str, Any]:
        # Technical IDs stay in the server-side projection only.  The bot
        # formatter renders the claim/status and experiment numbers instead.
        source_experiments = sorted({
            experiment_seq_by_id.get(evidence.experiment_id)
            for evidence in evidence_by_memory.get(memory.id, [])
            if experiment_seq_by_id.get(evidence.experiment_id) is not None
        })
        return {
            "claim": memory.content,
            "status": str(memory.status or "UNKNOWN").upper(),
            "confidence": str(memory.confidence or "UNKNOWN").upper(),
            "source_experiments": source_experiments,
            "evidence_count": len(evidence_by_memory.get(memory.id, [])),
        }

    memories_used_by_experiment: dict[str, list[int]] = {}
    for recommendation_row in all_recommendations:
        for memory_id in _memory_ids(recommendation_row.influenced_by_memory_ids_json):
            memory = memory_by_id.get(memory_id)
            if not memory or not memory.experiment_id:
                continue
            seq = recommendation_row.experiment_seq
            if seq is not None:
                memories_used_by_experiment.setdefault(memory.experiment_id, []).append(seq)
    creative_ids = {value for item in all_recommendations for value in (
        item.control_creative_id, item.treatment_creative_id, item.control_ad_entity_id, item.treatment_ad_entity_id
    ) if value}
    creative_rows = list((await db.scalars(select(AdsCreative).where(AdsCreative.id.in_(creative_ids)))).all()) if creative_ids else []
    entity_rows = list((await db.scalars(select(AdsEntity).where(AdsEntity.id.in_(creative_ids)))).all()) if creative_ids else []
    creative_by_id = {item.id: item for item in creative_rows}
    entity_by_id = {item.id: item for item in entity_rows}
    for item in all_recommendations:
        # Only canonical experiments belong in the user-facing history.
        if not item.experiment_id or item.experiment_seq is None:
            continue
        measured = next((value for value in outcomes_by_recommendation.get(item.id, [])
                         if value.status == "COMPLETED"), None)
        latest = next(iter(outcomes_by_recommendation.get(item.id, [])), None)
        run = history_run_by_id.get(item.ai_run_id)
        campaign = campaign_by_id.get(run.campaign_id) if run else None
        policy_payload = _json(item.policy_json, {})
        problem_payload = _json(item.problem_json, {})
        delta = _json(measured.observed_delta_json, {}) if measured else (_json(latest.observed_delta_json, {}) if latest else {})
        own_memories = memories_by_experiment.get(item.experiment_id, [])
        influenced_ids = _memory_ids(item.influenced_by_memory_ids_json)
        avoided_ids = _memory_ids(item.avoided_due_to_memory_ids_json)
        used_knowledge = [_memory_summary(memory) for memory in
                          (memory_by_id[memory_id] for memory_id in influenced_ids if memory_id in memory_by_id)]
        avoided_knowledge = [_memory_summary(memory) for memory in
                             (memory_by_id[memory_id] for memory_id in avoided_ids if memory_id in memory_by_id)]
        learned = [_memory_summary(memory) for memory in own_memories]
        next_experiments = sorted(set(memories_used_by_experiment.get(item.experiment_id, [])))
        result = str(measured.evaluation_status if measured else "PENDING").upper()
        if used_knowledge:
            why_selected = "На выбор повлияли сохранённые знания: " + "; ".join(
                f"эксперимент #{seq}: {str(knowledge['claim'])[:240]}" for knowledge in used_knowledge
                for seq in (knowledge["source_experiments"] or [None])
            )
        else:
            why_selected = "Источник решения не зафиксирован"
        history.append({
            "experiment_id": item.experiment_id,
            "experiment_seq": item.experiment_seq,
            "experiment_status": item.experiment_status,
            "created_at": item.created_at,
            "started_at": item.created_at,
            "completed_at": measured.completed_at if measured else None,
            "campaign_id": run.campaign_id if run else None,
            "campaign_name": campaign.name if campaign else None,
            "title": item.title,
            "hypothesis": item.hypothesis or policy_payload.get("hypothesis") or item.rationale,
            "rationale": item.rationale or problem_payload.get("summary"),
            "action": policy_payload.get("action_type") or item.action_code,
            "objective": problem_payload.get("objective") or policy_payload.get("objective"),
            "implementation_status": measured.implementation_confirmed if measured else "UNKNOWN",
            "evaluation_status": measured.evaluation_status if measured else "PENDING",
            "delta": delta,
            "mode": "Autopilot" if _implementation_actor(measured) == "MAXIBOT" else "Recommendations",
            "executed_by": _implementation_actor(measured),
            "before": _experiment_view(item, measured).get("before"),
            "after": _experiment_view(item, measured).get("after"),
            "rollback": bool(measured and measured.execution_status == "ROLLED_BACK"),
            "status_label": _experiment_status_label({"evaluation_status": measured.evaluation_status if measured else None, "experiment_status": item.experiment_status, "implementation_confirmed": measured.implementation_confirmed if measured else None}),
            "memories": [memory.content for memory in own_memories],
            "learned_memories": learned,
            "memory_status": learned[0]["status"] if learned else None,
            "memory_claim": learned[0]["claim"] if learned else None,
            "influenced_by": used_knowledge,
            "avoided_due_to_memory": avoided_knowledge,
            "why_selected": why_selected,
            "next_experiment_seqs": next_experiments,
            "learning_chain": {
                "result": result,
                "learned": learned,
                "used_in_next": next_experiments,
                "maturity_before": item.maturity_before,
                "maturity_after": item.maturity_after,
                "maturity_reason": item.maturity_reason,
            },
            "maturity_before": item.maturity_before,
            "maturity_after": item.maturity_after,
            "maturity_reason": item.maturity_reason,
            "creative_kind": item.experiment_kind,
            "control_creative": creative_by_id.get(item.control_creative_id).title if creative_by_id.get(item.control_creative_id) else None,
            "treatment_creative": creative_by_id.get(item.treatment_creative_id).title if creative_by_id.get(item.treatment_creative_id) else None,
            "creative_selection_reason": item.creative_selection_reason,
            "provider_lifecycle": item.provider_lifecycle,
        })
    history.sort(key=lambda row: (
        0 if row["status_label"] == "🟡 Идёт" else 1,
        -(row["created_at"].timestamp() if row.get("created_at") else 0),
    ))

    experiment_rows = []
    for item in all_recommendations:
        if not item.experiment_id or item.experiment_seq is None:
            continue
        measured = next((value for value in outcomes_by_recommendation.get(item.id, [])
                         if value.implementation_confirmed == "YES"), None)
        if measured:
            experiment_rows.append(_experiment_view(item, measured))
    active_experiment = next((item for item in experiment_rows if item.get("evaluation_status") not in {"SUCCESS", "FAIL"}), None)
    completed_experiment = next((item for item in experiment_rows if item.get("evaluation_status") in {"SUCCESS", "FAIL", "INCONCLUSIVE"}), None)
    total_experiments = len(experiment_rows)
    successful_experiments = sum(item.get("evaluation_status") == "SUCCESS" for item in experiment_rows)

    knowledge = []
    maturity_by_experiment = {item.experiment_id: item.maturity_after for item in all_recommendations if item.experiment_id}
    for memory in memories:
        source_experiments = sorted({
            experiment_seq_by_id.get(evidence.experiment_id)
            for evidence in evidence_by_memory.get(memory.id, [])
            if experiment_seq_by_id.get(evidence.experiment_id) is not None
        })
        knowledge.append({
            "id": memory.id, "claim": memory.content,
            "status": str(memory.status or "UNKNOWN").upper(),
            "confidence": str(memory.confidence or "UNKNOWN").upper(),
            "source_experiments": source_experiments,
            "evidence_count": evidence_counts.get(memory.id, 0),
            "maturity": maturity_by_experiment.get(memory.experiment_id),
        })
    knowledge_summary = {
        "validated": sum(item["status"] in {"VALIDATED", "TRUSTED"} for item in knowledge),
        "supported": sum(item["status"] == "SUPPORTED" for item in knowledge),
        "rejected": sum(item["status"] == "REJECTED" for item in knowledge),
    }

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
        "knowledge": knowledge, "knowledge_summary": knowledge_summary,
        "history": history,
        "current_experiment": active_experiment,
        "last_completed_experiment": completed_experiment,
        "learning_summary": {
            "total_experiments": total_experiments,
            "successful_experiments": successful_experiments,
            "proven_memories": knowledge_summary["validated"],
            "validated_knowledge": knowledge_summary["validated"],
            "supported_knowledge": knowledge_summary["supported"],
            "rejected_knowledge": knowledge_summary["rejected"],
        },
    }
