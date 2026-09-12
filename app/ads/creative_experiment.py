"""Existing-creative experiment primitives for Autopilot V1.

This module is deliberately provider-write agnostic.  It validates and
persists a safe control/treatment plan; the Control Plane remains the only
caller allowed to invoke the provider adapter.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdsAiRun, AdsCreative, AdsEntity, AdsObservation, AdsRecommendation, BotTrafficVisit, TrafficSubscription, TrafficVisit

CREATIVE_EXPERIMENT = "CREATIVE"
CREATIVE_LIFECYCLES = {"PLANNED", "CREATED", "AWAITING_MODERATION", "ACTIVE", "REJECTED", "SUSPENDED", "COMPLETED"}
REJECTED_PROVIDER_STATES = {"REJECTED", "REJECT", "DISAPPROVED", "INVALID", "ERROR"}
INACTIVE_PROVIDER_STATES = {"OFF", "SUSPENDED", "DISABLED", "ARCHIVED"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def variant_token(experiment_id: str, variant: str) -> str:
    """Opaque, bounded marker safe to put in a landing URL."""
    raw = f"MAXIBOT:CREATIVE:{experiment_id}:{variant.upper()}".encode()
    return hashlib.sha256(raw).hexdigest()[:32]


def tracking_params(experiment_id: str, variant: str) -> dict[str, str]:
    return {"maxibot_exp": variant_token(experiment_id, variant), "maxibot_variant": variant.upper()}


def creative_candidate_eligibility(
    creative: AdsCreative,
    *,
    campaign_id: str,
    control: AdsCreative | None = None,
    required_type: str | None = None,
) -> tuple[bool, str]:
    if not creative or creative.campaign_id != campaign_id:
        return False, "WRONG_SCOPE_CREATIVE"
    if control and creative.content_hash == control.content_hash:
        return False, "SAME_CONTENT_HASH"
    provider_state = _text(creative.provider_state).upper()
    if provider_state in REJECTED_PROVIDER_STATES:
        return False, "CREATIVE_PROVIDER_REJECTED"
    if provider_state in INACTIVE_PROVIDER_STATES:
        return False, "CREATIVE_NOT_PROVIDER_READY"
    kind = _text(creative.creative_type).upper()
    if required_type and required_type.upper() not in kind:
        return False, "WRONG_FORMAT_CREATIVE"
    # V1 sibling creation supports an existing image reference.  Video upload
    # or attachment is intentionally outside this scope.
    if "VIDEO" in kind or (not creative.image_ref and not creative.external_creative_id):
        return False, "CREATIVE_REFERENCE_UNAVAILABLE"
    if not creative.content_hash:
        return False, "CREATIVE_HASH_REQUIRED"
    return True, "ELIGIBLE"


def select_existing_creative(
    creatives: Iterable[AdsCreative],
    *,
    campaign_id: str,
    control: AdsCreative | None = None,
    required_type: str | None = None,
    historical_winner_hashes: set[str] | None = None,
) -> tuple[AdsCreative | None, str]:
    winners = historical_winner_hashes or set()
    eligible: list[tuple[int, float, str, AdsCreative]] = []
    for creative in creatives:
        ok, _ = creative_candidate_eligibility(creative, campaign_id=campaign_id, control=control, required_type=required_type)
        if not ok:
            continue
        score = 100 if creative.content_hash in winners else 0
        timestamp = creative.updated_at.timestamp() if creative.updated_at else 0.0
        eligible.append((score, timestamp, creative.id, creative))
    if not eligible:
        return None, "NO_ELIGIBLE_CREATIVE_CANDIDATE"
    eligible.sort(key=lambda row: (-row[0], -row[1], row[2]))
    chosen = eligible[0][3]
    reason = "Выбран существующий provider-ready creative с уникальным content hash"
    if chosen.content_hash in winners:
        reason += "; ранее подтверждён как успешный"
    return chosen, reason


def _creative_signature(payload: dict[str, Any]) -> Any:
    for key in ("creative", "AdImageHash", "ImageHash", "Creative", "VideoExtension", "AdImages"):
        if key in payload:
            return payload[key]
    return None


def normalized_creative_comparison(control_payload: dict[str, Any], treatment_payload: dict[str, Any]) -> dict[str, Any]:
    """Compare ad payloads while treating creative and attribution as special."""
    control = control_payload or {}
    treatment = treatment_payload or {}
    keys = sorted(set(control) | set(treatment))
    differences: list[str] = []
    for key in keys:
        if key in {"creative", "Creative", "AdImageHash", "ImageHash", "AdImages", "VideoExtension", "tracking", "Href"}:
            continue
        if control.get(key) != treatment.get(key):
            differences.append(key.lower())
    control_creative = _creative_signature(control)
    treatment_creative = _creative_signature(treatment)
    if control_creative == treatment_creative:
        differences.insert(0, "creative")
        return {"differing_business_variables": sorted(set(differences)), "allowed": False, "reason": "SAME_CREATIVE"}
    differences.insert(0, "creative")
    return {"differing_business_variables": sorted(set(differences)), "allowed": sorted(set(differences)) == ["creative"]}


def assert_one_creative_variable(control_payload: dict[str, Any], treatment_payload: dict[str, Any]) -> dict[str, Any]:
    result = normalized_creative_comparison(control_payload, treatment_payload)
    if not result["allowed"]:
        raise ValueError("CREATIVE_EXPERIMENT_ONLY_CREATIVE_VARIABLE_REQUIRED:" + ",".join(result["differing_business_variables"]))
    return result


async def persist_creative_mapping(
    db: AsyncSession,
    recommendation: AdsRecommendation,
    *,
    control_entity: AdsEntity,
    control_creative: AdsCreative,
    treatment_entity: AdsEntity | None = None,
    treatment_creative: AdsCreative | None = None,
    selection_reason: str,
) -> AdsRecommendation:
    if control_entity.campaign_id != control_creative.campaign_id:
        raise ValueError("CONTROL_CREATIVE_ENTITY_SCOPE_MISMATCH")
    if treatment_creative and treatment_creative.campaign_id != control_creative.campaign_id:
        raise ValueError("TREATMENT_CREATIVE_SCOPE_MISMATCH")
    if treatment_creative:
        eligible, reason = creative_candidate_eligibility(treatment_creative, campaign_id=control_creative.campaign_id, control=control_creative)
        if not eligible:
            raise ValueError(reason)
    recommendation.experiment_kind = CREATIVE_EXPERIMENT
    recommendation.control_ad_entity_id = control_entity.id
    recommendation.control_creative_id = control_creative.id
    recommendation.treatment_ad_entity_id = treatment_entity.id if treatment_entity else None
    recommendation.treatment_creative_id = treatment_creative.id if treatment_creative else None
    recommendation.creative_selection_reason = selection_reason[:4000]
    recommendation.provider_lifecycle = "PLANNED"
    recommendation.control_variant_token = variant_token(recommendation.experiment_id or recommendation.id, "CONTROL")
    recommendation.treatment_variant_token = variant_token(recommendation.experiment_id or recommendation.id, "TREATMENT")
    await db.flush()
    return recommendation


async def resolve_variant(
    db: AsyncSession,
    *,
    object_type: str,
    object_id: str,
    experiment_token: str | None = None,
    provider_ad_id: str | None = None,
) -> dict[str, Any] | None:
    """Resolve a landing click to one scoped experiment variant."""
    rows = list((await db.scalars(select(AdsRecommendation).where(
        AdsRecommendation.promotion_object_type == object_type,
        AdsRecommendation.promotion_object_id == object_id,
        AdsRecommendation.experiment_kind == CREATIVE_EXPERIMENT,
        AdsRecommendation.experiment_id.is_not(None),
    ).order_by(AdsRecommendation.created_at.desc()).limit(100))).all())
    for recommendation in rows:
        variant = None
        entity_id = None
        creative_id = None
        if experiment_token and experiment_token == recommendation.control_variant_token:
            variant, entity_id, creative_id = "CONTROL", recommendation.control_ad_entity_id, recommendation.control_creative_id
        elif experiment_token and experiment_token == recommendation.treatment_variant_token:
            variant, entity_id, creative_id = "TREATMENT", recommendation.treatment_ad_entity_id, recommendation.treatment_creative_id
        elif provider_ad_id:
            entities = [item for item in (recommendation.control_ad_entity_id, recommendation.treatment_ad_entity_id) if item]
            matched = await db.scalar(select(AdsEntity).where(AdsEntity.id.in_(entities), AdsEntity.provider_entity_id == str(provider_ad_id))) if entities else None
            if matched:
                variant = "CONTROL" if matched.id == recommendation.control_ad_entity_id else "TREATMENT"
                entity_id = matched.id
                creative_id = recommendation.control_creative_id if variant == "CONTROL" else recommendation.treatment_creative_id
        if variant:
            entity = await db.get(AdsEntity, entity_id) if entity_id else None
            creative = await db.get(AdsCreative, creative_id) if creative_id else None
            return {"experiment_id": recommendation.experiment_id, "variant": variant, "provider_ad_id": entity.provider_entity_id if entity else None, "provider_creative_id": creative.external_creative_id if creative else None}
    return None


def provider_lifecycle(provider_row: dict[str, Any] | None, *, created: bool = False) -> str:
    row = provider_row or {}
    state = _text(row.get("State") or row.get("state")).upper()
    status = _text(row.get("Status") or row.get("status")).upper()
    combined = {state, status}
    if combined & REJECTED_PROVIDER_STATES:
        return "REJECTED"
    if combined & INACTIVE_PROVIDER_STATES:
        return "SUSPENDED"
    if state == "ON" and status in {"ACCEPTED", "ON", "ELIGIBLE", "RUNNING", "ACTIVE"}:
        return "ACTIVE"
    return "CREATED" if created else "AWAITING_MODERATION"


def measurement_start(lifecycle: str, *, now: datetime | None = None) -> datetime | None:
    if lifecycle != "ACTIVE":
        return None
    return now or datetime.now(timezone.utc)


def creative_history_view(recommendation: AdsRecommendation, creatives: dict[str, AdsCreative], entities: dict[str, AdsEntity]) -> dict[str, Any]:
    control = creatives.get(recommendation.control_creative_id or "")
    treatment = creatives.get(recommendation.treatment_creative_id or "")
    control_entity = entities.get(recommendation.control_ad_entity_id or "")
    treatment_entity = entities.get(recommendation.treatment_ad_entity_id or "")
    def view(creative: AdsCreative | None, entity: AdsEntity | None) -> dict[str, Any] | None:
        if not creative:
            return None
        return {"label": creative.title or creative.external_creative_id or "Существующий креатив", "content_hash": creative.content_hash, "provider_creative_id": creative.external_creative_id, "provider_ad_id": entity.provider_entity_id if entity else None}
    return {"kind": recommendation.experiment_kind, "selection_reason": recommendation.creative_selection_reason, "control": view(control, control_entity), "treatment": view(treatment, treatment_entity), "provider_lifecycle": recommendation.provider_lifecycle}


async def creative_variant_metrics(
    db: AsyncSession,
    recommendation: AdsRecommendation,
    *,
    date_from=None,
    date_to=None,
) -> dict[str, dict[str, Any]]:
    """Aggregate provider and durable business metrics by CONTROL/TREATMENT."""
    result = {"CONTROL": {"impressions": 0.0, "clicks": 0.0, "spend": 0.0, "subscriptions": 0, "qualified_subscriptions": None},
              "TREATMENT": {"impressions": 0.0, "clicks": 0.0, "spend": 0.0, "subscriptions": 0, "qualified_subscriptions": None}}
    entity_to_variant = {recommendation.control_ad_entity_id: "CONTROL", recommendation.treatment_ad_entity_id: "TREATMENT"}
    entity_ids = [key for key in entity_to_variant if key]
    if entity_ids:
        query = select(AdsObservation).where(AdsObservation.entity_id.in_(entity_ids))
        if date_from is not None:
            query = query.where(AdsObservation.date_to >= date_from)
        if date_to is not None:
            query = query.where(AdsObservation.date_from <= date_to)
        for row in (await db.scalars(query)).all():
            bucket = result[entity_to_variant[row.entity_id]]
            bucket["impressions"] += float(row.impressions or 0)
            bucket["clicks"] += float(row.clicks or 0)
            bucket["spend"] += float(row.cost or 0)
    experiment_id = recommendation.experiment_id
    if experiment_id:
        for variant in result:
            channel_rows = await db.scalar(select(func.count(TrafficSubscription.id)).join(
                TrafficVisit, TrafficVisit.id == TrafficSubscription.visitor_id
            ).where(TrafficVisit.experiment_id == experiment_id, TrafficVisit.experiment_variant == variant, TrafficSubscription.status == "active"))
            bot_rows = await db.scalar(select(func.count(BotTrafficVisit.id)).where(
                BotTrafficVisit.experiment_id == experiment_id, BotTrafficVisit.experiment_variant == variant, BotTrafficVisit.status == "BOT_STARTED"))
            result[variant]["subscriptions"] = int(channel_rows or 0) + int(bot_rows or 0)
    for bucket in result.values():
        bucket["cpa"] = (bucket["spend"] / bucket["subscriptions"]) if bucket["subscriptions"] else None
    return result


async def finalize_creative_experiment(
    db: AsyncSession,
    *,
    recommendation: AdsRecommendation,
    outcome,
    campaign_provider_id: str,
    control_provider_ad_id: str | None,
    treatment_provider_ad_id: str | None,
    provider_execute,
    suspend_control_on_success: bool = False,
) -> dict[str, Any]:
    """Finalize a sibling-ad experiment through an injected provider callback."""
    status = str(outcome.evaluation_status or "INCONCLUSIVE").upper()
    if recommendation.provider_lifecycle == "REJECTED" or status == "PROVIDER_REJECTED":
        recommendation.provider_lifecycle = "COMPLETED"
        recommendation.experiment_status = "completed"
        outcome.execution_status = "PROVIDER_REJECTED"
        outcome.evaluation_status = "PROVIDER_REJECTED"
        return {"status": "PROVIDER_REJECTED", "control_preserved": True, "learning_allowed": False}
    if not treatment_provider_ad_id:
        raise ValueError("TREATMENT_PROVIDER_AD_REQUIRED")
    actions: list[dict[str, Any]] = []
    if status in {"FAIL", "INCONCLUSIVE"}:
        actions.append(await provider_execute({"action_type": "PAUSE_AD", "provider_campaign_id": campaign_provider_id, "target": {"ad_id": treatment_provider_ad_id}}))
        result = "FAIL_ROLLBACK" if status == "FAIL" else "INCONCLUSIVE_SAFE_CLOSE"
    elif status == "SUCCESS":
        if suspend_control_on_success:
            if not control_provider_ad_id:
                raise ValueError("CONTROL_PROVIDER_AD_REQUIRED")
            actions.append(await provider_execute({"action_type": "PAUSE_AD", "provider_campaign_id": campaign_provider_id, "target": {"ad_id": control_provider_ad_id}}))
        result = "SUCCESS_KEEP_TREATMENT"
    else:
        raise ValueError("CREATIVE_OUTCOME_STATUS_UNSUPPORTED")
    recommendation.provider_lifecycle = "COMPLETED"
    recommendation.experiment_status = "completed"
    outcome.execution_status = "FINALIZED" if status == "SUCCESS" else "ROLLED_BACK"
    evidence = {"creative_finalization": result, "actions": actions, "executed_by": "MAXIBOT"}
    outcome.implementation_evidence_json = json.dumps(evidence, ensure_ascii=False)
    await db.flush()
    return {"status": result, "actions": actions, "evidence": evidence}
