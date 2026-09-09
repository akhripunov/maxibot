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
    # This is the bot deep-link parameter that produces the bot_started event
    # in the MAX Bot API.  The server-side token remains opaque; no visitor
    # data is placed in the URL.
    return f"https://max.ru/{username}?start={quote(token, safe='')}"


def build_web_bot_deep_link(username: str, token: str) -> str:
    """Build the browser-compatible MAX Web bot link for desktop sessions."""
    username = (username or "").lstrip("@").strip()
    if not username or not token:
        raise ValueError("bot username and attribution token are required")
    return f"https://web.max.ru/{username}?start={quote(token, safe='')}"


def build_miniapp_deep_link(username: str, token: str) -> str:
    """Build the MAX Mini App link that carries an opaque startapp token."""
    username = (username or "").lstrip("@").strip()
    if not username or not token:
        raise ValueError("bot username and attribution token are required")
    return f"https://max.ru/{username}?startapp={quote(token, safe='')}"


def build_native_miniapp_deep_link(username: str, token: str) -> str:
    """Build the native MAX scheme used to launch the Mini App directly."""
    username = (username or "").lstrip("@").strip()
    if not username or not token:
        raise ValueError("bot username and attribution token are required")
    return f"max://max.ru/{username}?startapp={quote(token, safe='')}"


def build_web_miniapp_deep_link(username: str, token: str) -> str:
    """Build the MAX Web Mini App link for desktop browser sessions."""
    username = (username or "").lstrip("@").strip()
    if not username or not token:
        raise ValueError("bot username and attribution token are required")
    return f"https://web.max.ru/{username}?startapp={quote(token, safe='')}"


def valid_max_target(target: str | None) -> bool:
    if not target:
        return False
    parsed = urlsplit(target)
    return parsed.scheme == "https" and parsed.netloc == "max.ru" and bool(parsed.path.strip("/"))
