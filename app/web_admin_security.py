"""Browser boundary and abuse controls for the owner WebAuthn console."""

from __future__ import annotations

import hashlib

from fastapi import HTTPException, Request
from redis.asyncio import Redis

from app.core.config import settings


def is_owner_console_path(path: str) -> bool:
    return (
        path == "/admin" or path.startswith("/admin/")
        or path == "/api/admin" or path.startswith("/api/admin/")
        or path == "/static/admin" or path.startswith("/static/admin/")
    )


def is_machine_qa_ingest(path: str) -> bool:
    return path == "/api/admin/qa-runs/ingest"


def require_owner_console_host(request: Request) -> None:
    # Host selects which public site is served; it never authenticates a user.
    if (request.url.hostname or "").lower().rstrip(".") != settings.web_admin_rp_id.lower().rstrip("."):
        raise HTTPException(status_code=404, detail="not found")


def require_owner_console_origin(request: Request) -> None:
    require_owner_console_host(request)
    origin = request.headers.get("origin")
    if not origin or origin != settings.web_admin_origin:
        raise HTTPException(status_code=403, detail="owner console origin required")


async def enforce_web_admin_rate_limit(request: Request, action: str) -> None:
    """Fail closed with bounded per-action and per-peer ceremony limits."""
    limits = {
        "login-options": (10, 60),
        "login-verify": (10, 60),
        "register-options": (3, 600),
        "register-verify": (3, 600),
    }
    limit, period = limits[action]
    peer = request.client.host if request.client else "unknown"
    peer_hash = hashlib.sha256(peer.encode("utf-8")).hexdigest()
    key = f"web-admin:{action}:{peer_hash}"
    redis = None
    try:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        count = await redis.eval(
            "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n",
            1,
            key,
            period,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="owner console auth is temporarily unavailable") from exc
    finally:
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:
                pass
    if int(count) > limit:
        raise HTTPException(status_code=429, detail="owner console authentication rate limit exceeded")


def owner_console_security_headers() -> dict[str, str]:
    return {
        "Content-Security-Policy": "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; upgrade-insecure-requests",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Referrer-Policy": "no-referrer",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    }
