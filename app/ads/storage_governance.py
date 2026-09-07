"""Bounded, fail-closed lifecycle policy for the canonical ADS tables."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import AdsAnalysisSnapshot, AdsCollectionRun, AdsDailyAggregate, AdsEntity, AdsObservation, AdsReportRun


def is_interval_observation(row: AdsObservation) -> bool:
    provenance = json.loads(row.provenance_json or "{}")
    return str(provenance.get("aggregation") or "WINDOW_AGGREGATE") in {"INTERVAL_DELTA", "DAILY_SOURCE"}


async def _has_daily_coverage(db: AsyncSession, *, campaign_id: str, entity_id: str | None, dimension_type: str, dimension_value: str | None, date_from: date, date_to: date) -> bool:
    expected = max(1, (date_to - date_from).days + 1)
    actual = await db.scalar(select(func.count(AdsDailyAggregate.id)).where(
        AdsDailyAggregate.campaign_id == campaign_id,
        AdsDailyAggregate.entity_id == entity_id,
        AdsDailyAggregate.dimension_type == dimension_type,
        AdsDailyAggregate.dimension_value == dimension_value,
        AdsDailyAggregate.aggregate_date >= date_from,
        AdsDailyAggregate.aggregate_date <= date_to,
    ))
    return int(actual or 0) == expected


async def downsample_candidates(db: AsyncSession, *, now: datetime | None = None) -> dict[str, int | bool | str]:
    """Read-only preview using the same safety rules as the real purge."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.ads_observations_retention_days)
    rows = list((await db.scalars(select(AdsObservation).where(AdsObservation.created_at < cutoff).order_by(AdsObservation.created_at).limit(settings.ads_retention_batch_size))).all())
    eligible = protected = 0
    for row in rows:
        snapshot = await db.get(AdsAnalysisSnapshot, row.snapshot_id) if row.snapshot_id else None
        entity = await db.get(AdsEntity, row.entity_id) if row.entity_id else None
        campaign_id = snapshot.campaign_id if snapshot and snapshot.campaign_id else entity.campaign_id if entity else None
        if campaign_id and row.date_to and await _has_daily_coverage(db, campaign_id=campaign_id, entity_id=row.entity_id, dimension_type=row.dimension_type, dimension_value=row.dimension_value, date_from=row.date_from or row.date_to, date_to=row.date_to):
            eligible += 1
        else:
            protected += 1
    return {"dry_run": True, "cutoff": cutoff.isoformat(), "rows_scanned": len(rows), "eligible_interval_rows": eligible, "protected_window_rows": protected, "deleted": 0, "daily_rows_written": 0}


async def purge_expired_observations(db: AsyncSession, *, now: datetime | None = None, limit: int | None = None) -> dict[str, int | bool | str]:
    """Delete only detailed rows with complete daily-aggregate coverage."""
    limit = limit or settings.ads_retention_batch_size
    if limit < 1:
        raise ValueError("RETENTION_LIMIT_MUST_BE_POSITIVE")
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.ads_observations_retention_days)
    candidates = list((await db.scalars(select(AdsObservation).where(AdsObservation.created_at < cutoff).order_by(AdsObservation.created_at).limit(limit))).all())
    deleted = skipped = 0
    for row in candidates:
        snapshot = await db.get(AdsAnalysisSnapshot, row.snapshot_id) if row.snapshot_id else None
        entity = await db.get(AdsEntity, row.entity_id) if row.entity_id else None
        campaign_id = snapshot.campaign_id if snapshot and snapshot.campaign_id else entity.campaign_id if entity else None
        if not campaign_id or not row.date_to or not await _has_daily_coverage(db, campaign_id=campaign_id, entity_id=row.entity_id, dimension_type=row.dimension_type, dimension_value=row.dimension_value, date_from=row.date_from or row.date_to, date_to=row.date_to):
            skipped += 1
            continue
        await db.execute(delete(AdsObservation).where(AdsObservation.id == row.id))
        deleted += 1
    await db.flush()
    return {"cutoff": cutoff.isoformat(), "rows_scanned": len(candidates), "deleted": deleted, "skipped": skipped}


async def purge_operational_history(db: AsyncSession, *, now: datetime | None = None, limit: int | None = None) -> dict[str, int | str]:
    """Bounded cleanup for provenance only; daily and AI history are untouched."""
    limit = limit or settings.ads_retention_batch_size
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.ads_operational_retention_days)
    collection_ids = list((await db.scalars(select(AdsCollectionRun.id).where(func.coalesce(AdsCollectionRun.completed_at, AdsCollectionRun.scheduled_at) < cutoff, AdsCollectionRun.run_type != "RETENTION").order_by(AdsCollectionRun.scheduled_at).limit(limit))).all())
    report_ids = list((await db.scalars(select(AdsReportRun.id).where(AdsReportRun.fetched_at < cutoff).order_by(AdsReportRun.fetched_at).limit(limit))).all())
    deleted_collection = int((await db.execute(delete(AdsCollectionRun).where(AdsCollectionRun.id.in_(collection_ids)))).rowcount or 0) if collection_ids else 0
    deleted_report = int((await db.execute(delete(AdsReportRun).where(AdsReportRun.id.in_(report_ids)))).rowcount or 0) if report_ids else 0
    await db.flush()
    return {"cutoff": cutoff.isoformat(), "deleted_collection_runs": deleted_collection, "deleted_report_runs": deleted_report}


async def run_retention(db: AsyncSession, *, now: datetime | None = None, limit: int | None = None) -> dict[str, int | bool | str]:
    """One daily idempotent slot; repeated calls safely continue batches."""
    if not settings.ads_retention_enabled:
        return {"enabled": False, "deleted": 0, "skipped": 0, "deleted_collection_runs": 0, "deleted_report_runs": 0}
    now = now or datetime.now(timezone.utc)
    limit = limit or settings.ads_retention_batch_size
    slot_key = f"RETENTION:{now.date().isoformat()}:{settings.ads_observations_retention_days}:{settings.ads_operational_retention_days}"
    existing = await db.scalar(select(AdsCollectionRun).where(AdsCollectionRun.slot_key == slot_key))
    if existing and existing.completed_at and existing.completed_at >= now - timedelta(minutes=settings.ads_retention_interval_minutes):
        return {"enabled": True, "collection_run_id": existing.id, **(json.loads(existing.summary_json or "{}"))}
    result = await purge_expired_observations(db, now=now, limit=limit)
    result.update(await purge_operational_history(db, now=now, limit=limit))
    run = existing
    summary = {**result, "health_mode": "CURRENT_STATE", "daily_aggregates": "LONG_TERM", "ai_history": "LONG_TERM", "data_health_retention_days": settings.ads_data_health_retention_days}
    if not run:
        run = AdsCollectionRun(provider="INTERNAL", collection_type="RETENTION", run_type="RETENTION", trigger="SCHEDULED", scheduled_at=now, slot_key=slot_key, status="COMPLETED", started_at=now, completed_at=datetime.now(timezone.utc), summary_json=json.dumps(summary, ensure_ascii=False))
        db.add(run)
    else:
        run.completed_at = datetime.now(timezone.utc)
        run.summary_json = json.dumps(summary, ensure_ascii=False)
    await db.commit()
    return {**summary, "collection_run_id": run.id}
