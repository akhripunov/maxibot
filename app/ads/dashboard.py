"""Deterministic object-level AI Promotion dashboard view model.

This module is a read-only projection over the existing ADS tables.  It does
not create persistence, issue provider writes, or introduce a second decision
engine.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AdsAiOutcome, AdsAiRun, AdsAnalysisSnapshot, AdsCampaign, AdsCreative,
    AdsMemory, AdsPromotionCampaignLink, AdsRecommendation,
)


def _json(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


def _scope(object_type: str, object_id: str):
    if object_type == "CHANNEL":
        return AdsPromotionCampaignLink.object_type == "CHANNEL", AdsPromotionCampaignLink.channel_id == object_id
    if object_type == "BOT":
        return AdsPromotionCampaignLink.object_type == "BOT", AdsPromotionCampaignLink.bot_promotion_profile_id == object_id
    raise ValueError("ADS_PROMOTION_OBJECT_TYPE_UNSUPPORTED")


def stage_for_state(*, recommendation: Any, outcome: Any, health: str, has_data: bool, learning_proven: bool) -> str:
    """Map canonical Closed-Loop state to one user-facing stage."""
    if learning_proven and outcome and outcome.evaluation_status == "SUCCESS":
        return "SCALE"
    if outcome and outcome.implementation_confirmed == "YES" and outcome.evaluation_status not in {"SUCCESS", "FAIL"}:
        return "CHECK"
    if outcome and outcome.evaluation_status in {"SUCCESS", "FAIL"}:
        return "RESULT"
    if recommendation and recommendation.status == "OPEN" and recommendation.data_sufficiency != "INSUFFICIENT":
        return "RECOMMENDATION"
    return "ANALYSIS" if not has_data or health not in {"HEALTHY", "PARTIAL"} else "ANALYSIS"


def business_cpa(*, spend: float | None, confirmed_outcomes: int | None) -> float | None:
    """Return None for UNKNOWN or zero outcomes; never turn UNKNOWN into 0."""
    if spend is None or confirmed_outcomes is None or confirmed_outcomes <= 0:
        return None
    return float(spend) / confirmed_outcomes


async def build_ads_dashboard(db: AsyncSession, *, object_type: str, object_id: str) -> dict[str, Any]:
    """Build one bounded dashboard for a CHANNEL or BOT promotion object."""
    link_scope = _scope(object_type, object_id)
    links = list((await db.scalars(
        select(AdsPromotionCampaignLink).where(*link_scope).order_by(AdsPromotionCampaignLink.created_at)
    )).all())
    campaign_ids = [link.campaign_id for link in links]
    campaigns = list((await db.scalars(
        select(AdsCampaign).where(AdsCampaign.id.in_(campaign_ids)).order_by(AdsCampaign.name)
    )).all()) if campaign_ids else []
    campaign_by_id = {item.id: item for item in campaigns}
    active_links = {link.campaign_id for link in links if link.status == "ACTIVE"}
    active_campaigns = [campaign_by_id[cid] for cid in campaign_ids if cid in active_links and cid in campaign_by_id and campaign_by_id[cid].state not in {"ARCHIVED", "SUSPENDED", "DISABLED"}]

    run_scope = (AdsAiRun.channel_id == object_id) if object_type == "CHANNEL" else (AdsAiRun.promotion_object_type == object_type, AdsAiRun.promotion_object_id == object_id)
    runs = list((await db.scalars(
        select(AdsAiRun).where(*((run_scope,) if not isinstance(run_scope, tuple) else run_scope)).order_by(AdsAiRun.created_at.desc()).limit(20)
    )).all())
    run_ids = [item.id for item in runs]
    recommendations = list((await db.scalars(
        select(AdsRecommendation).where(AdsRecommendation.ai_run_id.in_(run_ids)).order_by(AdsRecommendation.created_at.desc())
    )).all()) if run_ids else []
    recommendation = next((item for item in recommendations if item.status == "OPEN"), recommendations[0] if recommendations else None)
    rec_ids = [item.id for item in recommendations]
    outcomes = list((await db.scalars(
        select(AdsAiOutcome).where(AdsAiOutcome.recommendation_id.in_(rec_ids)).order_by(AdsAiOutcome.due_at.desc())
    )).all()) if rec_ids else []
    outcome = next((item for item in outcomes if item.status in {"PENDING", "PROCESSING", "COMPLETED"}), outcomes[0] if outcomes else None)

    snapshots = []
    for campaign in active_campaigns:
        snapshot = await db.scalar(select(AdsAnalysisSnapshot).where(
            AdsAnalysisSnapshot.campaign_id == campaign.id,
            AdsAnalysisSnapshot.source != "PERIODIC_GRANULAR",
            AdsAnalysisSnapshot.promotion_object_type == object_type,
            AdsAnalysisSnapshot.promotion_object_id == object_id,
        ).order_by(AdsAnalysisSnapshot.created_at.desc()).limit(1))
        if snapshot:
            snapshots.append(snapshot)
    facts_by_campaign = [_json(item.facts_json, {}) for item in snapshots]
    latest_facts = facts_by_campaign[0] if facts_by_campaign else {}
    fact_values = latest_facts.get("facts") or {}
    metrics = latest_facts.get("MAXIBOT_METRICS") or {}
    window = metrics.get("7d") or {}
    confirmed = window.get("qualified_memberships")
    if confirmed is None:
        confirmed = window.get("qualified_outcomes")
    spend_values = [((facts.get("facts") or {}).get("cost_7d")) for facts in facts_by_campaign]
    spend = sum(float(value) for value in spend_values if value is not None) if any(value is not None for value in spend_values) else None
    health = latest_facts.get("data_health") or {}
    if not health:
        health = {"status": "UNKNOWN"}
    memories = list((await db.scalars(
        select(AdsMemory).where(
            *((AdsMemory.channel_id == object_id,) if object_type == "CHANNEL" else (AdsMemory.promotion_object_type == object_type, AdsMemory.promotion_object_id == object_id)),
            AdsMemory.status.in_(("VALIDATED", "TRUSTED")),
        ).order_by(AdsMemory.updated_at.desc()).limit(12)
    )).all())
    learning_proven = bool(memories and any(item.status == "TRUSTED" for item in memories))
    creatives = list((await db.scalars(
        select(AdsCreative).where(AdsCreative.campaign_id.in_([item.id for item in active_campaigns])).order_by(AdsCreative.updated_at.desc())
    )).all()) if active_campaigns else []
    cpa = business_cpa(spend=spend, confirmed_outcomes=int(confirmed) if confirmed is not None else None)
    root_cause = _json(recommendation.root_cause_json, {}) if recommendation else {}
    primary_root_cause = root_cause.get("primary") or root_cause.get("label") or (recommendation.title if recommendation else None)
    state = stage_for_state(recommendation=recommendation, outcome=outcome, health=str(health.get("status") or "UNKNOWN"), has_data=bool(facts_by_campaign), learning_proven=learning_proven)
    objective_type = next((campaign.optimization_goal for campaign in active_campaigns if campaign.optimization_goal), None)
    target_cpa = next((float(campaign.target_cpa) for campaign in active_campaigns if campaign.target_cpa is not None), None)
    return {
        "object": {"type": object_type, "id": object_id},
        "objective": {"type": objective_type or ("max_subscriber" if object_type == "CHANNEL" else "bot_started"), "status": "ACTIVE" if active_campaigns else "UNKNOWN"},
        "target_cpa": target_cpa,
        "actual_business_cpa": cpa,
        "confirmed_business_outcomes": int(confirmed) if confirmed is not None else None,
        "business_outcome_name": "max_subscriber" if object_type == "CHANNEL" else "bot_started",
        "creative_summary": {"active": len(creatives), "attention": 0, "status": "OK" if creatives else "NONE"},
        "current_stage": state,
        "primary_root_cause": primary_root_cause,
        "why_summary": recommendation.rationale if recommendation else None,
        "next_best_action": recommendation.title if recommendation else "Продолжать сбор данных.",
        "last_updated_at": max((item.created_at for item in snapshots), default=None),
        "data_trust": health.get("status", "UNKNOWN"),
        "active_campaigns": [{"id": item.id, "external_id": item.external_id, "name": item.name, "state": item.state, "active": item.id in active_links} for item in campaigns],
        "current_recommendation": recommendation,
        "current_outcome": outcome,
        "learning_state": {"status": "PROVEN" if learning_proven else "EXPLORE", "verified_count": len(memories)},
        "memories": memories,
        "creatives": creatives,
    }
