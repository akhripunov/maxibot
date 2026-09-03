"""Dry-run storage governance for the ADS V2 timeline.

Only interval/daily source observations may be downsampled.  Rolling-window
observations are intentionally excluded so overlapping 1d/3d/7d values can
never be double-counted.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import AdsV2Observation


def is_interval_observation(row: AdsV2Observation) -> bool:
    provenance = json.loads(row.provenance_json or "{}")
    return str(provenance.get("aggregation") or "WINDOW_AGGREGATE") in {"INTERVAL_DELTA", "DAILY_SOURCE"}


async def downsample_candidates(db: AsyncSession, *, now: datetime | None = None) -> dict[str, int | bool]:
    """Report candidates without writing or deleting anything."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.ads_hourly_retention_days)
    rows = list((await db.scalars(select(AdsV2Observation).where(AdsV2Observation.created_at < cutoff))).all())
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
