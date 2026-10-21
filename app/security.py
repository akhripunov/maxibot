from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import HTTPException

from app.core.config import settings


async def enforce_public_message_rate_limit(user_id: str) -> None:
    """Apply bounded global and per-sender ingress limits; Redis failure is closed."""
    from redis.asyncio import Redis

    sender_key = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    script = """
    local global_count = redis.call('INCR', KEYS[1])
    if global_count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
    if global_count > 300 then return {global_count, 0} end
    local sender_count = redis.call('INCR', KEYS[2])
    if sender_count == 1 then redis.call('EXPIRE', KEYS[2], ARGV[1]) end
    return {global_count, sender_count}
    """
    redis = None
    try:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        counts = await redis.eval(script, 2, "public-messages:global", f"public-messages:sender:{sender_key}", 60)
    except Exception as exc:
        raise HTTPException(503, "public messaging is temporarily unavailable") from exc
    finally:
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:
                pass
    if int(counts[0]) > 300 or int(counts[1]) > 10:
        raise HTTPException(429, "message rate limit exceeded")


def validate_max_init_data(init_data: str) -> dict:
    if not init_data or not settings.max_bot_token:
        raise HTTPException(401, "MAX authorization is required")
    pairs = parse_qsl(init_data, keep_blank_values=True)
    if not any(key == "hash" for key, _ in pairs):
        wrapped = [value for key, value in pairs if key == "WebAppData"]
        if len(wrapped) == 1:
            init_data = wrapped[0]
            pairs = parse_qsl(init_data, keep_blank_values=True)
    if not pairs or sum(1 for key, _ in pairs if key == "hash") != 1:
        raise HTTPException(401, "invalid MAX authorization data")
    values = dict(pairs)
    received_hash = values.pop("hash")
    if not isinstance(received_hash, str) or len(received_hash) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in received_hash):
        raise HTTPException(401, "invalid MAX authorization signature")
    launch_params = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret_key = hmac.new(b"WebAppData", settings.max_bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, launch_params.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise HTTPException(401, "invalid MAX authorization signature")
    try:
        auth_date = int(values.get("auth_date", "0"))
    except (TypeError, ValueError):
        raise HTTPException(401, "invalid MAX authorization date")
    if auth_date <= 0 or abs(time.time() - auth_date) > 86400:
        raise HTTPException(401, "expired MAX authorization data")
    try:
        user = json.loads(values["user"])
    except (KeyError, ValueError):
        raise HTTPException(401, "MAX user data is missing")
    if not isinstance(user, dict):
        raise HTTPException(401, "MAX user data is invalid")
    external_id = user.get("user_id") or user.get("id")
    if (external_id in (None, "", 0, "0") or isinstance(external_id, bool)
            or not isinstance(external_id, (str, int)) or len(str(external_id)) > 128):
        raise HTTPException(401, "MAX user id is missing")
    return {"external_id": str(external_id), "user": user, "values": values}
