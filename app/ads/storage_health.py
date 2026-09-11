"""Read-only storage diagnostics used by the admin ADS screen."""
from __future__ import annotations

import shutil
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ads.periodic import collection_health
from app.core.config import settings
from app.db.models import AdsCollectionRun, AdsCreative, AdsDailyAggregate, AdsObservation, AdsReportRun

TABLES = (
    "ads_accounts", "ads_campaigns", "ads_promotion_campaign_links",
    "ads_entities", "ads_creatives", "ads_observations",
    "ads_daily_aggregates", "ads_collection_runs", "ads_report_runs",
    "ads_analysis_snapshots", "ads_ai_runs", "ads_recommendations",
    "ads_ai_outcomes", "ads_memories", "ads_memory_evidence", "ads_data_health",
)
TABLE_LATEST_COLUMNS = {
    "ads_accounts": "updated_at",
    "ads_campaigns": "last_seen_at",
    "ads_promotion_campaign_links": "updated_at",
    "ads_entities": "last_seen_at",
    "ads_creatives": "updated_at",
    "ads_observations": "created_at",
    "ads_daily_aggregates": "created_at",
    "ads_collection_runs": "completed_at",
    "ads_report_runs": "fetched_at",
    "ads_analysis_snapshots": "created_at",
    "ads_ai_runs": "created_at",
    "ads_recommendations": "created_at",
    "ads_ai_outcomes": "completed_at",
    "ads_memories": "updated_at",
    "ads_memory_evidence": "created_at",
    "ads_data_health": "checked_at",
}
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
    table_details: dict[str, dict[str, object]] = {}
    now = datetime.now(timezone.utc)
    growth_by_days = {7: 0.0, 30: 0.0}
    for table in TABLES:
        relation = int(await db.scalar(text("select pg_relation_size(:table)"), {"table": table}) or 0)
        total = int(await db.scalar(text("select pg_total_relation_size(:table)"), {"table": table}) or 0)
        sizes[table] = {"data": relation, "indexes": max(0, total - relation), "total": total}
        row_count = int(await db.scalar(text(f'select count(*) from "{table}"')) or 0)
        latest_column = TABLE_LATEST_COLUMNS[table]
        latest = await db.scalar(text(f'select max("{latest_column}") from "{table}"'))
        if latest is not None and hasattr(latest, "isoformat"):
            latest = latest.isoformat()
        table_details[table] = {
            "name": table,
            "size": total,
            "row_count": row_count,
            "latest": latest,
            "retention_class": _retention_class(table),
            "status": "EMPTY" if row_count == 0 else "ACTIVE",
        }
        if row_count:
            for days in growth_by_days:
                cutoff = now - timedelta(days=days)
                recent_rows = int(await db.scalar(text(
                    f'select count(*) from "{table}" where "{latest_column}" >= :cutoff'
                ), {"cutoff": cutoff}) or 0)
                growth_by_days[days] += total * (recent_rows / row_count)
    disk = shutil.disk_usage("/")
    free_percent = (disk.free / disk.total * 100.0) if disk.total else None
    periodic = await collection_health(db)
    oldest_observation = await db.scalar(select(func.min(AdsObservation.created_at)))
    oldest_daily = await db.scalar(select(func.min(AdsDailyAggregate.aggregate_date)))
    oldest_collection = await db.scalar(select(func.min(AdsCollectionRun.scheduled_at)).where(AdsCollectionRun.run_type != "RETENTION"))
    oldest_report = await db.scalar(select(func.min(AdsReportRun.fetched_at)))
    retention_run = await db.scalar(select(AdsCollectionRun).where(AdsCollectionRun.run_type == "RETENTION").order_by(AdsCollectionRun.completed_at.desc()).limit(1))
    retention_summary = json.loads(retention_run.summary_json or "{}") if retention_run and retention_run.summary_json else {}
    creative_summary = {
        "records": int(await db.scalar(select(func.count(AdsCreative.id))) or 0),
        "unique_hashes": int(await db.scalar(select(func.count(func.distinct(AdsCreative.content_hash)))) or 0),
        "media_stored": int(await db.scalar(select(func.count(AdsCreative.id)).where(AdsCreative.media_storage_state == "STORED")) or 0),
        "media_missing": int(await db.scalar(select(func.count(AdsCreative.id)).where(AdsCreative.media_storage_state.in_(["SOURCE_UNAVAILABLE", "AVAILABLE_NOT_STORED"]))) or 0),
        "upload_failed": int(await db.scalar(select(func.count(AdsCreative.id)).where(AdsCreative.media_storage_state == "FAILED")) or 0),
        "media_bytes": int(await db.scalar(select(func.coalesce(func.sum(AdsCreative.file_size), 0))) or 0),
        "images": int(await db.scalar(select(func.count(AdsCreative.id)).where(AdsCreative.creative_type.ilike("%IMAGE%"))) or 0),
        "videos": int(await db.scalar(select(func.count(AdsCreative.id)).where(AdsCreative.creative_type.ilike("%VIDEO%"))) or 0),
        "mail_configured": bool(settings.mail_cloud_enabled and settings.mail_cloud_webdav_url and settings.mail_cloud_login and settings.mail_cloud_app_password),
        "storage_enabled": bool(settings.mail_cloud_enabled),
        "last_successful_upload": None,
        "last_error": None,
    }
    ai_history_tables = {name: sizes[name] for name in ("ads_analysis_snapshots", "ads_ai_runs", "ads_recommendations", "ads_ai_outcomes", "ads_memories", "ads_memory_evidence")}
    ai_history_size = sum(item["total"] for item in ai_history_tables.values())
    return {
        "database": {"total": db_size}, "tables": sizes, "table_details": table_details,
        "ads_total": sum(item["total"] for item in sizes.values()),
        "disk": {"total": disk.total, "used": disk.used, "free": disk.free, "free_percent": free_percent},
        "periodic": periodic,
        "growth_7d": int(growth_by_days[7]),
        "growth_30d": int(growth_by_days[30]),
        "projection": None,
        "retention": {"observations_days": settings.ads_observations_retention_days, "operational_days": settings.ads_operational_retention_days, "data_health_days": settings.ads_data_health_retention_days, "hourly_days": settings.ads_observations_retention_days, "daily_days": 0, "daily_aggregates": "LONG_TERM", "ai_history": "LONG_TERM", "metadata": "LONG_TERM", "last_run": retention_run.completed_at.isoformat() if retention_run and retention_run.completed_at else None, "deleted_rows": int(retention_summary.get("deleted", 0)) + int(retention_summary.get("deleted_collection_runs", 0)) + int(retention_summary.get("deleted_report_runs", 0)), "skipped_rows": int(retention_summary.get("skipped", 0)), "skip_reason": "daily aggregate coverage missing" if retention_summary.get("skipped") else None},
        "downsampling": {"eligible": None, "protected": None, "dry_run": True},
        "oldest": {"ads_observation": oldest_observation.isoformat() if oldest_observation else None, "daily_aggregate": oldest_daily.isoformat() if oldest_daily else None, "collection_run": oldest_collection.isoformat() if oldest_collection else None, "report_run": oldest_report.isoformat() if oldest_report else None},
        "ai_history_size": ai_history_size,
        "creatives": creative_summary,
        "growth_basis": "Оценка по новым строкам; исторические размеры БД отдельно не сохраняются",
        "status": storage_status(free_percent=free_percent, stale=int(periodic.get("stale_campaigns", 0)), retry_backlog=int(periodic.get("retry_backlog", 0))),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _retention_class(table: str) -> str:
    if table in {"ads_observations"}:
        return f"{settings.ads_observations_retention_days}d"
    if table in {"ads_collection_runs", "ads_report_runs", "ads_data_health"}:
        return f"{settings.ads_operational_retention_days}d"
    return "LONG_TERM"
