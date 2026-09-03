"""Read-only outcome scheduling primitives; this module never calls Codex."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdsV2Outcome


async def claim_due_outcomes(db: AsyncSession, *, now: datetime | None = None, limit: int = 20) -> list[AdsV2Outcome]:
    now = now or datetime.now(timezone.utc)
    async with db.begin():
        rows = list((await db.scalars(
            select(AdsV2Outcome)
            .where(or_(AdsV2Outcome.status == "PENDING", AdsV2Outcome.status == "RETRYABLE_FAILED"), AdsV2Outcome.due_at <= now, or_(AdsV2Outcome.next_retry_at.is_(None), AdsV2Outcome.next_retry_at <= now))
            .order_by(AdsV2Outcome.due_at)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )).all())
        for row in rows:
            row.status = "PROCESSING"
            row.attempts += 1
    return rows


async def mark_outcome_retryable(db: AsyncSession, outcome: AdsV2Outcome, error_code: str) -> None:
    outcome.status = "RETRYABLE_FAILED" if outcome.attempts < 3 else "FAILED"
    outcome.error_code = error_code
    outcome.next_retry_at = datetime.now(timezone.utc) + timedelta(hours=min(24, 6 * max(1, outcome.attempts))) if outcome.status == "RETRYABLE_FAILED" else None
    await db.commit()


async def mark_outcome_completed(db: AsyncSession, outcome: AdsV2Outcome, *, followup_snapshot_id: str, delta: dict) -> None:
    import json
    outcome.status = "COMPLETED"
    outcome.followup_snapshot_id = followup_snapshot_id
    outcome.observed_delta_json = json.dumps(delta, ensure_ascii=False)
    outcome.completed_at = datetime.now(timezone.utc)
    await db.commit()
