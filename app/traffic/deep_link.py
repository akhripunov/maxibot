"""Helpers for the optional MAX bot attribution path."""

from __future__ import annotations

import secrets
from urllib.parse import quote, urlsplit


def new_attribution_token() -> str:
    """Return an opaque URL-safe reference; no visitor data is embedded."""
    return secrets.token_urlsafe(24)


def build_bot_deep_link(username: str, token: str) -> str:
    username = (username or "").lstrip("@").strip()
    if not username or not token:
        raise ValueError("bot username and attribution token are required")
    return f"https://max.ru/{username}?start={quote(token, safe='')}"


def valid_max_target(target: str | None) -> bool:
    if not target:
        return False
    parsed = urlsplit(target)
    return parsed.scheme == "https" and parsed.netloc == "max.ru" and bool(parsed.path.strip("/"))
