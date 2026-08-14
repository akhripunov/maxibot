"""Safe MAX pending-request worker boundary.

The MAX Web single-user RPC is intentionally not guessed here.  A provider
must be supplied after a real, own-account network capture proves its request
and response schema.  Until then the worker remains disabled/auth-required and
cannot send any approval request.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, Any

from app.integrations.max_web_session import MaxWebSession


PENDING = "PENDING"
APPROVING = "APPROVING"
APPROVED = "APPROVED"
CONFIRMED = "CONFIRMED"
FAILED = "FAILED"
AUTH_REQUIRED = "AUTH_REQUIRED"
STATE_NOT_CONFIRMED = "STATE_NOT_CONFIRMED"
CONFIRMED_BY_PENDING_REMOVAL = "CONFIRMED_BY_PENDING_REMOVAL"
CONFIRMED_BY_MEMBERSHIP = "CONFIRMED_BY_MEMBERSHIP"


@dataclass(frozen=True)
class PendingRequest:
    channel_id: str
    max_user_id: str
    remote_request_id: str | None = None
    requested_at: datetime | None = None


class PendingRequestProvider(Protocol):
    async def list_pending(self, channel_id: str) -> list[PendingRequest]: ...


class SingleRequestApprover(Protocol):
    async def approve_one(self, request: PendingRequest) -> dict[str, Any]: ...


class MembershipVerifier(Protocol):
    async def verify(self, request: PendingRequest) -> bool: ...


class AutoApproveUnavailable(RuntimeError):
    """Raised when no proven MAX single-approve transport is configured."""


class MaxWebPendingProvider:
    """Adapter keeping the worker independent from the WebSocket transport."""

    def __init__(self, session: MaxWebSession):
        self.session = session

    async def list_pending(self, channel_id: str) -> list[PendingRequest]:
        rows = await self.session.list_pending_join_requests(channel_id)
        return [
            PendingRequest(
                channel_id=row.channel_id,
                max_user_id=row.user_id,
                remote_request_id=row.remote_request_id,
                requested_at=row.requested_at,
            )
            for row in rows
        ]


class MaxWebSingleRequestApprover:
    """Single-user approve adapter with response validation."""

    def __init__(self, session: MaxWebSession):
        self.session = session

    async def approve_one(self, request: PendingRequest) -> dict[str, Any]:
        result = await self.session.approve_join_request(request.channel_id, request.max_user_id)
        if not result.ok:
            raise RuntimeError("MAX approve response was not successful")
        return {"ok": True, "already_member": result.already_member, "raw": result.raw}


class MaxAutoApproveWorker:
    def __init__(self, provider: PendingRequestProvider | None, approver: SingleRequestApprover | None,
                 *, enabled: bool = False, max_attempts: int = 3, delay_ms: int = 0,
                 allowed_channel_ids: set[str] | None = None,
                 membership_verifier: MembershipVerifier | None = None,
                 verify_pending_after_approve: bool = False):
        self.provider = provider
        self.approver = approver
        self.enabled = enabled
        self.max_attempts = max_attempts
        self.delay_ms = max(0, delay_ms)
        self.allowed_channel_ids = allowed_channel_ids
        self.membership_verifier = membership_verifier
        self.verify_pending_after_approve = verify_pending_after_approve
        self.paused_reason: str | None = None
        self._terminal_keys: set[tuple[str, str]] = set()
        self._channel_locks: dict[str, asyncio.Lock] = {}

    async def process_channel(self, channel_id: str) -> list[dict[str, Any]]:
        lock = self._channel_locks.setdefault(channel_id, asyncio.Lock())
        if lock.locked():
            return [{"status": "SINGLE_FLIGHT_BUSY", "channel_id": channel_id}]
        async with lock:
            return await self._process_channel_locked(channel_id)

    async def _process_channel_locked(self, channel_id: str) -> list[dict[str, Any]]:
        if not self.enabled:
            return [{"status": "DISABLED", "channel_id": channel_id}]
        if not self.provider or not self.approver:
            self.paused_reason = "AUTH_REQUIRED"
            return [{"status": AUTH_REQUIRED, "channel_id": channel_id, "reason": "approve_transport_not_configured"}]
        if self.allowed_channel_ids is not None and channel_id not in self.allowed_channel_ids:
            return [{"status": "CHANNEL_NOT_ALLOWED", "channel_id": channel_id}]
        pending = await self.provider.list_pending(channel_id)
        results = []
        for request in pending:
            key = (request.channel_id, request.max_user_id)
            if key in self._terminal_keys:
                results.append({"channel_id": request.channel_id, "max_user_id": request.max_user_id,
                                "status": "SKIPPED_DUPLICATE"})
                continue
            results.append(await self._approve_one(request))
            if results[-1].get("status") in {APPROVED, CONFIRMED, AUTH_REQUIRED}:
                self._terminal_keys.add(key)
        return results

    async def _approve_one(self, request: PendingRequest) -> dict[str, Any]:
        if self.delay_ms:
            await asyncio.sleep(self.delay_ms / 1000)
        try:
            response = await self.approver.approve_one(request)
            if not response.get("ok"):
                return {"channel_id": request.channel_id, "max_user_id": request.max_user_id,
                        "status": FAILED, "retryable": False, "response": response}
            status = APPROVED
            if self.verify_pending_after_approve:
                after = await self.provider.list_pending(request.channel_id)
                if any(row.max_user_id == request.max_user_id for row in after):
                    return {"channel_id": request.channel_id, "max_user_id": request.max_user_id,
                            "status": STATE_NOT_CONFIRMED, "retryable": False, "response": response}
                status = CONFIRMED_BY_PENDING_REMOVAL
            if self.membership_verifier is not None:
                verified = await self.membership_verifier.verify(request)
                if not verified:
                    return {"channel_id": request.channel_id, "max_user_id": request.max_user_id,
                            "status": FAILED, "retryable": False,
                            "error": "membership_verification_failed", "response": response}
                status = CONFIRMED_BY_MEMBERSHIP if self.verify_pending_after_approve else CONFIRMED
            return {"channel_id": request.channel_id, "max_user_id": request.max_user_id,
                    "status": status, "approved_at": datetime.now(timezone.utc), "response": response}
        except PermissionError as exc:
            self.paused_reason = AUTH_REQUIRED
            return {"channel_id": request.channel_id, "max_user_id": request.max_user_id,
                    "status": AUTH_REQUIRED, "error": str(exc)}
        except AutoApproveUnavailable as exc:
            self.paused_reason = AUTH_REQUIRED
            return {"channel_id": request.channel_id, "max_user_id": request.max_user_id,
                    "status": AUTH_REQUIRED, "error": str(exc)}
        except (TimeoutError, ConnectionError) as exc:
            return {"channel_id": request.channel_id, "max_user_id": request.max_user_id,
                    "status": FAILED, "retryable": True, "error": str(exc)}
        except Exception as exc:
            return {"channel_id": request.channel_id, "max_user_id": request.max_user_id,
                    "status": FAILED, "retryable": False, "error": type(exc).__name__}
