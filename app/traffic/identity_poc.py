"""Small, in-memory identity PoC for the YCLID → MAX JOIN_REQUEST chain.

This module is intentionally not imported by the application runtime.  It is
an executable contract for the proof-of-concept and its tests: no production
database, MAX credentials, polling, webhook subscription, or Yandex API is
involved.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlsplit


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True)
class Visit:
    visitor_id: str
    visit_id: str
    yclid: str
    channel_id: str
    channel_url: str
    start_token: str


@dataclass(frozen=True)
class JoinAttribution:
    join_request_id: str
    channel_id: str
    max_user_id: str
    visitor_id: str
    visit_id: str
    yclid: str


class IdentityPoc:
    """In-memory proof of the exact identity handoff.

    ``resolve_start_token`` consumes a token once and binds the supplied MAX
    user ID to its visit.  ``open_channel`` has no click parameter: the caller
    may invoke it immediately after the Mini App resolves.  A JOIN_REQUEST is
    accepted only when its user and channel match the bound identity.
    """

    def __init__(self, *, signing_key: bytes = b"local-poc-only-key", now: float | None = None) -> None:
        if not signing_key:
            raise ValueError("signing_key is required")
        self._key = signing_key
        self._now = now if now is not None else time.time()
        self._visits: dict[str, Visit] = {}
        self._token_to_visit: dict[str, str] = {}
        self._used_tokens: set[str] = set()
        self._bound_users: dict[str, str] = {}

    def create_visit(self, *, yclid: str, channel_id: str, channel_url: str) -> Visit:
        if not yclid or not channel_id or not self._valid_channel_url(channel_url):
            raise ValueError("yclid, channel_id and an https MAX channel URL are required")
        visitor_id = secrets.token_urlsafe(18)
        visit_id = secrets.token_urlsafe(18)
        payload = {"visit_id": visit_id, "exp": int(self._now) + 300, "nonce": secrets.token_urlsafe(12)}
        body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        signature = _b64(hmac.new(self._key, body.encode(), hashlib.sha256).digest())
        token = f"{body}.{signature}"
        visit = Visit(visitor_id, visit_id, yclid, channel_id, channel_url, token)
        self._visits[visit_id] = visit
        self._token_to_visit[token] = visit_id
        return visit

    def resolve_start_token(self, token: str, *, max_user_id: str) -> Visit:
        if not max_user_id or token in self._used_tokens:
            raise ValueError("start token is missing or already used")
        try:
            body, signature = token.split(".", 1)
            expected = _b64(hmac.new(self._key, body.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(signature, expected):
                raise ValueError("invalid start token signature")
            payload = json.loads(_unb64(body))
            if int(payload["exp"]) < int(self._now):
                raise ValueError("start token expired")
            visit_id = str(payload["visit_id"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("invalid start token") from exc
        visit = self._visits.get(visit_id)
        if visit is None or self._token_to_visit.get(token) != visit_id:
            raise ValueError("unknown start token")
        if visit_id in self._bound_users and self._bound_users[visit_id] != max_user_id:
            raise ValueError("visit is already bound to another MAX user")
        self._used_tokens.add(token)
        self._bound_users[visit_id] = max_user_id
        return visit

    def open_channel(self, *, visit_id: str, max_user_id: str) -> str:
        visit = self._visits.get(visit_id)
        if visit is None or self._bound_users.get(visit_id) != max_user_id:
            raise ValueError("MAX user is not bound to this visit")
        return visit.channel_url

    def observe_join_request(self, *, channel_id: str, max_user_id: str) -> JoinAttribution:
        matches = [
            visit
            for visit_id, visit in self._visits.items()
            if visit.channel_id == channel_id and self._bound_users.get(visit_id) == max_user_id
        ]
        if len(matches) != 1:
            raise ValueError("JOIN_REQUEST does not have one exact bound visit")
        visit = matches[0]
        return JoinAttribution(
            join_request_id=secrets.token_urlsafe(12),
            channel_id=channel_id,
            max_user_id=max_user_id,
            visitor_id=visit.visitor_id,
            visit_id=visit.visit_id,
            yclid=visit.yclid,
        )

    @staticmethod
    def _valid_channel_url(value: str) -> bool:
        parsed = urlsplit(value)
        return parsed.scheme == "https" and parsed.netloc == "max.ru" and bool(parsed.path.strip("/"))
