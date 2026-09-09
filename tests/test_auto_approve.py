import asyncio
import pytest

from app.traffic.auto_approve import (
    AUTH_REQUIRED, APPROVED, FAILED, MaxAutoApproveWorker, PendingRequest,
)


class Provider:
    async def list_pending(self, channel_id):
        return [PendingRequest(channel_id, "user-a"), PendingRequest(channel_id, "user-b")]


class Approver:
    def __init__(self): self.calls = []
    async def approve_one(self, request):
        self.calls.append(request.max_user_id)
        return {"ok": True}


@pytest.mark.asyncio
async def test_worker_disabled_never_calls_approver():
    approver = Approver()
    result = await MaxAutoApproveWorker(Provider(), approver, enabled=False).process_channel("c")
    assert result[0]["status"] == "DISABLED"
    assert approver.calls == []


@pytest.mark.asyncio
async def test_worker_approves_each_pending_user_separately():
    approver = Approver()
    result = await MaxAutoApproveWorker(Provider(), approver, enabled=True).process_channel("c")
    assert [row["status"] for row in result] == [APPROVED, APPROVED]
    assert approver.calls == ["user-a", "user-b"]


@pytest.mark.asyncio
async def test_worker_skips_duplicate_poll_after_terminal_result():
    approver = Approver()
    worker = MaxAutoApproveWorker(Provider(), approver, enabled=True)
    first = await worker.process_channel("c")
    second = await worker.process_channel("c")
    assert [row["status"] for row in first] == [APPROVED, APPROVED]
    assert [row["status"] for row in second] == ["SKIPPED_DUPLICATE", "SKIPPED_DUPLICATE"]
    assert approver.calls == ["user-a", "user-b"]


@pytest.mark.asyncio
async def test_worker_does_not_mark_failed_response_as_approved():
    class BadResponse:
        async def approve_one(self, request): return {"ok": False}
    result = await MaxAutoApproveWorker(Provider(), BadResponse(), enabled=True).process_channel("c")
    assert all(row["status"] == FAILED for row in result)


@pytest.mark.asyncio
async def test_worker_confirms_membership_only_with_verifier():
    class Verifier:
        async def verify(self, request): return request.max_user_id == "user-a"
    approver = Approver()
    worker = MaxAutoApproveWorker(Provider(), approver, enabled=True, membership_verifier=Verifier())
    result = await worker.process_channel("c")
    assert [row["status"] for row in result] == ["CONFIRMED", FAILED]


@pytest.mark.asyncio
async def test_worker_enforces_channel_allowlist():
    result = await MaxAutoApproveWorker(Provider(), Approver(), enabled=True,
                                        allowed_channel_ids={"allowed"}).process_channel("blocked")
    assert result == [{"status": "CHANNEL_NOT_ALLOWED", "channel_id": "blocked"}]


@pytest.mark.asyncio
async def test_worker_enforces_per_channel_single_flight():
    started = asyncio.Event()
    release = asyncio.Event()
    class SlowApprover:
        async def approve_one(self, request):
            started.set(); await release.wait(); return {"ok": True}
    worker = MaxAutoApproveWorker(Provider(), SlowApprover(), enabled=True)
    first = asyncio.create_task(worker.process_channel("c"))
    await started.wait()
    second = await worker.process_channel("c")
    assert second[0]["status"] == "SINGLE_FLIGHT_BUSY"
    release.set(); await first


@pytest.mark.asyncio
async def test_missing_transport_pauses_auth_required():
    result = await MaxAutoApproveWorker(None, None, enabled=True).process_channel("c")
    assert result[0]["status"] == AUTH_REQUIRED


@pytest.mark.asyncio
async def test_permission_error_does_not_retry_forever():
    class Denied:
        async def approve_one(self, request): raise PermissionError("admin session expired")
    result = await MaxAutoApproveWorker(Provider(), Denied(), enabled=True).process_channel("c")
    assert all(row["status"] == AUTH_REQUIRED for row in result)


@pytest.mark.asyncio
async def test_network_error_is_retryable():
    class Flaky:
        async def approve_one(self, request): raise TimeoutError("timeout")
    result = await MaxAutoApproveWorker(Provider(), Flaky(), enabled=True).process_channel("c")
    assert all(row["status"] == FAILED and row["retryable"] for row in result)
