"""Stable business idempotency keys for paid BOT conversions."""
from __future__ import annotations

import hashlib


def paid_bot_conversion_key(*, bot_id: str, max_user_id: str, yclid: str | None) -> str | None:
    """Return a non-reversible key for one paid BOT journey."""
    if not bot_id or not max_user_id or not yclid or not yclid.strip():
        return None
    value = "\x1f".join((bot_id, max_user_id, yclid.strip()))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
