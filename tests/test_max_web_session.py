import asyncio
import pytest

from app.integrations.max_web_session import (
    ApproveResult, MaxSessionState, MaxWebAuthRequired, MaxWebProtocolUnavailable,
    MaxWebSession, PendingJoinRequest, RpcEnvelope, parse_pending_response,
)


class Auth:
    async def get_auth_headers(self): return {"X-Test": "present"}


class FakeSocket:
    def __init__(self): self.sent = []; self.queue = asyncio.Queue(); self.closed = False
    async def send(self, data): self.sent.append(data)
    async def recv(self): return await self.queue.get()
    async def close(self): self.closed = True


class Codec:
    def encode_auth(self, *, request_id): return f"auth:{request_id}".encode()
    def encode_rpc(self, *, request_id, opcode, payload):
        assert opcode in {59, 77}
        if opcode == 77:
            assert len(payload["userIds"]) == 1
        return f"rpc:{request_id}:{opcode}".encode()
    def decode(self, frame):
        text = frame.decode()
        _, request_id, kind = text.split(":")
        if kind == "push": return RpcEnvelope(request_id, 0, {}, push=True)
        if kind == "pending": return RpcEnvelope(request_id, 59, {"members": [{"contact": {"id": "u1", "names": [{"name": "A"}]}}]})
        if kind == "approve": return RpcEnvelope(request_id, 77, {"chat": {"id": "c"}})
        return RpcEnvelope(request_id, 0, {}, kind="auth")


@pytest.mark.asyncio
async def test_correlation_ignores_push_and_uses_request_id():
    socket = FakeSocket()
    async def factory(_): return socket
    session = MaxWebSession(Auth(), factory, Codec(), timeout_seconds=0.2)
    connect_task = asyncio.create_task(session.connect())
    await asyncio.sleep(0)
    auth_id = "maxibot-1"
    await socket.queue.put(f"x:{auth_id}:auth".encode())
    await connect_task
    rpc_task = asyncio.create_task(session.send_rpc(59, {"chatId": "c"}))
    await asyncio.sleep(0)
    request_id = "maxibot-2"
    await socket.queue.put(f"x:{request_id}:push".encode())
    await socket.queue.put(f"x:{request_id}:pending".encode())
    result = await rpc_task
    assert result.opcode == 59
    assert len(session.last_pushes) == 1
    await session.close()


@pytest.mark.asyncio
async def test_pending_single_flight_ignores_structurally_unrelated_frame():
    socket = FakeSocket()
    async def factory(_): return socket
    session = MaxWebSession(Auth(), factory, Codec(), timeout_seconds=0.2)
    task = asyncio.create_task(session.connect()); await asyncio.sleep(0); await socket.queue.put(b"x:maxibot-1:auth"); await task
    pending = asyncio.create_task(session.list_pending_join_requests("c")); await asyncio.sleep(0)
    await socket.queue.put(b"x:maxibot-2:auth")
    await asyncio.sleep(0)
    assert not pending.done()
    await socket.queue.put(b"x:maxibot-2:pending")
    rows = await pending
    assert rows[0].to_dict() == {"chat_id": "c", "user_id": "u1", "name": "A", "status": "JOIN_REQUEST"}
    await session.close()


@pytest.mark.asyncio
async def test_pending_reads_are_single_flight():
    socket = FakeSocket()
    async def factory(_): return socket
    session = MaxWebSession(Auth(), factory, Codec(), timeout_seconds=0.2)
    task = asyncio.create_task(session.connect()); await asyncio.sleep(0); await socket.queue.put(b"x:maxibot-1:auth"); await task
    first = asyncio.create_task(session.list_pending_join_requests("c")); await asyncio.sleep(0)
    second = asyncio.create_task(session.list_pending_join_requests("c")); await asyncio.sleep(0)
    assert len([frame for frame in socket.sent if frame.startswith(b"rpc:")]) == 1
    await socket.queue.put(b"x:maxibot-2:pending")
    await first
    await socket.queue.put(b"x:maxibot-3:pending")
    await second
    assert len([frame for frame in socket.sent if frame.startswith(b"rpc:")]) == 2
    await session.close()


@pytest.mark.asyncio
async def test_approve_requires_current_pending_and_single_user():
    socket = FakeSocket()
    async def factory(_): return socket
    session = MaxWebSession(Auth(), factory, Codec(), timeout_seconds=0.2, allowed_channel_ids={"c"})
    task = asyncio.create_task(session.connect()); await asyncio.sleep(0); await socket.queue.put(b"x:maxibot-1:auth"); await task
    with pytest.raises(MaxWebProtocolUnavailable):
        await session.approve_join_request("c", "u1")
    pending_task = asyncio.create_task(session.list_pending_join_requests("c")); await asyncio.sleep(0); await socket.queue.put(b"x:maxibot-2:pending"); await pending_task
    approve_task = asyncio.create_task(session.approve_join_request("c", "u1")); await asyncio.sleep(0); await socket.queue.put(b"x:maxibot-3:approve"); result = await approve_task
    assert result.ok and result.request_id == "maxibot-3"
    with pytest.raises(MaxWebProtocolUnavailable):
        await session.approve_join_request("other", "u1")
    await session.close()


@pytest.mark.asyncio
async def test_timeout_and_auth_required_are_safe():
    socket = FakeSocket()
    async def factory(_): return socket
    session = MaxWebSession(Auth(), factory, Codec(), timeout_seconds=0.01)
    with pytest.raises(Exception):
        await session.connect()
    assert session.state == MaxSessionState.AUTH_REQUIRED
    await session.close()


@pytest.mark.asyncio
async def test_reconnect_authenticates_without_replaying_rpc():
    sockets = []
    async def factory(_):
        socket = FakeSocket()
        sockets.append(socket)
        return socket
    session = MaxWebSession(Auth(), factory, Codec(), timeout_seconds=0.2)
    first = asyncio.create_task(session.connect()); await asyncio.sleep(0); await sockets[0].queue.put(b"x:maxibot-1:auth"); await first
    reconnect = asyncio.create_task(session.reconnect())
    await asyncio.sleep(0)
    await sockets[1].queue.put(b"x:maxibot-2:auth")
    await reconnect
    assert session.state == MaxSessionState.AUTHENTICATED
    assert all(not frame.startswith(b"rpc:") for frame in sockets[1].sent)
    await session.close()


def test_pending_parser_preserves_identity_and_status():
    rows = parse_pending_response({"members": [{"contact": {"id": 123, "names": [{"name": "A"}]}}]}, "c")
    assert rows == [PendingJoinRequest("c", "123", display_name="A")]


def test_default_session_fails_closed_without_codec():
    session = MaxWebSession()
    assert session.state == MaxSessionState.DISCONNECTED
