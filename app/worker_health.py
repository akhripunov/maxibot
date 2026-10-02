"""Small, fail-closed heartbeat contract for the dedicated MAX worker."""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from pathlib import Path

HEARTBEAT_PATH = os.getenv("MAX_WORKER_HEARTBEAT_PATH", "/tmp/maxibot-worker-heartbeat.json")
HEARTBEAT_MAX_AGE_SECONDS = 90


def write_heartbeat(*, marker: int | None, release: str | None = None, now: float | None = None) -> None:
    """Atomically publish a heartbeat only after a successful primary poll."""
    target = Path(HEARTBEAT_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "timestamp": float(time.time() if now is None else now),
        "marker": marker,
        "release": release or os.getenv("MAXIBOT_RELEASE_MARKER", "unknown"),
        "pid": os.getpid(),
    }
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def read_heartbeat(raw: str, *, now: float | None = None, max_age: int = HEARTBEAT_MAX_AGE_SECONDS,
                   expected_release: str | None = None, expected_pid: int | None = None) -> dict:
    """Validate a heartbeat; malformed or stale state is never healthy."""
    try:
        value = json.loads(raw)
        timestamp = float(value["timestamp"])
        if not math.isfinite(timestamp):
            raise ValueError("heartbeat timestamp is not finite")
        if value.get("version") != 1 or timestamp > (now if now is not None else time.time()) + 5:
            raise ValueError("invalid heartbeat timestamp")
        age = (now if now is not None else time.time()) - timestamp
        if age > max_age:
            raise ValueError(f"heartbeat stale: {age:.1f}s")
        if age < -5:
            raise ValueError("heartbeat timestamp is in the future")
        if not isinstance(value.get("pid"), int) or value["pid"] <= 0:
            raise ValueError("invalid heartbeat pid")
        if expected_release is not None and value.get("release") != expected_release:
            raise ValueError("heartbeat release mismatch")
        if expected_pid is not None and value.get("pid") != expected_pid:
            raise ValueError("heartbeat pid mismatch")
        return {**value, "age_seconds": age}
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid worker heartbeat: {exc}") from exc


def evaluate_runtime(*, before: dict, after: dict, heartbeat: dict | None, legacy_success: bool,
                     interval_seconds: int = 30, expected_release: str | None = None,
                     heartbeat_before: dict | None = None) -> tuple[bool, str]:
    """Evaluate a pair of Docker snapshots without treating historical restarts as failure."""
    if after.get("status") != "running":
        return False, "worker_not_running"
    if after.get("oom_killed") is True:
        return False, "worker_oom_killed"
    if not expected_release or after.get("release") != expected_release:
        return False, "worker_release_mismatch"
    if before.get("id") != after.get("id") or before.get("started_at") != after.get("started_at"):
        return False, "worker_identity_changed"
    try:
        delta = int(after.get("restart_count", 0)) - int(before.get("restart_count", 0))
    except (TypeError, ValueError):
        return False, "worker_restart_count_unreadable"
    if delta != 0:
        return False, "worker_restart_delta"
    if heartbeat is None:
        return False, "worker_heartbeat_missing"
    if heartbeat_before is not None:
        if heartbeat.get("timestamp", 0) <= heartbeat_before.get("timestamp", 0):
            return False, "worker_heartbeat_not_advanced"
        if heartbeat.get("marker") == heartbeat_before.get("marker"):
            return False, "primary_marker_not_advanced"
    return True, "heartbeat" if heartbeat is not None else "legacy_updates_success"
