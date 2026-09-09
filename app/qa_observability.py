"""Validated, sanitized QA run ingestion and read-only serialization helpers."""

from __future__ import annotations

import json
import hashlib
import hmac
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import QaRun, QaSuiteResult

UNKNOWN = "UNKNOWN"
UNVERIFIED = "UNVERIFIED"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SUITE_NAMES = ("static", "unit", "integration", "regression", "build", "staging", "e2e")
SUITE_STATUSES = {"PASS", "FAIL", "NOT_RUN", UNKNOWN, UNVERIFIED}


def _text(value: Any, *, limit: int = 256) -> str:
    value = str(value or "").strip()
    return value[:limit]


def _sha(value: Any) -> str:
    value = _text(value, limit=64).lower()
    return value if SHA_RE.fullmatch(value) else UNKNOWN


def _int(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return _int(value)


def _iso(value: Any) -> str | None:
    if not value:
        return None
    value = _text(value, limit=64)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _safe_failures(value: Any) -> list[dict[str, str]]:
    """Keep only test identity/status; never persist raw failure messages."""
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value[:100]:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name") or item.get("test") or "unknown-test", limit=200)
        status = _text(item.get("status") or "FAIL", limit=16).upper()
        result.append({"name": name, "status": status if status in {"FAIL", "FAILED", "ERROR"} else "FAIL"})
    return result


def normalize_qa_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the machine artifact and return only the safe canonical shape."""
    if not isinstance(payload, dict):
        raise ValueError("QA artifact must be an object")
    artifact_sha256 = _text(payload.get("artifact_sha256"), limit=64).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", artifact_sha256):
        raise ValueError("QA artifact must contain artifact_sha256")
    unsigned = dict(payload)
    unsigned.pop("artifact_sha256", None)
    expected_sha256 = hashlib.sha256(json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if not hmac.compare_digest(artifact_sha256, expected_sha256):
        raise ValueError("QA artifact hash mismatch")
    tested_sha = _sha(payload.get("tested_sha"))
    if tested_sha == UNKNOWN:
        raise ValueError("QA artifact must contain an exact 40-character tested_sha")
    verdict = _text(payload.get("verdict"), limit=16).upper()
    if verdict not in {"GO", "NO_GO"}:
        verdict = UNKNOWN
    production = payload.get("production") if isinstance(payload.get("production"), dict) else {}
    counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
    registry = payload.get("case_registry") if isinstance(payload.get("case_registry"), dict) else {}
    timing = payload.get("timing") if isinstance(payload.get("timing"), dict) else {}
    suites_payload = payload.get("suites") if isinstance(payload.get("suites"), list) else []
    by_name = {str(item.get("name")): item for item in suites_payload if isinstance(item, dict)}
    suites = []
    for name in SUITE_NAMES:
        item = by_name.get(name, {})
        status = _text(item.get("status"), limit=16).upper()
        suites.append({
            "name": name,
            "status": status if status in SUITE_STATUSES else UNVERIFIED,
            "total_tests": _optional_int(item.get("total_tests")),
            "passed": _optional_int(item.get("passed")),
            "failed": _optional_int(item.get("failed")),
            "skipped": _optional_int(item.get("skipped")),
            "duration_ms": _optional_int(item.get("duration_ms")),
            "failures": _safe_failures(item.get("failures")),
            "unverified": [_text(x, limit=64) for x in (item.get("unverified") or [])[:50]] if isinstance(item.get("unverified"), list) else [],
        })
    return {
        "schema_version": "qa_observability.v1",
        "release_id": _text(payload.get("release_id"), limit=128) or UNKNOWN,
        "tested_sha": tested_sha,
        "current_main_sha": _sha(payload.get("current_main_sha")),
        "production_sha": _sha(payload.get("production_sha")),
        "verdict": verdict,
        "timing": {"started_at": _iso(timing.get("started_at")), "finished_at": _iso(timing.get("finished_at")), "duration_ms": _optional_int(timing.get("duration_ms"))},
        "counts": {key: _int(counts.get(key)) for key in ("total_tests", "passed", "failed", "skipped")},
        "production": {
            "status": _text(production.get("status"), limit=24) or UNKNOWN,
            "smoke": _text(production.get("smoke"), limit=24) or UNVERIFIED,
            "changed": bool(production.get("changed", False)),
        },
        "artifact_sha256": artifact_sha256,
        "source_path": _text(payload.get("source_path"), limit=255) or None,
        "suites": suites,
        "failures": _safe_failures(payload.get("failures")),
        "unverified": {
            "total": _int(registry.get("unknown")),
            "critical": _int(registry.get("unknown_critical")),
            "statuses": ["WORKING_UNTESTED", "UNKNOWN"],
        },
    }


def parity_status(tested_sha: str, other_sha: str | None) -> str:
    if not tested_sha or tested_sha == UNKNOWN or not other_sha or other_sha == UNKNOWN:
        return UNKNOWN
    return "MATCH" if tested_sha == other_sha else "MISMATCH"


async def persist_qa_result(db: AsyncSession, payload: dict[str, Any]) -> QaRun:
    data = normalize_qa_result(payload)
    run = await db.scalar(select(QaRun).where(QaRun.release_id == data["release_id"]))
    if run is None:
        run = QaRun(release_id=data["release_id"])
        db.add(run)
    for key, value in {
        "tested_sha": data["tested_sha"],
        "current_main_sha": data["current_main_sha"],
        "production_sha": data["production_sha"],
        "verdict": data["verdict"],
        "started_at": datetime.fromisoformat(data["timing"]["started_at"]) if data["timing"]["started_at"] else None,
        "finished_at": datetime.fromisoformat(data["timing"]["finished_at"]) if data["timing"]["finished_at"] else None,
        "duration_ms": data["timing"]["duration_ms"],
        "total_tests": data["counts"]["total_tests"],
        "passed": data["counts"]["passed"],
        "failed": data["counts"]["failed"],
        "skipped": data["counts"]["skipped"],
        "production_status": data["production"]["status"],
        "production_smoke": data["production"]["smoke"],
        "artifact_sha256": data["artifact_sha256"],
        "source_path": data["source_path"],
        "failures_json": json.dumps(data["failures"], ensure_ascii=False),
        "unverified_json": json.dumps(data["unverified"], ensure_ascii=False),
    }.items():
        setattr(run, key, value)
    await db.flush()
    await db.execute(delete(QaSuiteResult).where(QaSuiteResult.run_id == run.id))
    for suite in data["suites"]:
        db.add(QaSuiteResult(
            run_id=run.id,
            suite_name=suite["name"],
            status=suite["status"],
            total_tests=suite["total_tests"],
            passed=suite["passed"],
            failed=suite["failed"],
            skipped=suite["skipped"],
            duration_ms=suite["duration_ms"],
            failures_json=json.dumps(suite["failures"], ensure_ascii=False),
            unverified_json=json.dumps(suite["unverified"], ensure_ascii=False),
        ))
    await db.commit()
    await db.refresh(run)
    return run


def decode_json(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback
