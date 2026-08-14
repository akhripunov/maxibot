from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import HTTPException

from app.core.config import settings


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
    launch_params = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret_key = hmac.new(b"WebAppData", settings.max_bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, launch_params.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise HTTPException(401, "invalid MAX authorization signature")
    auth_date = int(values.get("auth_date", "0"))
    if auth_date <= 0 or abs(time.time() - auth_date) > 86400:
        raise HTTPException(401, "expired MAX authorization data")
    try:
        user = json.loads(values["user"])
    except (KeyError, ValueError):
        raise HTTPException(401, "MAX user data is missing")
    external_id = user.get("user_id") or user.get("id")
    if external_id in (None, "", 0, "0"):
        raise HTTPException(401, "MAX user id is missing")
    return {"external_id": str(external_id), "user": user, "values": values}
