"""Fail-closed MAX Web transport and application facade.

The application contract is production-shaped, while the undocumented MAX
binary codec is injected.  No captured frame, credential, test chat or test
user is embedded here.  A real deployment must provide a proven codec and
credential provider explicitly.
"""
from __future__ import annotations

import asyncio
import enum
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol

from app.integrations.max_auth_state import MaxAuthState, PersistedMaxAuth


LOGGER = logging.getLogger(__name__)


class MaxSessionState(str, enum.Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    AUTHENTICATING = "AUTHENTICATING"
    AUTHENTICATED = "AUTHENTICATED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    ERROR = "ERROR"


class MaxWebTransportError(RuntimeError):
    pass


class MaxWebAuthRequired(MaxWebTransportError):
    pass


class MaxWebProtocolUnavailable(MaxWebTransportError):
    pass


class MaxWebRateLimited(MaxWebTransportError):
    pass


class MaxWebTimeout(MaxWebTransportError):
    pass


class MaxWebDisconnected(MaxWebTransportError):
    pass


@dataclass(frozen=True)
class PendingJoinRequest:
    channel_id: str
    user_id: str
    requested_at: datetime | None = None
    remote_request_id: str | None = None
    display_name: str | None = None
    status: str = "JOIN_REQUEST"

    def to_dict(self) -> dict[str, Any]:
        return {"chat_id": self.channel_id, "user_id": self.user_id,
                "name": self.display_name, "status": self.status}


@dataclass(frozen=True)
class ApproveResult:
    ok: bool
    already_member: bool = False
    raw: dict[str, Any] | None = None
    request_id: str | None = None
    duration_ms: int | None = None


@dataclass(frozen=True)
class RpcEnvelope:
    request_id: str
    opcode: int
    payload: dict[str, Any]
    kind: str = "response"
    push: bool = False


class AuthProvider(Protocol):
    async def get_auth_headers(self) -> dict[str, str]: ...


class FrameCodec(Protocol):
    def encode_auth(self, *, request_id: str) -> bytes: ...

    def encode_rpc(self, *, request_id: str, opcode: int, payload: dict[str, Any]) -> bytes: ...

    def decode(self, frame: bytes) -> RpcEnvelope: ...


class WebSocketLike(Protocol):
    async def send(self, data: bytes) -> None: ...

    async def recv(self) -> bytes: ...

    async def close(self) -> None: ...


SocketFactory = Callable[[dict[str, str]], Awaitable[WebSocketLike]]


class UnconfiguredMaxCodec:
    """Prevents accidentally sending static or guessed MAX frames."""

    def encode_auth(self, *, request_id: str) -> bytes:
        raise MaxWebProtocolUnavailable("MAX binary auth codec is not configured")

    def encode_rpc(self, *, request_id: str, opcode: int, payload: dict[str, Any]) -> bytes:
        if opcode not in {59, 77}:
            raise MaxWebProtocolUnavailable("unsupported MAX opcode")
        raise MaxWebProtocolUnavailable("MAX dynamic binary codec is not configured")

    def decode(self, frame: bytes) -> RpcEnvelope:
        raise MaxWebProtocolUnavailable("MAX binary decoder is not configured")


class MaxWebSession:
    """Authenticated, correlated session facade with destructive-operation guards."""

    def __init__(self, auth: AuthProvider | None = None,
                 socket_factory: SocketFactory | None = None,
                 codec: FrameCodec | None = None, *, timeout_seconds: float = 10.0,
                 allowed_channel_ids: set[str] | None = None):
        self.auth = auth
        self.socket_factory = socket_factory
        self.codec = codec or UnconfiguredMaxCodec()
        self.timeout_seconds = timeout_seconds
        self.allowed_channel_ids = allowed_channel_ids
        self.state = MaxSessionState.DISCONNECTED
        self._socket: WebSocketLike | None = None
        self._reader_task: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future[RpcEnvelope]] = {}
        self._request_seq = 0
        self._known_pending: dict[tuple[str, str], PendingJoinRequest] = {}
        self._approve_inflight: set[tuple[str, str]] = set()
        self._approve_channel_inflight: set[str] = set()
        self._pending_read_lock = asyncio.Lock()
        self._waiter_predicates: dict[str, Callable[[RpcEnvelope], bool]] = {}
        self.last_pushes: list[RpcEnvelope] = []

    @classmethod
    def from_auth_state(cls, auth_state_path: str,
                        socket_factory: SocketFactory,
                        codec: FrameCodec, **kwargs: Any) -> "MaxWebSession":
        """Build a session from persisted headers without browser access."""
        return cls(PersistedMaxAuth(MaxAuthState.load(auth_state_path)),
                   socket_factory, codec, **kwargs)

    def get_session_state(self) -> MaxSessionState:
        return self.state

    def _new_request_id(self) -> str:
        self._request_seq += 1
        return f"maxibot-{self._request_seq}"

    async def connect(self) -> None:
        if self.auth is None or self.socket_factory is None:
            self.state = MaxSessionState.AUTH_REQUIRED
            raise MaxWebAuthRequired("MAX Web session is not configured")
        self.state = MaxSessionState.CONNECTING
        try:
            headers = await self.auth.get_auth_headers()
            if not headers:
                raise MaxWebAuthRequired("MAX auth provider returned no credentials")
            self._socket = await self.socket_factory(headers)
            self._reader_task = asyncio.create_task(self._reader_loop())
            await self.authenticate()
        except MaxWebAuthRequired:
            self.state = MaxSessionState.AUTH_REQUIRED
            raise
        except MaxWebTimeout:
            self.state = MaxSessionState.AUTH_REQUIRED
            raise
        except Exception:
            self.state = MaxSessionState.ERROR
            raise

    async def authenticate(self) -> None:
        if self._socket is None:
            raise MaxWebDisconnected("socket is not connected")
        self.state = MaxSessionState.AUTHENTICATING
        request_id = self._new_request_id()
        waiter = self._reserve_waiter(request_id)
        await self._socket.send(self.codec.encode_auth(request_id=request_id))
        try:
            envelope = await self._await_waiter(request_id, waiter)
        except MaxWebTimeout:
            self.state = MaxSessionState.AUTH_REQUIRED
            raise
        if envelope.push or envelope.kind not in {"auth", "response"}:
            self.state = MaxSessionState.AUTH_REQUIRED
            raise MaxWebAuthRequired("MAX auth response was not correlated")
        self.state = MaxSessionState.AUTHENTICATED

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        socket, self._socket = self._socket, None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(MaxWebDisconnected("session closed"))
        self._pending.clear()
        self._waiter_predicates.clear()
        self.state = MaxSessionState.DISCONNECTED
        if socket is not None:
            await socket.close()

    async def reconnect(self) -> None:
        """Reconnect and re-authenticate without replaying any RPC.

        In particular, an interrupted approve is deliberately not retried: the
        caller must list and re-verify state before issuing another destructive
        request.
        """
        await self.close()
        await self.connect()

    async def _reader_loop(self) -> None:
        assert self._socket is not None
        try:
            while True:
                raw = await self._socket.recv()
                envelope = self.codec.decode(raw)
                future = self._pending.get(envelope.request_id)
                if envelope.push or future is None:
                    self.last_pushes.append(envelope)
                    self.last_pushes = self.last_pushes[-100:]
                    continue
                predicate = self._waiter_predicates.get(envelope.request_id)
                if predicate is not None and not predicate(envelope):
                    self.last_pushes.append(envelope)
                    self.last_pushes = self.last_pushes[-100:]
                    continue
                if not future.done():
                    future.set_result(envelope)
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError) as exc:
            self.state = MaxSessionState.DISCONNECTED
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(MaxWebDisconnected(str(exc)))
        except Exception as exc:
            self.state = MaxSessionState.ERROR
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(MaxWebTransportError(type(exc).__name__))

    async def _wait_for(self, request_id: str) -> RpcEnvelope:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[request_id] = future
        return await self._await_waiter(request_id, future)

    def _reserve_waiter(self, request_id: str) -> asyncio.Future[RpcEnvelope]:
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        return future

    def _reserve_structural_waiter(self, request_id: str,
                                   predicate: Callable[[RpcEnvelope], bool]) -> asyncio.Future[RpcEnvelope]:
        future = self._reserve_waiter(request_id)
        self._waiter_predicates[request_id] = predicate
        return future

    async def _await_waiter(self, request_id: str, future: asyncio.Future[RpcEnvelope]) -> RpcEnvelope:
        try:
            return await asyncio.wait_for(future, timeout=self.timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise MaxWebTimeout(f"MAX RPC timeout request_id={request_id}") from exc
        finally:
            self._pending.pop(request_id, None)
            self._waiter_predicates.pop(request_id, None)

    async def send_rpc(self, opcode: int, payload: dict[str, Any]) -> RpcEnvelope:
        if self.state != MaxSessionState.AUTHENTICATED or self._socket is None:
            raise MaxWebAuthRequired("MAX session is not authenticated")
        request_id = self._new_request_id()
        frame = self.codec.encode_rpc(request_id=request_id, opcode=opcode, payload=payload)
        waiter = self._reserve_waiter(request_id)
        await self._socket.send(frame)
        return await self._await_waiter(request_id, waiter)

    async def list_pending_join_requests(self, channel_id: str) -> list[PendingJoinRequest]:
        self._validate_channel(channel_id)
        async with self._pending_read_lock:
            envelope = await self._send_structural_rpc(
                59,
                {"query": "", "type": "JOIN_REQUEST", "chatId": channel_id, "count": 50},
                _looks_like_pending_response,
            )
        rows = parse_pending_response(envelope.payload, channel_id)
        self._known_pending = {(row.channel_id, row.user_id): row for row in rows}
        return rows

    get_pending_join_requests = list_pending_join_requests

    async def approve_join_request(self, channel_id: str, user_id: str) -> ApproveResult:
        self._validate_channel(channel_id)
        if not user_id:
            raise ValueError("user_id is required")
        key = (channel_id, user_id)
        current = self._known_pending.get(key)
        if current is None or current.status != "JOIN_REQUEST":
            raise MaxWebProtocolUnavailable("user is not in the current pending list")
        if key in self._approve_inflight or channel_id in self._approve_channel_inflight:
            raise MaxWebProtocolUnavailable("approve already in flight for user")
        self._approve_inflight.add(key)
        self._approve_channel_inflight.add(channel_id)
        started = time.monotonic()
        try:
            payload = {"chatId": channel_id, "userIds": [user_id], "showHistory": True,
                       "operation": "add", "type": "JOIN_REQUEST"}
            if len(payload["userIds"]) != 1:
                raise ValueError("single-user guard failed")
            envelope = await self.send_rpc(77, payload)
            result = parse_approve_response(envelope.payload)
            return ApproveResult(result.ok, result.already_member, result.raw,
                                 envelope.request_id, int((time.monotonic() - started) * 1000))
        finally:
            self._approve_inflight.discard(key)
            self._approve_channel_inflight.discard(channel_id)

    async def _send_structural_rpc(self, opcode: int, payload: dict[str, Any],
                                   predicate: Callable[[RpcEnvelope], bool]) -> RpcEnvelope:
        if self.state != MaxSessionState.AUTHENTICATED or self._socket is None:
            raise MaxWebAuthRequired("MAX session is not authenticated")
        request_id = self._new_request_id()
        frame = self.codec.encode_rpc(request_id=request_id, opcode=opcode, payload=payload)
        waiter = self._reserve_structural_waiter(request_id, predicate)
        try:
            await self._socket.send(frame)
        except Exception:
            self._pending.pop(request_id, None)
            self._waiter_predicates.pop(request_id, None)
            raise
        return await self._await_waiter(request_id, waiter)

    def _validate_channel(self, channel_id: str) -> None:
        if not channel_id:
            raise ValueError("channel_id is required")
        if self.allowed_channel_ids is not None and channel_id not in self.allowed_channel_ids:
            raise MaxWebProtocolUnavailable("channel is not allowed for auto-approve")


def parse_pending_response(response: dict[str, Any], channel_id: str) -> list[PendingJoinRequest]:
    members = response.get("members", [])
    if not isinstance(members, list):
        raise MaxWebProtocolUnavailable("pending response members is not a list")
    result: list[PendingJoinRequest] = []
    for item in members:
        contact = item.get("contact", {}) if isinstance(item, dict) else {}
        if not isinstance(contact, dict) or contact.get("id") is None:
            continue
        names = contact.get("names")
        name = None
        if isinstance(names, list) and names and isinstance(names[0], dict):
            name = names[0].get("name") or names[0].get("firstName")
        result.append(PendingJoinRequest(channel_id, str(contact["id"]), display_name=name))
    return result


def _looks_like_pending_response(envelope: RpcEnvelope) -> bool:
    if envelope.opcode != 59 or not isinstance(envelope.payload, dict):
        return False
    members = envelope.payload.get("members")
    if not isinstance(members, list):
        return False
    return not members or any(
        isinstance(item, dict)
        and isinstance(item.get("contact"), dict)
        and item["contact"].get("id") is not None
        for item in members
    )


def parse_approve_response(response: dict[str, Any]) -> ApproveResult:
    status = str(response.get("status", "")).upper()
    error = str(response.get("error", "")).upper()
    if error in {"ALREADY_MEMBER", "ALREADY_IN_CHAT", "MEMBER_EXISTS"}:
        return ApproveResult(True, True, response)
    if response.get("ok") is True or status in {"OK", "SUCCESS", "APPROVED"} or response.get("chat") is not None:
        return ApproveResult(True, False, response)
    if error in {"AUTH_REQUIRED", "UNAUTHORIZED", "FORBIDDEN"}:
        raise MaxWebAuthRequired(error)
    if error in {"RATE_LIMIT", "TOO_MANY_REQUESTS"}:
        raise MaxWebRateLimited(error)
    return ApproveResult(False, False, response)
