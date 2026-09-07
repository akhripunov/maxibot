"""Read-only storage diagnostics used by the admin ADS screen."""
from __future__ import annotations

import shutil
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ads.periodic import collection_health
from app.core.config import settings
from app.db.models import AdsCollectionRun, AdsDailyAggregate, AdsObservation, AdsReportRun

TABLES = (
    "ads_accounts", "ads_campaigns", "ads_promotion_campaign_links",
    "ads_entities", "ads_creatives", "ads_observations",
    "ads_daily_aggregates", "ads_collection_runs", "ads_report_runs",
    "ads_analysis_snapshots", "ads_ai_runs", "ads_recommendations",
    "ads_ai_outcomes", "ads_memories", "ads_memory_evidence", "ads_data_health",
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
    oldest_observation = await db.scalar(select(func.min(AdsObservation.created_at)))
    oldest_daily = await db.scalar(select(func.min(AdsDailyAggregate.aggregate_date)))
    oldest_collection = await db.scalar(select(func.min(AdsCollectionRun.scheduled_at)).where(AdsCollectionRun.run_type != "RETENTION"))
    oldest_report = await db.scalar(select(func.min(AdsReportRun.fetched_at)))
    retention_run = await db.scalar(select(AdsCollectionRun).where(AdsCollectionRun.run_type == "RETENTION").order_by(AdsCollectionRun.completed_at.desc()).limit(1))
    retention_summary = json.loads(retention_run.summary_json or "{}") if retention_run and retention_run.summary_json else {}
    ai_history_tables = {name: sizes[name] for name in ("ads_analysis_snapshots", "ads_ai_runs", "ads_recommendations", "ads_ai_outcomes", "ads_memories", "ads_memory_evidence")}
    ai_history_size = sum(item["total"] for item in ai_history_tables.values())
    return {
        "database": {"total": db_size}, "tables": sizes,
        "ads_total": sum(item["total"] for item in sizes.values()),
        "disk": {"total": disk.total, "used": disk.used, "free": disk.free, "free_percent": free_percent},
        "periodic": periodic,
        "growth_7d": None, "growth_30d": None,
        "projection": None,
        "retention": {"observations_days": settings.ads_observations_retention_days, "operational_days": settings.ads_operational_retention_days, "data_health_days": settings.ads_data_health_retention_days, "hourly_days": settings.ads_observations_retention_days, "daily_days": 0, "daily_aggregates": "LONG_TERM", "ai_history": "LONG_TERM", "metadata": "LONG_TERM", "last_run": retention_run.completed_at.isoformat() if retention_run and retention_run.completed_at else None, "deleted_rows": int(retention_summary.get("deleted", 0)) + int(retention_summary.get("deleted_collection_runs", 0)) + int(retention_summary.get("deleted_report_runs", 0)), "skipped_rows": int(retention_summary.get("skipped", 0)), "skip_reason": "daily aggregate coverage missing" if retention_summary.get("skipped") else None},
        "downsampling": {"eligible": None, "protected": None, "dry_run": True},
        "oldest": {"ads_observation": oldest_observation.isoformat() if oldest_observation else None, "daily_aggregate": oldest_daily.isoformat() if oldest_daily else None, "collection_run": oldest_collection.isoformat() if oldest_collection else None, "report_run": oldest_report.isoformat() if oldest_report else None},
        "ai_history_size": ai_history_size,
        "growth_basis": "UNKNOWN: no persisted storage-size samples",
        "status": storage_status(free_percent=free_percent, stale=int(periodic.get("stale_campaigns", 0)), retry_backlog=int(periodic.get("retry_backlog", 0))),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
