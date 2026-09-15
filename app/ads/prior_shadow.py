"""Read-only Prior Engine shadow orchestration.

The runner writes only an `AdsAiRun` diagnostic record. It never creates an
experiment, memory, policy, maturity transition, or provider action.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ads.prior_engine import (
    CONTEXT_SIGNATURE_VERSION, SCORING_POLICY_VERSION,
    SIMILARITY_POLICY_VERSION, build_context_signature, rank_priors,
)
from app.core.config import settings
from app.db.models import AdsAiRun, AdsAnalysisSnapshot, AdsCampaign, AdsMemory

logger = logging.getLogger(__name__)
SHADOW_PROVIDER = "PRIOR_ENGINE_SHADOW"
SHADOW_SCHEMA_VERSION = "prior-shadow-v1"


def _canonical_fields(facts: Mapping[str, Any], campaign: AdsCampaign) -> dict[str, Any]:
    objective = facts.get("OBJECTIVE_AT_ANALYSIS_TIME") or {}
    campaign_facts = facts.get("campaign") or {}
    return {
        "provider": "YANDEX_DIRECT", "destination_platform": facts.get("destination_platform", "MAX"),
        "promotion_object_type": facts.get("promotion_object_type", "CHANNEL"),
        "campaign_type": campaign_facts.get("type") or getattr(campaign, "type", None),
        "provider_strategy": campaign_facts.get("strategy"),
        "business_objective": objective.get("objective_type") or objective.get("type"),
        "primary_metric": "CPA", "data_trust": (facts.get("data_health") or {}).get("status"),
        "attribution_quality": (facts.get("confidence_axes") or {}).get("attribution_confidence"),
        "experiment_policy_version": "evidence-policy-v1",
    }


def build_shadow_result(*, facts: Mapping[str, Any], campaign: AdsCampaign, memories: list[Mapping[str, Any]], snapshot_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    signature = build_context_signature(_canonical_fields(facts, campaign))
    candidates = []
    for memory in memories:
        raw = memory.get("context_signature_json")
        try:
            context_signature = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            context_signature = None
        if not context_signature:
            continue
        candidates.append({
            "id": memory.get("id"), "tenant_id": memory.get("channel_id"),
            "tenant_verified": bool(memory.get("channel_id")),
            "context_signature": context_signature,
            "scope": memory.get("scope", "PRIVATE"), "evidence": [],
            "observed_at": memory.get("updated_at"),
        })
    ranked = rank_priors(candidates, signature, tenant_id=tenant_id or getattr(campaign, "channel_id", None))
    top = ranked[0] if ranked else None
    canonical = facts.get("DIRECTOLOGIST") or {}
    canonical_action = canonical.get("action") or canonical.get("decision") or canonical.get("state")
    canonical_hypothesis = canonical.get("hypothesis")
    comparison = "SHADOW_NO_CANDIDATE" if top is None else "SAME" if (top.get("action") == canonical_action and canonical_action) else "DIFFERENT"
    return {
        "shadow_run_id": f"{campaign.id}:{snapshot_id}:{SCORING_POLICY_VERSION}",
        "campaign_id": campaign.id, "analysis_snapshot_id": snapshot_id,
        "cold_start_source": "GENERIC" if top is None else "LOCAL",
        "local_prior": None, "cohort_prior": None, "global_prior": None, "external_prior": None,
        "similar_experiments": int(top["evidence"]["experiments"]) if top else 0,
        "shadow_top_candidate": top.get("id") if top else None,
        "shadow_prior_score": float(top["score"]) if top else 0.0,
        "shadow_reason": "NO_APPLICABLE_PRIOR" if top is None else "DETERMINISTIC_SIMILARITY_RANKING",
        "matched_features": list(top["similarity"].matched) if top else [],
        "mismatched_features": list(top["similarity"].mismatched) if top else [],
        "unknown_features": list(top["similarity"].unknown) if top else list(signature["fields"]),
        "prior_artifact_version": SCORING_POLICY_VERSION,
        "context_signature_version": CONTEXT_SIGNATURE_VERSION,
        "similarity_policy_version": SIMILARITY_POLICY_VERSION,
        "scoring_policy_version": SCORING_POLICY_VERSION,
        "canonical_decision": canonical.get("decision") or canonical.get("state"),
        "canonical_candidate_action": canonical_action,
        "canonical_candidate_hypothesis": canonical_hypothesis,
        "canonical_vs_shadow": comparison,
        "context_signature": signature,
    }


async def run_prior_shadow(*, db: AsyncSession, snapshot: AdsAnalysisSnapshot, campaign: AdsCampaign, facts: Mapping[str, Any]) -> dict[str, Any] | None:
    """Execute one idempotent shadow cycle; failures never escape canonical flow."""
    if not settings.ads_prior_engine_shadow:
        return None
    existing = await db.scalar(select(AdsAiRun).where(
        AdsAiRun.provider == SHADOW_PROVIDER,
        AdsAiRun.input_snapshot_id == snapshot.id,
        AdsAiRun.schema_version == SHADOW_SCHEMA_VERSION,
    ).limit(1))
    if existing:
        try:
            return json.loads(existing.output_json or "{}")
        except (TypeError, ValueError):
            return {"status": "SHADOW_FAILED", "error": "CORRUPT_RESULT"}
    try:
        rows = await db.scalars(select(AdsMemory).where(AdsMemory.channel_id == getattr(campaign, "channel_id", None), AdsMemory.status.in_({"SUPPORTED", "VALIDATED", "TRUSTED"})))
        memories = [{"id": m.id, "channel_id": m.channel_id, "context_signature_json": m.context_signature_json, "scope": m.scope, "updated_at": m.updated_at.isoformat() if m.updated_at else None} for m in rows]
        result = build_shadow_result(facts=facts, campaign=campaign, memories=memories, snapshot_id=snapshot.id, tenant_id=snapshot.channel_id)
        ai = AdsAiRun(channel_id=snapshot.channel_id, promotion_object_type=snapshot.promotion_object_type, promotion_object_id=snapshot.promotion_object_id, destination_platform=snapshot.destination_platform, campaign_id=campaign.id, status="SHADOW_COMPLETED", provider=SHADOW_PROVIDER, model="deterministic", input_snapshot_id=snapshot.id, schema_version=SHADOW_SCHEMA_VERSION, output_json=json.dumps(result, ensure_ascii=False, sort_keys=True), safe_context_json=json.dumps({"snapshot_id": snapshot.id, "read_only": True}, ensure_ascii=False), is_test=True, completed_at=datetime.now(timezone.utc))
        db.add(ai)
        await db.flush()
        return result
    except Exception as exc:  # pragma: no cover - defensive production guard
        logger.warning("prior shadow failed; canonical flow continues: %s", exc)
        db.add(AdsAiRun(channel_id=snapshot.channel_id, campaign_id=campaign.id, status="SHADOW_FAILED", provider=SHADOW_PROVIDER, model="deterministic", input_snapshot_id=snapshot.id, schema_version=SHADOW_SCHEMA_VERSION, validation_error="SHADOW_RUN_FAILED", output_json=json.dumps({"error": "SHADOW_RUN_FAILED"}), is_test=True, completed_at=datetime.now(timezone.utc)))
        return {"status": "SHADOW_FAILED"}
