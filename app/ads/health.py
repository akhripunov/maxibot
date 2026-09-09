from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

def sufficiency(*, mapped_campaigns: int, report_days: int, conversions: float | None, attribution_available: bool, direct_available: bool) -> tuple[str, list[str]]:
    missing: list[str] = []
    if not direct_available: missing.append("DIRECT_SYNC")
    if not mapped_campaigns: missing.append("CAMPAIGN_MAPPING")
    if report_days < 3: missing.append("SNAPSHOT_HISTORY")
    if not attribution_available: missing.append("ATTRIBUTION")
    if conversions is None: missing.append("CONVERSIONS")
    if len(missing) >= 3: return "INSUFFICIENT", missing
    return ("SUFFICIENT" if not missing else "PARTIAL"), missing

def data_health(*, direct_available: bool, mapped_campaigns: int, attribution_available: bool, snapshot_fresh: bool, ai_available: bool, memory_db_available: bool, unmatched_attribution: int | None = None) -> dict[str, Any]:
    checks = {
        "DIRECT_SYNC": "HEALTHY" if direct_available else "UNHEALTHY",
        "CAMPAIGN_MAPPING": "HEALTHY" if mapped_campaigns else "DEGRADED",
        "ATTRIBUTION": "HEALTHY" if attribution_available else "DEGRADED",
        "SNAPSHOT_FRESHNESS": "HEALTHY" if snapshot_fresh else "DEGRADED",
        "AI_PROVIDER": "HEALTHY" if ai_available else "DEGRADED",
        "MEMORY_DATABASE": "HEALTHY" if memory_db_available else "UNHEALTHY",
    }
    if unmatched_attribution is not None: checks["UNMATCHED_ATTRIBUTION"] = "DEGRADED" if unmatched_attribution else "HEALTHY"
    status = "UNHEALTHY" if "UNHEALTHY" in checks.values() else ("DEGRADED" if "DEGRADED" in checks.values() else "HEALTHY")
    return {"status": status, "checks": checks, "checked_at": datetime.now(timezone.utc).isoformat()}
