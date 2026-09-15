"""Standalone Telegram executor entrypoint; intentionally DB/Redis-free."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from .identity_store import IdentityStore
from .protocol import TelegramExecutionRequestV1
from .service import TelegramExecutorService

app = FastAPI(title="MAXIBOT Telegram Executor", docs_url=None, redoc_url=None)
_store = IdentityStore(os.getenv("TELEGRAM_EXECUTOR_IDENTITY_DIR", "/var/lib/maxibot/identities"), os.getenv("TELEGRAM_EXECUTOR_MASTER_KEY_FILE", "/run/secrets/telegram_executor_master_key"))
_service = TelegramExecutorService(_store, region=os.getenv("EXECUTOR_REGION", "EU"), version_sha=os.getenv("MAXIBOT_COMMIT_SHA", "UNKNOWN"))
_replay: dict[str, tuple[float, dict[str, Any]]] = {}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    try:
        _store._fernet()
    except Exception:
        raise HTTPException(status_code=503, detail="IDENTITY_STORE_NOT_READY")
    return {"status": "ready", "region": _service.region}


@app.get("/version")
async def version() -> dict[str, Any]:
    return {"protocol_version": 1, "executor_region": _service.region, "executor_version_sha": _service.version_sha}


@app.get("/v1/identities/{identity_ref}/status")
async def identity_status(identity_ref: str, owner: str) -> dict[str, Any]:
    return _store.status(identity_ref, owner)


@app.post("/v1/execute")
async def execute(request: TelegramExecutionRequestV1) -> dict[str, Any]:
    key = str(request.request_id)
    now = datetime.now(timezone.utc).timestamp()
    for old, (expires, _) in list(_replay.items()):
        if expires <= now:
            _replay.pop(old, None)
    if key in _replay:
        return _replay[key][1]
    try:
        response = await _service.execute(request)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="INVALID_REQUEST") from exc
    encoded = response.model_dump(mode="json")
    _replay[key] = (now + 300, encoded)
    return encoded

