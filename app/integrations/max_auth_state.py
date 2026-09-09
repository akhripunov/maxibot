"""Persisted, minimal MAX Web auth state.

The file contains only operator-supplied WebSocket headers.  It is never
logged and is deliberately not bundled with captures or source code.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


class MaxAuthStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class MaxAuthState:
    headers: dict[str, str]

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "MaxAuthState":
        auth_path = Path(path)
        try:
            raw = json.loads(auth_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise MaxAuthStateError("MAX_AUTH_EXPIRED") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise MaxAuthStateError("MAX_AUTH_STATE_INVALID") from exc
        headers = raw.get("headers") if isinstance(raw, dict) else None
        if not isinstance(headers, dict) or not headers:
            raise MaxAuthStateError("MAX_AUTH_STATE_INVALID")
        normalized = {str(key): str(value) for key, value in headers.items() if value}
        if not normalized:
            raise MaxAuthStateError("MAX_AUTH_STATE_INVALID")
        return cls(normalized)

    def save(self, path: str | os.PathLike[str]) -> None:
        auth_path = Path(path)
        auth_path.parent.mkdir(parents=True, exist_ok=True)
        auth_path.write_text(json.dumps({"headers": self.headers}, indent=2) + "\n", encoding="utf-8")
        try:
            auth_path.chmod(0o600)
        except OSError:
            pass


class PersistedMaxAuth:
    def __init__(self, state: MaxAuthState):
        self.state = state

    async def get_auth_headers(self) -> dict[str, str]:
        return dict(self.state.headers)
