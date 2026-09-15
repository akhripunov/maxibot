"""Validated external-prior manifest adapter; raw corpus never enters ADS DB."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def validate_manifest(manifest: Mapping[str, Any], *, now: datetime | None = None) -> bool:
    if manifest.get("version") != 1 or manifest.get("scope") != "EXTERNAL":
        return False
    digest = str(manifest.get("content_hash") or "")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        return False
    if not manifest.get("artifact_id") or not manifest.get("schema_version"):
        return False
    now = now or datetime.now(timezone.utc)
    expires = manifest.get("expires_at")
    if expires:
        try:
            when = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            if when.tzinfo is None: when = when.replace(tzinfo=timezone.utc)
            if when <= now: return False
        except ValueError:
            return False
    return True


def load_manifest(path: str | Path) -> dict[str, Any] | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and validate_manifest(data) else None


def verify_artifact_bytes(payload: bytes, manifest: Mapping[str, Any]) -> bool:
    return validate_manifest(manifest) and hashlib.sha256(payload).hexdigest() == manifest.get("content_hash")
