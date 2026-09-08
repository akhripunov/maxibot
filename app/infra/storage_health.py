"""Read-only host storage samples mounted into the API container."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _path() -> Path:
    return Path(os.getenv("STORAGE_SAMPLES_PATH", "/var/lib/maxibot/storage/storage-samples.jsonl"))


def diagnostics() -> dict[str, object]:
    samples: list[dict[str, object]] = []
    try:
        for line in _path().read_text(encoding="utf-8").splitlines()[-1000:]:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and isinstance(item.get("timestamp"), (int, float)):
                samples.append(item)
    except OSError:
        return {"status": "UNKNOWN", "reason": "storage_samples_unavailable", "samples": 0, "read_only": True}
    if not samples:
        return {"status": "UNKNOWN", "reason": "storage_samples_empty", "samples": 0, "read_only": True}
    latest = samples[-1]
    disk = latest.get("disk") if isinstance(latest.get("disk"), dict) else {}
    total = int(disk.get("total") or 0)
    used = int(disk.get("used") or 0)
    free = int(disk.get("free") or 0)
    growth_7d = None
    cutoff = time.time() - 7 * 86400
    older = [item for item in samples if float(item["timestamp"]) <= cutoff]
    if older:
        old_disk = older[-1].get("disk") or {}
        growth_7d = used - int(old_disk.get("used") or used)
    free_percent = (free / total * 100) if total else None
    status = "OK" if free_percent is not None and free_percent >= 15 else "WARN" if free_percent is not None and free_percent >= 8 else "CRITICAL"
    return {
        "status": status,
        "reason": "disk_free_below_threshold" if status != "OK" else None,
        "checked_at": latest["timestamp"],
        "samples": len(samples),
        "disk": {"total": total, "used": used, "free": free, "free_percent": free_percent},
        "growth_7d": growth_7d,
        "containerd": latest.get("containerd"),
        "releases": latest.get("releases"),
        "postgres": latest.get("postgres"),
        "ads": latest.get("ads"),
        "logs": latest.get("logs"),
        "backups": latest.get("backups"),
        "read_only": True,
    }
