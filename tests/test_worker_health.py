import asyncio
from unittest.mock import AsyncMock, Mock

from app.worker_health import evaluate_runtime, read_heartbeat


def snapshot(*, started="t0", restarts=0, status="running", oom=False, release="r1"):
    return {"id": "container", "started_at": started, "restart_count": restarts, "status": status, "oom_killed": oom, "release": release, "pid": 12,
            "configured_memory_limit_bytes": 512 * 1024 * 1024,
            "memory_limit_bytes": 512 * 1024 * 1024}


def test_healthy_heartbeat_and_stable_identity():
    ok, reason = evaluate_runtime(
        before=snapshot(), after=snapshot(),
        heartbeat={"version": 1, "timestamp": 100, "pid": 12},
        legacy_success=False,
        expected_release="r1",
    )
    assert (ok, reason) == (True, "heartbeat")


def test_primary_heartbeat_must_advance():
    current = {"version": 1, "timestamp": 100, "marker": 4, "pid": 12}
    ok, reason = evaluate_runtime(
        before=snapshot(), after=snapshot(), heartbeat=current, heartbeat_before=current,
        legacy_success=False, expected_release="r1",
    )
    assert not ok and reason == "worker_heartbeat_not_advanced"


def test_restart_requires_fresh_baseline_and_stuck_loop_fails():
    ok, _ = evaluate_runtime(
        before=snapshot(started="old", restarts=3), after=snapshot(started="new", restarts=4),
        heartbeat={"version": 1, "timestamp": 101, "pid": 12}, legacy_success=False,
        expected_release="r1",
    )
    assert not ok
    ok, reason = evaluate_runtime(
        before=snapshot(), after=snapshot(), heartbeat=None, legacy_success=False,
    )
    assert not ok and reason == "worker_release_mismatch"


def test_unstable_worker_and_current_oom_fail_closed():
    ok, reason = evaluate_runtime(
        before=snapshot(), after=snapshot(started="changed"),
        heartbeat={"version": 1, "timestamp": 100, "pid": 12}, legacy_success=False, expected_release="r1",
    )
    assert not ok and reason == "worker_identity_changed"
    ok, reason = evaluate_runtime(
        before=snapshot(), after=snapshot(oom=True),
        heartbeat={"version": 1, "timestamp": 100, "pid": 12}, legacy_success=False, expected_release="r1",
    )
    assert not ok and reason == "worker_oom_killed"


def test_worker_memory_limit_must_match_512_mib_at_both_samples():
    healthy = {"version": 1, "timestamp": 101, "pid": 12}
    wrong_before = snapshot()
    wrong_before["configured_memory_limit_bytes"] = 256 * 1024 * 1024
    ok, reason = evaluate_runtime(before=wrong_before, after=snapshot(), heartbeat=healthy,
                                  heartbeat_before={"timestamp": 100}, legacy_success=False,
                                  expected_release="r1")
    assert not ok and reason == "worker_memory_limit_mismatch"

    wrong_actual = snapshot()
    wrong_actual["memory_limit_bytes"] = 0
    ok, reason = evaluate_runtime(before=snapshot(), after=wrong_actual, heartbeat=healthy,
                                  heartbeat_before={"timestamp": 100}, legacy_success=False,
                                  expected_release="r1")
    assert not ok and reason == "worker_memory_limit_mismatch"


def test_expected_worker_memory_limit_passes_with_other_runtime_guards():
    ok, reason = evaluate_runtime(before=snapshot(), after=snapshot(),
                                  heartbeat={"version": 1, "timestamp": 101, "pid": 12},
                                  heartbeat_before={"timestamp": 100}, legacy_success=False,
                                  expected_release="r1")
    assert (ok, reason) == (True, "heartbeat")


def test_legacy_worker_is_blocked_without_primary_heartbeat():
    ok, reason = evaluate_runtime(
        before=snapshot(), after=snapshot(), heartbeat=None, legacy_success=True,
    )
    assert not ok and reason == "worker_release_mismatch"


def test_corrupt_heartbeat_is_rejected():
    for raw in ["{\"version\": 1, \"timestamp\": 1, \"pid\": 4}", "null", "[]", "42"]:
        try:
            read_heartbeat(raw, now=100)
        except ValueError:
            continue
        raise AssertionError("corrupt heartbeat must fail closed")


def test_release_keeps_ads_write_flags_disabled():
    source = open("scripts/release_prod.sh", encoding="utf-8").read()
    assert "ADS_AUTOPILOT_ENABLED=false" in source
    assert "ADS_WRITE_ACTIONS_ENABLED=false" in source


def test_primary_runtime_heartbeat_follows_durable_marker_and_stop(monkeypatch):
    import pytest
    pytest.importorskip("sqlalchemy")
    from app.bot.runtime import MaxBotRuntime
    from app.integrations.max_client import MaxUpdate

    client = AsyncMock()
    runtime = MaxBotRuntime(client, AsyncMock())
    runtime._load_primary_marker = AsyncMock(return_value=7)
    runtime._daily_report_loop = AsyncMock()
    runtime._ads_ai_event_loop = AsyncMock()
    runtime._poll_custom_bots = AsyncMock()
    stored = AsyncMock(side_effect=lambda marker: runtime._stop.set())
    runtime._store_primary_marker = stored
    updates = [MaxUpdate(update_type="message_created", raw={})]
    client.updates = AsyncMock(return_value=(updates, 8))
    runtime.handle = AsyncMock()
    heartbeat = Mock()
    monkeypatch.setattr("app.bot.runtime.write_heartbeat", heartbeat)

    asyncio.run(runtime.run_forever())
    stored.assert_awaited_once_with(8)
    heartbeat.assert_called_once_with(marker=8)
    client.close.assert_not_awaited()


def test_primary_runtime_does_not_heartbeat_when_marker_persistence_fails(monkeypatch):
    import pytest
    pytest.importorskip("sqlalchemy")
    from app.bot.runtime import MaxBotRuntime

    client = AsyncMock()
    runtime = MaxBotRuntime(client, AsyncMock())
    runtime._load_primary_marker = AsyncMock(return_value=7)
    runtime._daily_report_loop = AsyncMock()
    runtime._ads_ai_event_loop = AsyncMock()
    async def persist_failure(marker):
        runtime._stop.set()
        raise RuntimeError("isolated persistence failure")
    runtime._store_primary_marker = AsyncMock(side_effect=persist_failure)
    client.updates = AsyncMock(return_value=([], 8))
    heartbeat = Mock()
    monkeypatch.setattr("app.bot.runtime.write_heartbeat", heartbeat)

    asyncio.run(runtime.run_forever())
    heartbeat.assert_not_called()
