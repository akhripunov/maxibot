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
from app.db.models import AdsAccount, AdsAiOutcome, AdsAiRun, AdsCampaign, AdsPromotionCampaignLink, AdsRecommendation

ADVISORY = "ADVISORY"
AUTONOMOUS = "AUTONOMOUS"
DEFAULT_MAX_TEST_SPEND = 1000.0
DEFAULT_MAX_BUDGET_CHANGE_PERCENT = 10.0
DEFAULT_MAX_PARALLEL_EXPERIMENTS = 1
AUTOPILOT_ACTION_ALLOWLIST = frozenset({
    "EXCLUDE_PLACEMENT", "RESTORE_PLACEMENT", "PAUSE_AD", "ENABLE_AD",
    "CREATE_CREATIVE_TEST", "CREATE_AD_TEST", "BOUNDED_BUDGET_REALLOCATION",
})
FUTURE_ACTIONS = frozenset({"CREATE_CAMPAIGN", "CREATE_AD_GROUP", "CHANGE_TARGETING", "CHANGE_STRATEGY", "CHANGE_BID", "SCALE_CAMPAIGN"})


def default_autopilot_config() -> dict[str, Any]:
    return {
        "monthly_budget_limit": None,
        "weekly_budget_limit": None,
        "max_test_spend": DEFAULT_MAX_TEST_SPEND,
        "max_budget_change_percent": DEFAULT_MAX_BUDGET_CHANGE_PERCENT,
        "max_parallel_experiments": DEFAULT_MAX_PARALLEL_EXPERIMENTS,
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
                    proposed_spend: float = 0.0, config: dict[str, Any] | None = None) -> GuardrailDecision:
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
    if trust != "GOOD":
        return GuardrailDecision(False, f"DATA_TRUST_{trust}", action_type, mode)
    max_parallel = int(config.get("max_parallel_experiments") or DEFAULT_MAX_PARALLEL_EXPERIMENTS)
    if active_experiments >= max_parallel:
        return GuardrailDecision(False, "MAX_PARALLEL_EXPERIMENTS", action_type, mode)
    limit = monthly_budget_limit if monthly_budget_limit is not None else (config.get("weekly_budget_limit") if config.get("weekly_budget_limit") is not None else config.get("monthly_budget_limit"))
    if limit is not None and monthly_spend + max(0.0, proposed_spend) > float(limit):
        return GuardrailDecision(False, "WEEKLY_BUDGET_LIMIT", action_type, mode)
    if proposed_spend > float(config.get("max_test_spend") or DEFAULT_MAX_TEST_SPEND):
        return GuardrailDecision(False, "MAX_TEST_SPEND", action_type, mode)
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


async def promotion_policy(db: AsyncSession, *, object_type: str, object_id: str) -> dict[str, Any]:
    links = await active_links(db, object_type=object_type, object_id=object_id)
    if not links:
        return {"execution_mode": ADVISORY, **default_autopilot_config(), "active_links": 0}
    authorized = [link.campaign_id for link in links if str(link.execution_mode or ADVISORY).upper() == AUTONOMOUS]
    mode = AUTONOMOUS if authorized else ADVISORY
    source = next((link for link in links if link.campaign_id in authorized), links[0])
    config = parse_config(source.autopilot_config_json)
    return {"execution_mode": mode, **config, "active_links": len(links),
            "authorized_campaign_ids": authorized,
            "write_scope": "CAMPAIGN_SCOPED"}


async def autopilot_enable_requirements(db: AsyncSession, *, object_type: str, object_id: str,
                                        campaign_id: str | None) -> list[str]:
    """Return missing owner prerequisites before enabling campaign autopilot."""
    links = await active_links(db, object_type=object_type, object_id=object_id)
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
    links = await active_links(db, object_type=object_type, object_id=object_id)
    if not links:
        raise ValueError("ACTIVE_PROMOTION_CAMPAIGN_NOT_FOUND")
    selected = next((link for link in links if link.campaign_id == campaign_id), None) if campaign_id else None
    if campaign_id and not selected:
        raise ValueError("AUTOPILOT_CAMPAIGN_NOT_LINKED")
    # Budget is campaign-scoped in V1, just like write authorization.  A
    # missing campaign is accepted only for legacy advisory objects, where it
    # is applied to the sole active link to avoid ambiguous writes.
    if selected is None:
        if len(links) != 1:
            raise ValueError("AUTOPILOT_CAMPAIGN_SELECTION_REQUIRED")
        selected = links[0]
    now = datetime.now(timezone.utc)
    config = parse_config(selected.autopilot_config_json)
    config["monthly_budget_limit"] = limit
    config["weekly_budget_limit"] = limit
    selected.autopilot_config_json = json.dumps(config, ensure_ascii=False)
    selected.execution_updated_at = now
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
    active_experiments = await active_experiment_count_for_scope(
        db, object_type=object_type, object_id=object_id, campaign_id=campaign_id
    )
    guard = validate_action(execution_mode=AUTONOMOUS, action_type=action_type,
                           data_trust=data_trust, active_experiments=active_experiments,
                           monthly_spend=monthly_spend,
                           proposed_spend=proposed_spend, config=policy)
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
            recommendation.experiment_status = "running"
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
