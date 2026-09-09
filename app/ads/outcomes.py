"""Read-only outcome scheduling primitives; this module never calls Codex."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdsAiRun, AdsAiOutcome, AdsRecommendation

OUTCOME_HORIZONS = (("24h", timedelta(hours=24)), ("72h", timedelta(hours=72)), ("7d", timedelta(days=7)))
OUTCOME_METRICS = ("impressions_7d", "clicks_7d", "cost_7d", "conversions_7d")


def classify_outcome(*, implementation_confirmed: str, valid_window: bool, sufficient_data: bool, success_criteria_met: bool | None) -> str:
    """Causal close-loop gate; observation alone can never be SUCCESS/FAIL."""
    if implementation_confirmed != "YES" or not valid_window or not sufficient_data or success_criteria_met is None:
        return "INCONCLUSIVE"
    return "SUCCESS" if success_criteria_met else "FAIL"


def outcome_metrics_sufficient(facts: dict | None) -> bool:
    """Return true only when all campaign totals needed for a delta are known."""
    values = (facts or {}).get("facts", facts or {})
    return all(values.get(key) is not None for key in OUTCOME_METRICS)


def _json_object(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = __import__("json").loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def evaluate_success_criteria(*, expected_impact: dict | None, delta: dict | None) -> bool | None:
    """Evaluate an explicit recommendation hypothesis against observed delta.

    The evaluator is deliberately conservative: without an explicit metric,
    direction, and threshold it cannot turn an observation into SUCCESS/FAIL.
    ``delta`` stores each metric as ``{"before": ..., "after": ...}``.
    """
    spec = expected_impact if isinstance(expected_impact, dict) else {}
    metric = spec.get("metric") or spec.get("metric_name")
    direction = str(spec.get("direction") or spec.get("expected_direction") or "").lower()
    threshold = spec.get("threshold", spec.get("min_relative_change"))
    if not metric or direction not in {"increase", "decrease", "maintain"}:
        return None
    try:
        threshold = abs(float(threshold))
        values = (delta or {}).get(metric) or {}
        before, after = values.get("before"), values.get("after")
        if before is None or after is None:
            return None
        before, after = float(before), float(after)
        if before == 0:
            return None
        relative_change = (after - before) / abs(before)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if direction == "increase":
        return relative_change >= threshold
    if direction == "decrease":
        return relative_change <= -threshold
    return abs(relative_change) <= threshold


async def record_implementation_evidence(
    db: AsyncSession,
    outcome: AdsAiOutcome,
    evidence: list[dict] | dict,
    *,
    confirmed: str = "YES",
    execution_status: str = "IMPLEMENTED",
) -> None:
    """Record the canonical, auditable implementation checkpoint.

    This is an evidence write only. It never executes or mutates an ad
    campaign, and it intentionally requires non-empty structured evidence.
    """
    rows = evidence if isinstance(evidence, list) else [evidence]
    if not rows or any(not isinstance(row, dict) or not row for row in rows):
        raise ValueError("IMPLEMENTATION_EVIDENCE_REQUIRED")
    if confirmed not in {"YES", "NO", "UNKNOWN"}:
        raise ValueError("IMPLEMENTATION_CONFIRMATION_INVALID")
    import json
    outcome.implementation_confirmed = confirmed
    outcome.implementation_evidence_json = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    outcome.execution_status = execution_status
    outcome.evaluation_status = "READY" if confirmed == "YES" else "INCONCLUSIVE"
    outcome.error_code = None
    await db.commit()


async def ensure_outcome_checkpoints(
    db: AsyncSession,
    recommendation: AdsRecommendation,
    *,
    baseline_snapshot_id: str | None = None,
) -> int:
    """Idempotently create the three advisory checkpoints from recommendation time."""
    existing = {
        row.horizon
        for row in (await db.scalars(select(AdsAiOutcome).where(AdsAiOutcome.recommendation_id == recommendation.id))).all()
    }
    if baseline_snapshot_id is None and recommendation.ai_run_id:
        ai = await db.get(AdsAiRun, recommendation.ai_run_id)
        baseline_snapshot_id = ai.input_snapshot_id if ai else None
    created = 0
    created_at = recommendation.created_at
    if created_at is None:
        raise ValueError("RECOMMENDATION_CREATED_AT_REQUIRED")
    for horizon, delta in OUTCOME_HORIZONS:
        if horizon in existing:
            continue
        db.add(AdsAiOutcome(
            recommendation_id=recommendation.id,
            horizon=horizon,
            due_at=created_at + delta,
            execution_status="RECOMMENDATION_ONLY",
            baseline_snapshot_id=baseline_snapshot_id,
            evaluation_status="PENDING",
            implementation_confirmed="UNKNOWN",
            expected_impact_json=recommendation.expected_impact_json,
        ))
        created += 1
    if created:
        await db.flush()
    return created


async def backfill_missing_outcome_checkpoints(db: AsyncSession, *, limit: int = 100) -> int:
    """Repair only missing checkpoint rows; never invent measured outcomes."""
    recommendations = list((await db.scalars(
        select(AdsRecommendation).order_by(AdsRecommendation.created_at).limit(limit)
    )).all())
    created = 0
    for recommendation in recommendations:
        created += await ensure_outcome_checkpoints(db, recommendation)
    if created:
        await db.commit()
    return created


async def claim_due_outcomes(db: AsyncSession, *, now: datetime | None = None, limit: int = 20) -> list[AdsAiOutcome]:
    now = now or datetime.now(timezone.utc)
    async with db.begin():
        rows = list((await db.scalars(
            select(AdsAiOutcome)
            .where(or_(AdsAiOutcome.status == "PENDING", AdsAiOutcome.status == "RETRYABLE_FAILED"), AdsAiOutcome.due_at <= now, or_(AdsAiOutcome.next_retry_at.is_(None), AdsAiOutcome.next_retry_at <= now))
            .order_by(AdsAiOutcome.due_at)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )).all())
        for row in rows:
            row.status = "PROCESSING"
            row.attempts += 1
    return rows


async def mark_outcome_retryable(db: AsyncSession, outcome: AdsAiOutcome, error_code: str) -> None:
    outcome.status = "RETRYABLE_FAILED" if outcome.attempts < 3 else "FAILED"
    outcome.error_code = error_code
    outcome.next_retry_at = datetime.now(timezone.utc) + timedelta(hours=min(24, 6 * max(1, outcome.attempts))) if outcome.status == "RETRYABLE_FAILED" else None
    await db.commit()


async def mark_outcome_completed(db: AsyncSession, outcome: AdsAiOutcome, *, followup_snapshot_id: str, delta: dict) -> None:
    import json
    outcome.status = "COMPLETED"
    recommendation = await db.get(AdsRecommendation, outcome.recommendation_id)
    expected_impact = _json_object(outcome.expected_impact_json) or _json_object(recommendation.expected_impact_json if recommendation else None)
    criteria = evaluate_success_criteria(expected_impact=expected_impact, delta=delta)
    outcome.expected_impact_json = json.dumps(expected_impact, ensure_ascii=False) if expected_impact else outcome.expected_impact_json
    outcome.expected_direction = str(expected_impact.get("direction") or expected_impact.get("expected_direction")) if expected_impact else outcome.expected_direction
    outcome.evaluation_status = classify_outcome(
        implementation_confirmed=outcome.implementation_confirmed,
        valid_window=True,
        sufficient_data=outcome_metrics_sufficient(delta),
        success_criteria_met=criteria,
    )
    outcome.followup_snapshot_id = followup_snapshot_id
    outcome.observed_delta_json = json.dumps(delta, ensure_ascii=False)
    outcome.actual_metrics_json = json.dumps(delta, ensure_ascii=False)
    outcome.confidence = "LOW" if outcome.evaluation_status == "INCONCLUSIVE" else outcome.confidence
    outcome.reason_json = json.dumps({
        "reason": "Критерий проверен по явно заданной гипотезе рекомендации." if criteria is not None else "Недостаточно implementation evidence или явного критерия успеха для детерминированной оценки.",
        "success_criteria_met": criteria,
    }, ensure_ascii=False)
    outcome.completed_at = datetime.now(timezone.utc)
    if outcome.evaluation_status in {"SUCCESS", "FAIL"} and recommendation and recommendation.ai_run_id:
        ai = await db.get(AdsAiRun, recommendation.ai_run_id)
        try:
            validated = json.loads(ai.validated_output_json or "{}") if ai else {}
            context = json.loads(ai.safe_context_json or "{}") if ai else {}
            candidates = validated.get("memory_candidates") or []
            from app.ads.llm import evidence_registry
            known_evidence = evidence_registry(context)
            from app.ads.memory import create_candidates
            await create_candidates(
                db,
                channel_id=recommendation.channel_id,
                campaign_id=ai.campaign_id if ai else None,
                ai_run_id=ai.id if ai else recommendation.ai_run_id,
                snapshot_id=followup_snapshot_id,
                candidates=candidates,
                known_evidence=known_evidence,
                proven_outcome=True,
                object_type=recommendation.promotion_object_type or "CHANNEL",
                object_id=recommendation.promotion_object_id or recommendation.channel_id,
            )
        except (TypeError, ValueError, KeyError):
            # Outcome status is still durable; malformed historical AI payloads
            # must not make collection retry forever.
            pass
    await db.commit()
