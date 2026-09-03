"""Read-only storage diagnostics used by the admin ADS screen."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ads.periodic import collection_health
from app.core.config import settings

TABLES = (
    "ads_v2_entities", "ads_v2_report_runs", "ads_v2_observations",
    "ads_v2_outcomes", "ads_v2_collection_runs", "ads_v2_daily_aggregates",
)
MOSCOW = ZoneInfo("Europe/Moscow")


def ui_time(value: str | None) -> str:
    if not value:
        return "н/д"
    try:
        return datetime.fromisoformat(value).astimezone(MOSCOW).strftime("%d.%m %H:%M")
    except ValueError:
        return "н/д"


def human_size(value: int | None) -> str:
    if value is None:
        return "н/д"
    value = max(0, int(value))
    units = ("Б", "КБ", "МБ", "ГБ", "ТБ")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "Б" else f"{int(amount)} Б"
        amount /= 1024
    return "н/д"


def storage_status(*, free_percent: float | None, stale: int = 0, retry_backlog: int = 0) -> str:
    if free_percent is None:
        return "UNKNOWN"
    if free_percent < settings.ads_storage_critical_free_percent or stale or retry_backlog > 10:
        return "CRITICAL"
    if free_percent < settings.ads_storage_warning_free_percent or retry_backlog:
        return "WARNING"
    return "NORMAL"


async def diagnostics(db: AsyncSession) -> dict:
    """Collect bounded, sanitized diagnostics; callers should fail open."""
    db_size = int(await db.scalar(text("select pg_database_size(current_database())")) or 0)
    sizes: dict[str, dict[str, int]] = {}
    for table in TABLES:
        relation = int(await db.scalar(text("select pg_relation_size(:table)"), {"table": table}) or 0)
        total = int(await db.scalar(text("select pg_total_relation_size(:table)"), {"table": table}) or 0)
        sizes[table] = {"data": relation, "indexes": max(0, total - relation), "total": total}
    disk = shutil.disk_usage("/")
    free_percent = (disk.free / disk.total * 100.0) if disk.total else None
    periodic = await collection_health(db)
    return {
        "database": {"total": db_size}, "tables": sizes,
        "ads_total": sum(item["total"] for item in sizes.values()),
        "disk": {"total": disk.total, "used": disk.used, "free": disk.free, "free_percent": free_percent},
        "periodic": periodic,
        "growth_7d": None, "growth_30d": None,
        "projection": None,
        "retention": {"hourly_days": settings.ads_hourly_retention_days, "daily_days": settings.ads_daily_retention_days, "downsampling": "PREPARED; deletion disabled"},
        "downsampling": {"eligible": None, "protected": None, "dry_run": True},
        "status": storage_status(free_percent=free_percent, stale=int(periodic.get("stale_campaigns", 0)), retry_backlog=int(periodic.get("retry_backlog", 0))),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
