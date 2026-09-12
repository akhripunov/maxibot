"""Bounded, opt-in execution policy for AI Promotion Autopilot V1.

This module is deliberately provider-neutral.  It validates every proposed
action before a provider adapter is called and fails closed when the object is
not explicitly enabled, data trust is insufficient, or a guardrail is
missing.  No table outside the canonical ADS model is introduced.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import AdsAccount, AdsAiOutcome, AdsAiRun, AdsCampaign, AdsCreative, AdsEntity, AdsPromotionCampaignLink, AdsRecommendation
from app.ads.creative_experiment import assert_one_creative_variable, persist_creative_mapping, provider_lifecycle, measurement_start
from app.ads.creative_builder import build_creative_candidate
from app.ads.creative_storage import store_media_if_available


async def reserve_experiment_budget(db: AsyncSession, *, recommendation: AdsRecommendation,
                                    object_type: str, object_id: str, campaign_id: str,
                                    actual_spend: float, requested: float, monthly_limit: float) -> bool:
    """Durable reservation under the canonical promotion-link row lock."""
    link = await db.scalar(select(AdsPromotionCampaignLink).where(
        AdsPromotionCampaignLink.object_type == object_type,
        (AdsPromotionCampaignLink.channel_id == object_id if object_type == "CHANNEL" else AdsPromotionCampaignLink.bot_promotion_profile_id == object_id),
        AdsPromotionCampaignLink.campaign_id == campaign_id,
    ).with_for_update())
    if not link:
        return False
    reserved = await db.scalar(select(func.coalesce(func.sum(AdsRecommendation.reserved_budget), 0)).where(
        AdsRecommendation.reservation_status == "HELD",
        AdsRecommendation.promotion_object_type == object_type,
        AdsRecommendation.promotion_object_id == object_id,
    ))
    if float(actual_spend or 0) + float(reserved or 0) + max(0.0, float(requested or 0)) > float(monthly_limit):
        return False
    recommendation.reserved_budget = max(0.0, float(requested or 0))
    recommendation.experiment_budget_cap = float(monthly_limit)
    recommendation.reservation_status = "HELD"
    await db.flush()
    return True


async def release_experiment_budget(db: AsyncSession, recommendation: AdsRecommendation, *, reason: str) -> None:
    recommendation.reservation_status = "RELEASED"
    recommendation.reserved_budget = 0
    recommendation.maturity_reason = (recommendation.maturity_reason or "") + f"; reservation released: {reason}"
    await db.flush()

ADVISORY = "ADVISORY"
AUTONOMOUS = "AUTONOMOUS"
DEFAULT_MAX_TEST_SPEND = 1000.0
DEFAULT_MAX_BUDGET_CHANGE_PERCENT = 10.0
DEFAULT_MAX_PARALLEL_EXPERIMENTS = 1
DEFAULT_TARGET_CPA_TOLERANCE = 0.20
DEFAULT_STOP_LOSS_MULTIPLE = 1.5
DEFAULT_MIN_EFFECT = 0.05
DEFAULT_MIN_EVIDENCE = 10
AUTOPILOT_ACTION_ALLOWLIST = frozenset({
    "EXCLUDE_PLACEMENT", "RESTORE_PLACEMENT", "PAUSE_AD", "ENABLE_AD",
    "CREATE_CREATIVE_TEST", "CREATE_AD_TEST", "BOUNDED_BUDGET_REALLOCATION",
})
_AD_ACTIONS = frozenset({"PAUSE_AD", "ENABLE_AD"})
_CREATIVE_ACTIONS = frozenset({"CREATE_AD_TEST", "CREATE_CREATIVE_TEST"})


def assess_provider_capabilities(*, campaign_id: str | int, campaign_state: str | None,
                                 adgroups: list[dict[str, Any]] | None = None,
                                 ads: list[dict[str, Any]] | None = None,
                                 creatives_visible: bool = False,
                                 placement_state_visible: bool = False,
                                 budget_state_visible: bool = False,
                                 ad_write_supported: bool = True,
                                 ad_reread_supported: bool = True,
                                 ad_rollback_supported: bool = True,
                                 ad_create_supported: bool = True,
                                 ad_create_reread_supported: bool = True,
                                 ad_create_rollback_supported: bool = True) -> dict[str, Any]:
    """Build a fail-closed, per-action provider capability assessment.

    Campaigns.get state is intentionally independent from child-object
    ownership.  A report row or a local campaign link never proves a write
    capability; every ad must point to an AdGroup whose provider CampaignId
    matches the exact authorized campaign.
    """
    exact_campaign_id = str(campaign_id)
    groups = [row for row in (adgroups or []) if str(row.get("CampaignId") or "") == exact_campaign_id]
    group_ids = {str(row.get("Id")) for row in groups if row.get("Id") is not None}
    owned_ads = [row for row in (ads or []) if str(row.get("CampaignId") or "") == exact_campaign_id
                 and str(row.get("AdGroupId") or "") in group_ids]
    capabilities: list[str] = []
    evidence: list[dict[str, Any]] = []
    if owned_ads and ad_write_supported and ad_reread_supported and ad_rollback_supported:
        capabilities.extend(sorted(_AD_ACTIONS))
        evidence.append({"capability": "PAUSE_AD/ENABLE_AD", "ad_ids": [str(row.get("Id")) for row in owned_ads], "ad_group_ids": sorted(group_ids), "ownership": "provider_verified", "reread": True, "rollback": True})
    if groups and ad_create_supported and ad_create_reread_supported and ad_create_rollback_supported:
        capabilities.extend(sorted(_CREATIVE_ACTIONS))
        evidence.append({"capability": "CREATE_AD_TEST/CREATE_CREATIVE_TEST", "ad_group_ids": sorted(group_ids), "ownership": "provider_verified", "reread": True, "rollback": True})
    # Campaign-level operations remain blocked while campaign state or the
    # corresponding exact campaign object is not independently readable.
    if str(campaign_state or "").upper() == "ON" and placement_state_visible:
        capabilities.extend(["EXCLUDE_PLACEMENT", "RESTORE_PLACEMENT"])
    if str(campaign_state or "").upper() == "ON" and budget_state_visible:
        capabilities.append("BOUNDED_BUDGET_REALLOCATION")
    capabilities = sorted(set(capabilities))
    manageability = "FULL" if {"EXCLUDE_PLACEMENT", "RESTORE_PLACEMENT", "BOUNDED_BUDGET_REALLOCATION"}.issubset(capabilities) else "PARTIAL" if capabilities else "NONE"
    return {
        "provider_campaign_state": str(campaign_state or "UNKNOWN").upper(),
        "provider_manageability": manageability,
        "verified_capabilities": capabilities,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "verification_evidence": evidence,
        "campaign_id": exact_campaign_id,
        "campaign_state_source": "Campaigns.get" if campaign_state else "UNAVAILABLE",
        "creatives_visible": bool(creatives_visible),
    }


def store_provider_capability_assessment(campaign: AdsCampaign, assessment: dict[str, Any]) -> dict[str, Any]:
    """Persist the bounded assessment in the existing campaign metadata JSON."""
    try:
        metadata = json.loads(campaign.provider_metadata_json or "{}")
    except (TypeError, ValueError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["provider_capability_assessment"] = assessment
    campaign.provider_metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    return assessment


def stored_provider_capabilities(campaign: AdsCampaign | None) -> dict[str, Any]:
    """Read only the canonical capability assessment, never infer capabilities."""
    if not campaign:
        return {"provider_manageability": "NONE", "verified_capabilities": []}
    try:
        metadata = json.loads(campaign.provider_metadata_json or "{}")
    except (TypeError, ValueError):
        metadata = {}
    value = metadata.get("provider_capability_assessment") if isinstance(metadata, dict) else None
    return value if isinstance(value, dict) and isinstance(value.get("verified_capabilities"), list) else {
        "provider_manageability": "NONE", "verified_capabilities": []
    }
FUTURE_ACTIONS = frozenset({"CREATE_CAMPAIGN", "CREATE_AD_GROUP", "CHANGE_TARGETING", "CHANGE_STRATEGY", "CHANGE_BID", "SCALE_CAMPAIGN"})


def default_autopilot_config() -> dict[str, Any]:
    return {
        "monthly_budget_limit": None,
        "weekly_budget_limit": None,
        "max_test_spend": DEFAULT_MAX_TEST_SPEND,
        "max_budget_change_percent": DEFAULT_MAX_BUDGET_CHANGE_PERCENT,
        "max_parallel_experiments": DEFAULT_MAX_PARALLEL_EXPERIMENTS,
        "target_cpa_tolerance": DEFAULT_TARGET_CPA_TOLERANCE,
        "stop_loss_multiple": DEFAULT_STOP_LOSS_MULTIPLE,
        "min_effect": DEFAULT_MIN_EFFECT,
        "min_evidence": DEFAULT_MIN_EVIDENCE,
        "max_duration_hours": 168,
        "cooldown_hours": 24,
        "allowed_actions": sorted(AUTOPILOT_ACTION_ALLOWLIST),
    }


def parse_config(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        value = {}
    result = default_autopilot_config()
    if isinstance(value, dict):
        result.update(value)
    return result


def _positive(value: Any, *, field: str) -> float:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{field}_INVALID")
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{field}_MUST_BE_POSITIVE")
    return float(parsed)


@dataclass(frozen=True)
class GuardrailDecision:
    allowed: bool
    reason: str
    action_type: str
    execution_mode: str


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    reason: str
    evidence: dict[str, Any]


def validate_action(*, execution_mode: str, action_type: str, data_trust: str,
                    active_experiments: int, monthly_spend: float = 0.0,
                    monthly_budget_limit: float | None = None,
                    proposed_spend: float = 0.0, config: dict[str, Any] | None = None,
                    target_cpa: float | None = None, actual_cpa: float | None = None,
                    attribution_quality: str = "KNOWN", experiment_spend: float = 0.0,
                    experiment_budget_cap: float | None = None, evidence_sufficient: bool = True,
                    effect_size: float | None = None, provider_ready: bool = True,
                    rollback_available: bool = True, max_duration_expired: bool = False,
                    verified_provider_capabilities: list[str] | set[str] | None = None) -> GuardrailDecision:
    config = {**default_autopilot_config(), **(config or {})}
    action_type = str(action_type or "").upper()
    mode = str(execution_mode or ADVISORY).upper()
    trust = str(data_trust or "UNKNOWN").upper()
    if mode != AUTONOMOUS:
        return GuardrailDecision(False, "EXECUTION_MODE_ADVISORY", action_type, mode)
    if not settings.ads_autopilot_enabled or not settings.ads_write_actions_enabled:
        return GuardrailDecision(False, "AUTOPILOT_GLOBAL_GUARD_DISABLED", action_type, mode)
    if action_type not in AUTOPILOT_ACTION_ALLOWLIST or action_type not in set(config.get("allowed_actions") or AUTOPILOT_ACTION_ALLOWLIST):
        return GuardrailDecision(False, "ACTION_NOT_ALLOWLISTED", action_type, mode)
    if verified_provider_capabilities is not None and action_type not in {str(item).upper() for item in verified_provider_capabilities}:
        return GuardrailDecision(False, "PROVIDER_CAPABILITY_NOT_VERIFIED", action_type, mode)
    if trust != "GOOD":
        return GuardrailDecision(False, f"DATA_TRUST_{trust}", action_type, mode)
    if not provider_ready:
        return GuardrailDecision(False, "PROVIDER_NOT_READY", action_type, mode)
    if not rollback_available:
        return GuardrailDecision(False, "ROLLBACK_NOT_AVAILABLE", action_type, mode)
    if max_duration_expired:
        return GuardrailDecision(False, "MAX_DURATION_EXPIRED", action_type, mode)
    if not evidence_sufficient:
        return GuardrailDecision(False, "MINIMUM_EVIDENCE_NOT_MET", action_type, mode)
    if str(attribution_quality).upper() == "UNKNOWN" and action_type in {"BOUNDED_BUDGET_REALLOCATION", "CREATE_AD_TEST", "CREATE_CREATIVE_TEST"}:
        return GuardrailDecision(False, "ATTRIBUTION_UNKNOWN", action_type, mode)
    max_parallel = int(config.get("max_parallel_experiments") or DEFAULT_MAX_PARALLEL_EXPERIMENTS)
    if active_experiments >= max_parallel:
        return GuardrailDecision(False, "MAX_PARALLEL_EXPERIMENTS", action_type, mode)
    limit = monthly_budget_limit if monthly_budget_limit is not None else (config.get("weekly_budget_limit") if config.get("weekly_budget_limit") is not None else config.get("monthly_budget_limit"))
    if limit is not None and monthly_spend + max(0.0, proposed_spend) > float(limit):
        return GuardrailDecision(False, "WEEKLY_BUDGET_LIMIT", action_type, mode)
    if proposed_spend > float(config.get("max_test_spend") or DEFAULT_MAX_TEST_SPEND):
        return GuardrailDecision(False, "MAX_TEST_SPEND", action_type, mode)
    cap = experiment_budget_cap if experiment_budget_cap is not None else config.get("max_test_spend")
    if cap is not None and experiment_spend + max(0.0, proposed_spend) > float(cap):
        return GuardrailDecision(False, "EXPERIMENT_BUDGET_CAP", action_type, mode)
    if target_cpa is not None and actual_cpa is not None:
        tolerance = float(config.get("target_cpa_tolerance") or DEFAULT_TARGET_CPA_TOLERANCE)
        stop_multiple = float(config.get("stop_loss_multiple") or DEFAULT_STOP_LOSS_MULTIPLE)
        if actual_cpa > float(target_cpa) * stop_multiple:
            return GuardrailDecision(False, "TARGET_CPA_STOP_LOSS", action_type, mode)
        if effect_size is not None and abs(float(effect_size)) < float(config.get("min_effect") or DEFAULT_MIN_EFFECT):
            return GuardrailDecision(False, "MINIMUM_EFFECT_NOT_MET", action_type, mode)
    return GuardrailDecision(True, "ALLOWLISTED_GUARDS_PASSED", action_type, mode)


async def active_links(db: AsyncSession, *, object_type: str, object_id: str) -> list[AdsPromotionCampaignLink]:
    predicate = (AdsPromotionCampaignLink.channel_id == object_id
                 if object_type == "CHANNEL"
                 else AdsPromotionCampaignLink.bot_promotion_profile_id == object_id)
    return list((await db.scalars(select(AdsPromotionCampaignLink).where(
        AdsPromotionCampaignLink.object_type == object_type,
        predicate,
        AdsPromotionCampaignLink.status == "ACTIVE",
    ).order_by(AdsPromotionCampaignLink.created_at))).all())


async def object_links(db: AsyncSession, *, object_type: str, object_id: str) -> list[AdsPromotionCampaignLink]:
    predicate = (AdsPromotionCampaignLink.channel_id == object_id
                 if object_type == "CHANNEL"
                 else AdsPromotionCampaignLink.bot_promotion_profile_id == object_id)
    return list((await db.scalars(select(AdsPromotionCampaignLink).where(
        AdsPromotionCampaignLink.object_type == object_type, predicate
    ).order_by(AdsPromotionCampaignLink.created_at))).all())


async def promotion_policy(db: AsyncSession, *, object_type: str, object_id: str) -> dict[str, Any]:
    links = await object_links(db, object_type=object_type, object_id=object_id)
    if not links:
        return {"execution_mode": ADVISORY, **default_autopilot_config(), "active_links": 0,
                "execution_capability": "DISABLED" if not (settings.ads_autopilot_enabled and settings.ads_write_actions_enabled) else "BLOCKED"}
    authorized = [link.campaign_id for link in links if str(link.execution_mode or ADVISORY).upper() == AUTONOMOUS]
    mode = AUTONOMOUS if authorized else ADVISORY
    source = next((link for link in links if link.campaign_id in authorized), links[0])
    config = parse_config(source.autopilot_config_json)
    capability = "READY" if settings.ads_autopilot_enabled and settings.ads_write_actions_enabled else "DISABLED"
    return {"execution_mode": mode, **config, "active_links": len(links),
            "authorized_campaign_ids": authorized,
            "write_scope": "CAMPAIGN_SCOPED", "execution_capability": capability}


async def autopilot_enable_requirements(db: AsyncSession, *, object_type: str, object_id: str,
                                        campaign_id: str | None) -> list[str]:
    """Return missing owner prerequisites before enabling campaign autopilot."""
    links = await object_links(db, object_type=object_type, object_id=object_id)
    if not links:
        return ["выбрать кампанию для автопилота"]
    selected = next((link for link in links if link.campaign_id == campaign_id), None)
    if not selected:
        return ["выбрать кампанию для автопилота"]
    missing: list[str] = []
    if not selected.objective_type or selected.target_cpa is None or float(selected.target_cpa) <= 0:
        missing.append("задать цену за цель")
    config = parse_config(selected.autopilot_config_json)
    if config.get("monthly_budget_limit") is None or float(config.get("monthly_budget_limit") or 0) <= 0:
        missing.append("задать бюджет автопилота")
    return missing


async def set_mode(db: AsyncSession, *, object_type: str, object_id: str, mode: str,
                   campaign_id: str | None = None) -> dict[str, Any]:
    mode = str(mode or ADVISORY).upper()
    if mode not in {ADVISORY, AUTONOMOUS}:
        raise ValueError("EXECUTION_MODE_INVALID")
    links = await active_links(db, object_type=object_type, object_id=object_id)
    if not links:
        raise ValueError("ACTIVE_PROMOTION_CAMPAIGN_NOT_FOUND")
    if mode == AUTONOMOUS and not campaign_id:
        raise ValueError("AUTOPILOT_CAMPAIGN_SELECTION_REQUIRED")
    selected = next((link for link in links if link.campaign_id == campaign_id), None) if campaign_id else None
    if mode == AUTONOMOUS and not selected:
        raise ValueError("AUTOPILOT_CAMPAIGN_NOT_LINKED")
    if mode == AUTONOMOUS:
        missing = await autopilot_enable_requirements(db, object_type=object_type, object_id=object_id, campaign_id=campaign_id)
        if missing:
            raise ValueError("AUTOPILOT_PREREQUISITES_MISSING:" + ",".join(missing))
    now = datetime.now(timezone.utc)
    for link in links:
        # V1 intentionally permits one authorized campaign per object.  A
        # disable may target one campaign; enabling replaces any old scope.
        if mode == AUTONOMOUS:
            link.execution_mode = AUTONOMOUS if link is selected else ADVISORY
            link.execution_updated_at = now
        elif campaign_id is None or link.campaign_id == campaign_id:
            link.execution_mode = ADVISORY
            link.execution_updated_at = now
        if not link.autopilot_config_json:
            link.autopilot_config_json = json.dumps(default_autopilot_config(), ensure_ascii=False)
    await db.flush()
    return await promotion_policy(db, object_type=object_type, object_id=object_id)


async def set_budget(db: AsyncSession, *, object_type: str, object_id: str, monthly_budget_limit: Any,
                     campaign_id: str | None = None) -> dict[str, Any]:
    limit = _positive(monthly_budget_limit, field="MONTHLY_BUDGET_LIMIT")
    links = await object_links(db, object_type=object_type, object_id=object_id)
    if not links:
        raise ValueError("ACTIVE_PROMOTION_CAMPAIGN_NOT_FOUND")
    selected = next((link for link in links if link.campaign_id == campaign_id), None) if campaign_id else None
    if campaign_id and not selected:
        raise ValueError("AUTOPILOT_CAMPAIGN_NOT_LINKED")
    now = datetime.now(timezone.utc)
    targets = [selected] if selected is not None else links
    for link in targets:
        config = parse_config(link.autopilot_config_json)
        config["monthly_budget_limit"] = limit
        config["weekly_budget_limit"] = limit
        link.autopilot_config_json = json.dumps(config, ensure_ascii=False)
        link.execution_updated_at = now
    await db.flush()
    return await promotion_policy(db, object_type=object_type, object_id=object_id)


async def validate_campaign_scope(db: AsyncSession, *, object_type: str, object_id: str,
                                 campaign_id: str, action_type: str) -> GuardrailDecision:
    """Fail closed unless the exact linked campaign has explicit write scope."""
    links = await active_links(db, object_type=object_type, object_id=object_id)
    link = next((item for item in links if item.campaign_id == campaign_id), None)
    if not link:
        return GuardrailDecision(False, "CAMPAIGN_NOT_LINKED", action_type, ADVISORY)
    mode = str(link.execution_mode or ADVISORY).upper()
    if mode != AUTONOMOUS:
        return GuardrailDecision(False, "CAMPAIGN_NOT_AUTHORIZED_FOR_AUTOPILOT", action_type, mode)
    authorized = [item for item in links if str(item.execution_mode or ADVISORY).upper() == AUTONOMOUS]
    if len(authorized) != 1 or authorized[0].campaign_id != campaign_id:
        return GuardrailDecision(False, "AUTOPILOT_CAMPAIGN_SCOPE_MISMATCH", action_type, mode)
    return GuardrailDecision(True, "AUTOPILOT_CAMPAIGN_SCOPE_CHECK_PASS", action_type, mode)


def rollback_for(action_type: str, previous_state: dict[str, Any] | None = None) -> dict[str, Any]:
    action_type = str(action_type or "").upper()
    return {
        "EXCLUDE_PLACEMENT": "RESTORE_PLACEMENT",
        "RESTORE_PLACEMENT": "EXCLUDE_PLACEMENT",
        "PAUSE_AD": "ENABLE_AD",
        "ENABLE_AD": "PAUSE_AD",
        "CREATE_AD_TEST": "PAUSE_AD",
        "CREATE_CREATIVE_TEST": "PAUSE_AD",
        "BOUNDED_BUDGET_REALLOCATION": "RESTORE_PREVIOUS_ALLOCATION",
    }.get(action_type, "NO_DESTRUCTIVE_ROLLBACK")


async def execute_autonomous_action(
    db: AsyncSession,
    *,
    object_type: str,
    object_id: str,
    campaign_id: str,
    action_type: str,
    target: dict[str, Any],
    data_trust: str,
    monthly_spend: float = 0.0,
    proposed_spend: float = 0.0,
    provider_execute: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    outcome: AdsAiOutcome | None = None,
) -> ExecutionResult:
    """Execute exactly one allowlisted action after all deterministic gates.

    The provider callback is injected by the provider adapter.  Keeping it out
    of the decision logic prevents a manual/UI path from bypassing scope and
    guardrails, and makes dry-run tests side-effect free.
    """
    action_type = str(action_type or "").upper()
    scope = await validate_campaign_scope(db, object_type=object_type, object_id=object_id,
                                          campaign_id=campaign_id, action_type=action_type)
    if not scope.allowed:
        evidence = {"status": "EXECUTION_DENIED", "reason": scope.reason,
                    "object_type": object_type, "object_id": object_id,
                    "campaign_id": campaign_id, "action_type": action_type,
                    "executed_by": "MAXIBOT"}
        if outcome:
            outcome.execution_status = "DENIED"
            outcome.implementation_confirmed = "NO"
            outcome.implementation_evidence_json = json.dumps(evidence, ensure_ascii=False)
        return ExecutionResult("DENIED", scope.reason, evidence)
    campaign = await db.get(AdsCampaign, campaign_id)
    account = await db.get(AdsAccount, campaign.account_id) if campaign else None
    if not campaign or not account:
        reason = "CAMPAIGN_OR_ACCOUNT_NOT_FOUND"
        return ExecutionResult("DENIED", reason, {"status": "EXECUTION_DENIED", "reason": reason, "campaign_id": campaign_id})
    policy = await promotion_policy(db, object_type=object_type, object_id=object_id)
    selected_link = next((item for item in await object_links(db, object_type=object_type, object_id=object_id)
                          if item.campaign_id == campaign_id), None)
    objective_target = float(selected_link.target_cpa) if selected_link and selected_link.target_cpa is not None else None
    # Values supplied by the analysis are bounded inputs; missing critical
    # values remain fail-closed in the Control Plane rather than inferred.
    actual_cpa = target.get("actual_cpa") if isinstance(target, dict) else None
    attribution_quality = str(target.get("attribution_quality") or "UNKNOWN") if isinstance(target, dict) else "UNKNOWN"
    # Missing safety attestations are ambiguous and therefore fail closed.
    evidence_sufficient = bool(target.get("evidence_sufficient", False)) if isinstance(target, dict) else False
    provider_ready = bool(target.get("provider_ready", False)) if isinstance(target, dict) else False
    verified_provider_capabilities = target.get("verified_provider_capabilities") if isinstance(target, dict) else None
    if verified_provider_capabilities is None:
        stored = stored_provider_capabilities(campaign)
        try:
            campaign_metadata = json.loads(campaign.provider_metadata_json or "{}")
        except (TypeError, ValueError):
            campaign_metadata = {}
        if stored.get("provider_manageability") in {"FULL", "PARTIAL", "NONE"} and isinstance(campaign_metadata, dict) and "provider_capability_assessment" in campaign_metadata:
            verified_provider_capabilities = stored.get("verified_capabilities", [])
    rollback_available = bool(target.get("rollback_available", False)) if isinstance(target, dict) else False
    active_experiments = await active_experiment_count_for_scope(
        db, object_type=object_type, object_id=object_id, campaign_id=campaign_id
    )
    guard = validate_action(execution_mode=AUTONOMOUS, action_type=action_type,
                           data_trust=data_trust, active_experiments=active_experiments,
                           monthly_spend=monthly_spend,
                           proposed_spend=proposed_spend, config=policy,
                           target_cpa=objective_target, actual_cpa=actual_cpa,
                           attribution_quality=attribution_quality,
                           experiment_spend=float(target.get("experiment_spend") or 0) if isinstance(target, dict) else 0,
                           experiment_budget_cap=target.get("experiment_budget_cap") if isinstance(target, dict) else None,
                           evidence_sufficient=evidence_sufficient, provider_ready=provider_ready,
                           rollback_available=rollback_available,
                           verified_provider_capabilities=verified_provider_capabilities,
                           max_duration_expired=bool(target.get("max_duration_expired", False)) if isinstance(target, dict) else False)
    if not guard.allowed:
        evidence = {"status": "EXECUTION_DENIED", "reason": guard.reason,
                    "object_type": object_type, "object_id": object_id,
                    "campaign_id": campaign_id, "provider_account_id": account.external_account_id,
                    "action_type": action_type, "executed_by": "MAXIBOT"}
        if outcome:
            outcome.execution_status = "DENIED"
            outcome.implementation_confirmed = "NO"
            outcome.implementation_evidence_json = json.dumps(evidence, ensure_ascii=False)
        return ExecutionResult("DENIED", guard.reason, evidence)
    guardrail_evidence = {
        "guardrail_decision_id": __import__("uuid").uuid4().hex,
        "timestamp": datetime.now(timezone.utc).isoformat(), "rules_version": "autopilot-v2",
        "inputs": {"target_cpa": objective_target, "monthly_budget": policy.get("monthly_budget_limit"), "actual_spend": monthly_spend, "reserved_budget": target.get("experiment_spend", 0), "experiment_cap": target.get("experiment_budget_cap"), "stop_loss": policy.get("stop_loss_multiple"), "data_trust": data_trust, "attribution_quality": attribution_quality, "campaign_scope": campaign_id, "provider_ready": provider_ready, "verified_provider_capabilities": verified_provider_capabilities, "allowlist": action_type in AUTOPILOT_ACTION_ALLOWLIST, "rollback_available": rollback_available, "active_experiments": active_experiments},
        "result": "ALLOW", "reasons": [guard.reason],
    }
    if outcome:
        outcome.implementation_evidence_json = json.dumps({"guardrail": guardrail_evidence}, ensure_ascii=False)
    if recommendation:
        recommendation.guardrail_decision_json = json.dumps(guardrail_evidence, ensure_ascii=False)
        monthly_limit = float(policy.get("monthly_budget_limit") or 0)
        if proposed_spend and monthly_limit <= 0:
            return ExecutionResult("DENIED", "BUDGET_LIMIT_REQUIRED", {"status": "EXECUTION_DENIED", "guardrail": guardrail_evidence})
        if proposed_spend and not await reserve_experiment_budget(db, recommendation=recommendation, object_type=object_type, object_id=object_id, campaign_id=campaign_id, actual_spend=monthly_spend, requested=proposed_spend, monthly_limit=monthly_limit):
            guardrail_evidence["result"] = "DENY"
            guardrail_evidence["reasons"] = ["ATOMIC_BUDGET_RESERVATION_DENIED"]
            recommendation.guardrail_decision_json = json.dumps(guardrail_evidence, ensure_ascii=False)
            return ExecutionResult("DENIED", "ATOMIC_BUDGET_RESERVATION_DENIED", {"status": "EXECUTION_DENIED", "guardrail": guardrail_evidence})
    if provider_execute is None:
        reason = "PROVIDER_EXECUTOR_NOT_CONFIGURED"
        evidence = {"status": "EXECUTION_DENIED", "reason": reason,
                    "campaign_id": campaign_id, "action_type": action_type,
                    "executed_by": "MAXIBOT"}
        if outcome:
            outcome.execution_status = "DENIED"
            outcome.implementation_confirmed = "NO"
            outcome.implementation_evidence_json = json.dumps(evidence, ensure_ascii=False)
        return ExecutionResult("DENIED", reason, evidence)
    if outcome:
        recommendation = await db.get(AdsRecommendation, outcome.recommendation_id)
        if recommendation:
            recommendation.experiment_status = "executing"
        await db.flush()
    else:
        recommendation = None
    if action_type == "CREATE_CREATIVE_TEST":
        if not recommendation:
            return ExecutionResult("DENIED", "CREATIVE_EXPERIMENT_RECOMMENDATION_REQUIRED", {"status": "EXECUTION_DENIED", "reason": "CREATIVE_EXPERIMENT_RECOMMENDATION_REQUIRED"})
        if not isinstance(target.get("ad_payload"), dict):
            control_entity = await db.get(AdsEntity, target.get("control_ad_entity_id")) if target.get("control_ad_entity_id") else None
            control_creative = await db.get(AdsCreative, target.get("control_creative_id")) if target.get("control_creative_id") else None
            if not control_entity:
                control_entity = await db.scalar(select(AdsEntity).where(
                    AdsEntity.campaign_id == campaign_id, AdsEntity.entity_type.in_(("AD", "AD_ENTITY"))
                ).order_by(AdsEntity.last_seen_at.desc()).limit(1))
            if control_entity and not control_creative:
                control_creative = await db.scalar(select(AdsCreative).where(
                    AdsCreative.ad_entity_id == control_entity.id
                ).order_by(AdsCreative.updated_at.desc()).limit(1))
            candidate = build_creative_candidate(
                campaign_id=campaign_id, experiment_id=recommendation.experiment_id or recommendation.id,
                objective=target.get("objective"), target_cpa=objective_target,
                control={"provider_ad_id": control_entity.provider_entity_id} if control_entity else None,
                hypothesis=recommendation.hypothesis or recommendation.title,
            )
            stored = await store_media_if_available(candidate["image_bytes"], mime_type=candidate["mime_type"], extension=candidate["extension"])
            if stored.get("state") != "STORED":
                return ExecutionResult("DENIED", "CREATIVE_ASSET_NOT_DURABLY_STORED", {"status": "EXECUTION_DENIED", "asset": stored})
            treatment_creative = AdsCreative(
                campaign_id=campaign_id, content_hash=candidate["content_hash"], creative_type="IMAGE",
                title=candidate["headline"], body=candidate["text"], cta=candidate["cta"],
                source_type="AUTONOMOUS_GENERATOR", storage_provider="MAIL_RU_WEBDAV",
                storage_key=stored.get("storage_key"), media_storage_state=stored.get("state"),
                mime_type=candidate["mime_type"], file_size=stored.get("file_size"),
                ai_features_json=json.dumps({"concept": candidate["creative_concept"], "experiment_id": recommendation.experiment_id}, ensure_ascii=False),
            )
            db.add(treatment_creative)
            await db.flush()
            target["treatment_creative_id"] = treatment_creative.id
            if control_entity:
                target["control_ad_entity_id"] = control_entity.id
                target["control_ad_id"] = target.get("control_ad_id") or control_entity.provider_entity_id
            if control_creative:
                target["control_creative_id"] = control_creative.id
            target["image_bytes"] = candidate["image_bytes"]
            target["ad_payload"] = {"TextImageAd": {"Title": candidate["headline"], "Text": candidate["text"], "Href": target.get("href") or "https://example.invalid"}}
            target["control_ad_payload"] = {"Creative": control_creative.content_hash if control_creative else "CONTROL", "Title": candidate["headline"], "Text": candidate["text"]}
            target["treatment_comparison_payload"] = {"Creative": candidate["content_hash"], "Title": candidate["headline"], "Text": candidate["text"]}
        control_payload = target.get("control_ad_payload")
        treatment_payload = target.get("treatment_comparison_payload") or target.get("ad_payload")
        if not isinstance(control_payload, dict) or not isinstance(treatment_payload, dict):
            return ExecutionResult("DENIED", "CREATIVE_MAPPING_REQUIRED", {"status": "EXECUTION_DENIED", "reason": "CREATIVE_MAPPING_REQUIRED"})
        try:
            assert_one_creative_variable(control_payload, treatment_payload)
        except ValueError as exc:
            return ExecutionResult("DENIED", str(exc), {"status": "EXECUTION_DENIED", "reason": str(exc)})
        control_entity = await db.get(AdsEntity, target.get("control_ad_entity_id")) if target.get("control_ad_entity_id") else None
        control_creative = await db.get(AdsCreative, target.get("control_creative_id")) if target.get("control_creative_id") else None
        treatment_creative = await db.get(AdsCreative, target.get("treatment_creative_id")) if target.get("treatment_creative_id") else None
        if not control_entity or not control_creative or not treatment_creative:
            return ExecutionResult("DENIED", "CREATIVE_MAPPING_REQUIRED", {"status": "EXECUTION_DENIED", "reason": "CREATIVE_MAPPING_REQUIRED"})
        await persist_creative_mapping(db, recommendation, control_entity=control_entity, control_creative=control_creative, treatment_creative=treatment_creative, selection_reason=target.get("selection_reason") or "Existing provider-ready creative")
        target["control_ad_id"] = target.get("control_ad_id") or control_entity.provider_entity_id
    provider_target = {**target, "provider_campaign_id": campaign.external_id}
    request = {"object_type": object_type, "object_id": object_id,
               "campaign_id": campaign_id, "provider_account_id": account.external_account_id,
               "provider_campaign_id": campaign.external_id,
               "action_type": action_type, "target": provider_target,
               "rollback": rollback_for(action_type, target),
               "executed_by": "MAXIBOT", "scope_check": "AUTOPILOT_CAMPAIGN_SCOPE_CHECK_PASS"}
    try:
        response = await provider_execute(request)
    except Exception as exc:
        evidence = {**request, "status": "EXECUTION_FAILED", "reason": type(exc).__name__}
        if outcome:
            outcome.execution_status = "FAILED"
            outcome.implementation_confirmed = "NO"
            outcome.implementation_evidence_json = json.dumps(evidence, ensure_ascii=False)
        return ExecutionResult("FAILED", type(exc).__name__, evidence)
    evidence = {**request, "status": "IMPLEMENTED", **(response or {}),
                "executed_at": datetime.now(timezone.utc).isoformat()}
    if outcome:
        recommendation = await db.get(AdsRecommendation, outcome.recommendation_id)
        if recommendation:
            lifecycle = (response or {}).get("verified_after_state", {}).get("provider_lifecycle") if isinstance(response, dict) else None
            recommendation.provider_lifecycle = lifecycle or ("CREATED" if action_type == "CREATE_CREATIVE_TEST" else recommendation.provider_lifecycle)
            if action_type in {"CREATE_AD_TEST", "CREATE_CREATIVE_TEST"} and isinstance(response, dict):
                created_id = (response.get("verified_after_state") or {}).get("created_ad_id")
                if created_id:
                    entity = AdsEntity(provider="YANDEX_DIRECT", entity_type="AD", provider_entity_id=str(created_id), campaign_id=campaign_id, status=str((response.get("verified_after_state") or {}).get("ad", {}).get("State") or "UNKNOWN"), provider_metadata_json=json.dumps({"experiment_id": recommendation.experiment_id, "role": "TREATMENT"}, ensure_ascii=False))
                    db.add(entity)
                    await db.flush()
                    recommendation.treatment_ad_entity_id = entity.id
                    if action_type == "CREATE_CREATIVE_TEST" and recommendation.treatment_creative_id:
                        treatment = await db.get(AdsCreative, recommendation.treatment_creative_id)
                        if treatment:
                            treatment.ad_entity_id = entity.id
                            treatment.external_creative_id = str((response.get("verified_after_state") or {}).get("ad", {}).get("CreativeId") or "") or None
            recommendation.measurement_started_at = measurement_start(recommendation.provider_lifecycle)
            recommendation.experiment_status = "running" if recommendation.provider_lifecycle == "ACTIVE" else "planned"
        outcome.execution_status = "IMPLEMENTED"
        outcome.implementation_confirmed = "YES"
        outcome.implementation_evidence_json = json.dumps(evidence, ensure_ascii=False)
    return ExecutionResult("IMPLEMENTED", "PROVIDER_CONFIRMED", evidence)


async def rollback_autonomous_action(
    db: AsyncSession, *, outcome: AdsAiOutcome,
    provider_rollback: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
) -> ExecutionResult:
    """Perform and evidence a non-destructive rollback of one real experiment."""
    try:
        evidence = json.loads(outcome.implementation_evidence_json or "{}")
    except (TypeError, ValueError):
        evidence = {}
    if outcome.implementation_confirmed != "YES" or not evidence:
        return ExecutionResult("DENIED", "ROLLBACK_EVIDENCE_REQUIRED", {"status": "ROLLBACK_DENIED"})
    if provider_rollback is None:
        return ExecutionResult("DENIED", "PROVIDER_ROLLBACK_NOT_CONFIGURED", {"status": "ROLLBACK_DENIED"})
    recommendation = await db.get(AdsRecommendation, outcome.recommendation_id)
    if recommendation:
        recommendation.experiment_status = "rolling_back"
    try:
        result = await provider_rollback(evidence)
    except Exception as exc:
        failed = {"status": "ROLLBACK_FAILED", "reason": type(exc).__name__, "rollback_of": evidence}
        outcome.implementation_evidence_json = json.dumps(failed, ensure_ascii=False)
        return ExecutionResult("FAILED", type(exc).__name__, failed)
    completed = {"status": "ROLLED_BACK", "rollback_of": evidence, "rollback": result,
                 "executed_by": "MAXIBOT", "executed_at": datetime.now(timezone.utc).isoformat()}
    outcome.execution_status = "ROLLED_BACK"
    outcome.implementation_confirmed = "YES"
    outcome.implementation_evidence_json = json.dumps(completed, ensure_ascii=False)
    if recommendation:
        recommendation.experiment_status = "rolled_back"
    return ExecutionResult("ROLLED_BACK", "PROVIDER_ROLLBACK_CONFIRMED", completed)


async def active_experiment_count(db: AsyncSession, *, recommendation_ids: list[str]) -> int:
    if not recommendation_ids:
        return 0
    return int(await db.scalar(select(func.count(AdsAiOutcome.id)).where(
        AdsAiOutcome.recommendation_id.in_(recommendation_ids),
        AdsAiOutcome.status.in_(("PENDING", "RETRYABLE_FAILED")),
        AdsAiOutcome.implementation_confirmed == "YES",
    )) or 0)


async def active_experiment_count_for_scope(db: AsyncSession, *, object_type: str,
                                            object_id: str, campaign_id: str) -> int:
    """Count running implemented experiments for one exact object/campaign."""
    scope = ((AdsAiRun.channel_id == object_id,) if object_type == "CHANNEL" else (
        AdsAiRun.promotion_object_type == "BOT", AdsAiRun.promotion_object_id == object_id,
    ))
    return int(await db.scalar(select(func.count(AdsAiOutcome.id))
        .join(AdsRecommendation, AdsRecommendation.id == AdsAiOutcome.recommendation_id)
        .join(AdsAiRun, AdsAiRun.id == AdsRecommendation.ai_run_id)
        .where(*scope, AdsAiRun.campaign_id == campaign_id,
               AdsRecommendation.experiment_status.in_(("executing", "running", "evaluating", "rolling_back")))) or 0)


async def evaluate_recommendation_guardrails(db: AsyncSession, *, recommendation: AdsRecommendation,
                                             data_trust: str, monthly_spend: float = 0.0,
                                             proposed_spend: float = 0.0) -> GuardrailDecision:
    object_type = recommendation.promotion_object_type or "CHANNEL"
    object_id = recommendation.promotion_object_id or recommendation.channel_id or ""
    policy = await promotion_policy(db, object_type=object_type, object_id=object_id)
    payload = {}
    try:
        payload = json.loads(recommendation.policy_json or "{}")
    except (TypeError, ValueError):
        pass
    action_type = payload.get("action_type") or recommendation.action_code or "OBSERVE"
    campaign_id = await db.scalar(select(AdsAiRun.campaign_id).join(
        AdsRecommendation, AdsRecommendation.ai_run_id == AdsAiRun.id
    ).where(AdsRecommendation.id == recommendation.id))
    scope = await validate_campaign_scope(db, object_type=object_type, object_id=object_id,
                                          campaign_id=campaign_id or "",
                                          action_type=action_type)
    if not scope.allowed:
        return scope
    count = await active_experiment_count_for_scope(
        db, object_type=object_type, object_id=object_id, campaign_id=campaign_id or ""
    )
    return validate_action(
        execution_mode=policy["execution_mode"], action_type=action_type,
        data_trust=data_trust, active_experiments=count,
        monthly_spend=monthly_spend, proposed_spend=proposed_spend,
        config=policy,
    )
