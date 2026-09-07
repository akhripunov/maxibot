"""Dry-run storage governance for the ADS V2 timeline.

Only interval/daily source observations may be downsampled.  Rolling-window
observations are intentionally excluded so overlapping 1d/3d/7d values can
never be double-counted.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import AdsAnalysisSnapshot, AdsCollectionRun, AdsDailyAggregate, AdsObservation


def is_interval_observation(row: AdsObservation) -> bool:
    provenance = json.loads(row.provenance_json or "{}")
    return str(provenance.get("aggregation") or "WINDOW_AGGREGATE") in {"INTERVAL_DELTA", "DAILY_SOURCE"}


async def downsample_candidates(db: AsyncSession, *, now: datetime | None = None) -> dict[str, int | bool]:
    """Report candidates without writing or deleting anything."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.ads_hourly_retention_days)
    rows = list((await db.scalars(select(AdsObservation).where(AdsObservation.created_at < cutoff))).all())
    eligible = 0
    protected = 0
    for row in rows:
        if is_interval_observation(row):
            eligible += 1
        else:
            # Current V2 observations are rolling/window aggregates and must
            # not be summed into daily totals.
            protected += 1
    return {"dry_run": True, "cutoff": cutoff.isoformat(), "rows_scanned": len(rows), "eligible_interval_rows": eligible, "protected_window_rows": protected, "deleted": 0, "daily_rows_written": 0}


async def purge_expired_observations(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 500,
) -> dict[str, int | bool | str]:
    """Delete only bounded interval detail whose daily aggregate is durable.

    The operation is deliberately idempotent: a second call sees fewer eligible
    rows and never rewrites aggregates. Rolling/window observations are kept.
    """
    if limit < 1:
        raise ValueError("RETENTION_LIMIT_MUST_BE_POSITIVE")
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.ads_hourly_retention_days)
    candidates = list((await db.execute(
        select(AdsObservation, AdsAnalysisSnapshot)
        .join(AdsAnalysisSnapshot, AdsAnalysisSnapshot.id == AdsObservation.snapshot_id)
        .where(AdsObservation.created_at < cutoff)
        .order_by(AdsObservation.created_at)
        .limit(limit)
    )).all())
    deleted = 0
    protected = 0
    for row, snapshot in candidates:
        if not is_interval_observation(row) or not snapshot.campaign_id or not row.date_to:
            protected += 1
            continue
        aggregate = await db.scalar(select(AdsDailyAggregate.id).where(
            AdsDailyAggregate.campaign_id == snapshot.campaign_id,
            AdsDailyAggregate.entity_id == row.entity_id,
            AdsDailyAggregate.dimension_type == row.dimension_type,
            AdsDailyAggregate.dimension_value == row.dimension_value,
            AdsDailyAggregate.aggregate_date == row.date_to,
        ).limit(1))
        if not aggregate:
            protected += 1
            continue
        await db.execute(delete(AdsObservation).where(AdsObservation.id == row.id))
        deleted += 1
    await db.flush()
    return {
        "dry_run": False,
        "cutoff": cutoff.isoformat(),
        "rows_scanned": len(candidates),
        "deleted": deleted,
        "protected": protected,
    }


async def run_retention(db: AsyncSession, *, now: datetime | None = None, limit: int = 500) -> dict[str, int | bool | str]:
    """Run one bounded retention slot and expose it through collection history."""
    now = now or datetime.now(timezone.utc)
    result = await purge_expired_observations(db, now=now, limit=limit)
    slot_key = f"RETENTION:{now.date().isoformat()}:{settings.ads_hourly_retention_days}"
    run = await db.scalar(select(AdsCollectionRun).where(AdsCollectionRun.slot_key == slot_key))
    if not run:
        run = AdsCollectionRun(
            provider="INTERNAL", collection_type="RETENTION", run_type="RETENTION",
            trigger="SCHEDULED", scheduled_at=now, slot_key=slot_key,
            status="COMPLETED", started_at=now, completed_at=datetime.now(timezone.utc),
            summary_json=json.dumps(result, ensure_ascii=False),
        )
        db.add(run)
    await db.commit()
    return {**result, "collection_run_id": run.id}
