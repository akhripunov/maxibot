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
    outcome.evaluation_status = classify_outcome(
        implementation_confirmed=outcome.implementation_confirmed,
        valid_window=True,
        sufficient_data=outcome_metrics_sufficient(delta),
        success_criteria_met=None,
    )
    outcome.followup_snapshot_id = followup_snapshot_id
    outcome.observed_delta_json = json.dumps(delta, ensure_ascii=False)
    outcome.actual_metrics_json = json.dumps(delta, ensure_ascii=False)
    outcome.confidence = "LOW" if outcome.evaluation_status == "INCONCLUSIVE" else outcome.confidence
    outcome.reason_json = json.dumps({"reason": "Наблюдение после рекомендации не доказывает причинность; implementation evidence отсутствует."}, ensure_ascii=False)
    outcome.completed_at = datetime.now(timezone.utc)
    await db.commit()
