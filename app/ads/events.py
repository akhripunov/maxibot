from __future__ import annotations

import json
import logging
from typing import Any

from redis.asyncio import Redis

from app.core.config import settings

logger = logging.getLogger(__name__)
ADS_AI_RUN_EVENTS = "maxibot:ads_ai_run_events"


async def publish_ai_run_event(run_id: str, status: str) -> None:
    """Publish a committed terminal run state; delivery is deliberately best effort."""
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis.publish(ADS_AI_RUN_EVENTS, json.dumps({"run_id": run_id, "status": status}))
    finally:
        await redis.aclose()


def parse_ai_run_event(value: Any) -> tuple[str, str] | None:
    try:
        event = json.loads(value) if isinstance(value, str) else value
        run_id, status = str(event["run_id"]), str(event["status"])
    except (TypeError, ValueError, KeyError):
        return None
    if status not in {"CODEX_COMPLETED", "CODEX_FAILED"} or not run_id:
        return None
    return run_id, status
