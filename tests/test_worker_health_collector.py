from datetime import datetime, timedelta, timezone

import pytest

from scripts.worker_health_preflight import collect
from app.worker_health import read_heartbeat
import json


def snap(*, started=None, restarts=0, oom=False, status="running", release="a" * 40, ident="cid"):
    return {"id": ident, "started_at": started or datetime.now(timezone.utc).isoformat(),
            "restart_count": restarts, "status": status, "oom_killed": oom,
            "paused": False, "dead": False, "release": release, "pid": 1,
            "configured_memory_limit_bytes": 512 * 1024 * 1024,
            "memory_limit_bytes": 512 * 1024 * 1024}


def hb(ts, marker=1, release="a" * 40, pid=1):
    return {"version": 1, "timestamp": ts, "marker": marker, "release": release, "pid": pid, "age_seconds": 1}


def run_collect(snaps, heartbeats, *, expected="a" * 40, startup=90):
    clock = iter([0, 1, 31])
    def heartbeat_fn(_, sample, _max_age):
        return read_heartbeat(json.dumps(heartbeats.pop(0)), now=102, expected_release=sample["release"], expected_pid=1)
    return collect("worker", 30, startup, 90, expected,
                   sample_fn=lambda _: snaps.pop(0), heartbeat_fn=heartbeat_fn,
                   sleep_fn=lambda _: None, clock_fn=lambda: next(clock, 31))


def test_normal_collection_and_empty_poll_heartbeat_advance():
    started = datetime.now(timezone.utc).isoformat()
    result = run_collect([snap(started=started), snap(started=started)], [hb(100, 4), hb(101, 4)])
    assert result["status"] == "PASS" and result["restart_delta"] == 0


def test_restart_before_baseline_is_allowed_only_as_fresh_startup():
    started = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    snaps = [snap(started=started, restarts=2), snap(started=started, restarts=2)]
    assert run_collect(snaps, [hb(100), hb(101)])["status"] == "PASS"


@pytest.mark.parametrize("after", [snap(ident="new"), snap(restarts=1), snap(oom=True), snap(status="paused")])
def test_identity_restart_oom_and_unhealthy_state_fail_closed(after):
    started = datetime.now(timezone.utc).isoformat()
    result = run_collect([snap(started=started), {**after, "started_at": started}], [hb(100), hb(101)])
    assert result["status"] == "FAIL"


@pytest.mark.parametrize("field", ["configured_memory_limit_bytes", "memory_limit_bytes"])
def test_actual_or_configured_memory_limit_mismatch_fails_closed(field):
    started = datetime.now(timezone.utc).isoformat()
    after = snap(started=started)
    after[field] = 256 * 1024 * 1024
    result = run_collect([snap(started=started), after], [hb(100), hb(101)])
    assert result["status"] == "FAIL"
    assert result["reason"] == "worker_memory_limit_mismatch"


def test_missing_runtime_memory_limit_fails_closed():
    started = datetime.now(timezone.utc).isoformat()
    after = snap(started=started)
    after["memory_limit_bytes"] = None
    result = run_collect([snap(started=started), after], [hb(100), hb(101)])
    assert result["status"] == "FAIL"
    assert result["reason"] == "worker_memory_limit_mismatch"


def test_snapshot_reads_configured_and_kernel_cgroup_memory_limit(monkeypatch):
    from scripts import worker_health_preflight
    import json as json_module

    item = {"Id": "c" * 64, "RestartCount": 3,
            "State": {"StartedAt": "t0", "Status": "running", "OOMKilled": False,
                      "Paused": False, "Dead": False},
            "HostConfig": {"Memory": 512 * 1024 * 1024},
            "Config": {"Env": ["ADS_AUTOPILOT_ENABLED=false", "ADS_WRITE_ACTIONS_ENABLED=false"],
                       "Labels": {"org.opencontainers.image.revision": "a" * 40}}}
    outputs = iter([json_module.dumps([item]), str(512 * 1024 * 1024)])
    monkeypatch.setattr(worker_health_preflight, "command", lambda _args: next(outputs))

    result = worker_health_preflight.snapshot("worker")

    assert result["configured_memory_limit_bytes"] == 512 * 1024 * 1024
    assert result["memory_limit_bytes"] == 512 * 1024 * 1024


def test_snapshot_reports_actual_cgroup_limit_when_it_differs_from_configuration(monkeypatch):
    from scripts import worker_health_preflight
    import json as json_module

    item = {"Id": "c" * 64, "RestartCount": 3,
            "State": {"StartedAt": "t0", "Status": "running", "OOMKilled": False,
                      "Paused": False, "Dead": False},
            "HostConfig": {"Memory": 512 * 1024 * 1024},
            "Config": {"Env": ["ADS_AUTOPILOT_ENABLED=false", "ADS_WRITE_ACTIONS_ENABLED=false"],
                       "Labels": {"org.opencontainers.image.revision": "a" * 40}}}
    outputs = iter([json_module.dumps([item]), str(256 * 1024 * 1024)])
    monkeypatch.setattr(worker_health_preflight, "command", lambda _args: next(outputs))

    result = worker_health_preflight.snapshot("worker")

    assert result["configured_memory_limit_bytes"] == 512 * 1024 * 1024
    assert result["memory_limit_bytes"] == 256 * 1024 * 1024


def test_snapshot_rejects_unlimited_or_unreadable_cgroup_limit(monkeypatch):
    from scripts import worker_health_preflight
    import json as json_module

    item = {"Id": "c" * 64, "RestartCount": 3,
            "State": {"StartedAt": "t0", "Status": "running", "OOMKilled": False,
                      "Paused": False, "Dead": False},
            "HostConfig": {"Memory": 512 * 1024 * 1024},
            "Config": {"Env": ["ADS_AUTOPILOT_ENABLED=false", "ADS_WRITE_ACTIONS_ENABLED=false"],
                       "Labels": {"org.opencontainers.image.revision": "a" * 40}}}
    outputs = iter([json_module.dumps([item]), "max"])
    monkeypatch.setattr(worker_health_preflight, "command", lambda _args: next(outputs))

    with pytest.raises(ValueError, match="worker_memory_limit_unreadable"):
        worker_health_preflight.snapshot("worker")


def test_stale_malformed_wrong_release_and_pid_heartbeats_fail_closed():
    for bad in [hb(1), hb(101, release="b" * 40), hb(101, pid=2), {"version": 1}]:
        with pytest.raises(ValueError):
            run_collect([snap(), snap()], [hb(100), bad])


def test_startup_waits_before_taking_control_baseline():
    started=datetime.now(timezone.utc).isoformat()
    samples=iter([snap(started=started,status='restarting',restarts=172),
                  snap(started=started,restarts=173),snap(started=started,restarts=173)])
    beats=iter([hb(100),hb(101)])
    sleeps=[]
    result=collect('worker',35,90,90,'a'*40,
        sample_fn=lambda _:next(samples),heartbeat_fn=lambda *_:next(beats),
        sleep_fn=sleeps.append,clock_fn=lambda:0)
    assert result['status']=='PASS' and result['restart_before']==173
    assert sleeps==[2,35]


def test_stalled_primary_loop_fails_even_with_healthy_process():
    started=datetime.now(timezone.utc).isoformat()
    result=run_collect([snap(started=started),snap(started=started)],[hb(100),hb(100)])
    assert result['status']=='FAIL' and result['reason']=='worker_heartbeat_not_advanced'


def bootstrap_collect(samples, *, failure='heartbeat_absent', legacy=None, expected=None):
    from scripts.worker_health_preflight import LEGACY_BOOTSTRAP_SHA
    def missing(*_):
        if failure:
            raise ValueError(failure)
        return hb(100)
    return collect('worker',35,90,90,expected,
                   sample_fn=lambda _: samples.pop(0), heartbeat_fn=missing,
                   sleep_fn=lambda _: None, bootstrap_from=legacy or LEGACY_BOOTSTRAP_SHA)


def test_bootstrap_only_known_legacy_absent_heartbeat_and_stable_process():
    from scripts.worker_health_preflight import LEGACY_BOOTSTRAP_SHA
    before=snap(release=LEGACY_BOOTSTRAP_SHA)
    result=bootstrap_collect([before, dict(before)])
    assert result['status']=='PASS'
    assert result['primary_loop_verified'] is False
    assert result['reason']=='approved_legacy_bootstrap'


@pytest.mark.parametrize('change', [{'restart_count':1},{'oom_killed':True},{'status':'restarting'},
                                   {'id':'other'},{'release':'b'*40},{'started_at':'other'},
                                   {'paused':True},{'dead':True},
                                   {'configured_memory_limit_bytes':256*1024*1024},
                                   {'memory_limit_bytes':256*1024*1024}])
def test_bootstrap_unstable_worker_is_blocked(change):
    from scripts.worker_health_preflight import LEGACY_BOOTSTRAP_SHA
    before=snap(release=LEGACY_BOOTSTRAP_SHA)
    assert bootstrap_collect([before, {**before,**change}])['status']=='FAIL'


@pytest.mark.parametrize('failure', ['docker_read_failed','heartbeat_stale','malformed',None])
def test_bootstrap_does_not_waive_failed_or_present_heartbeat(failure):
    from scripts.worker_health_preflight import LEGACY_BOOTSTRAP_SHA
    with pytest.raises(ValueError):
        bootstrap_collect([snap(release=LEGACY_BOOTSTRAP_SHA)],failure=failure)


def test_bootstrap_cannot_be_used_for_other_sha_or_postdeploy():
    with pytest.raises(ValueError,match='bootstrap_legacy_release_mismatch'):
        bootstrap_collect([snap()])
    with pytest.raises(ValueError,match='bootstrap_scope_invalid'):
        bootstrap_collect([],legacy='b'*40)
    with pytest.raises(ValueError,match='bootstrap_scope_invalid'):
        bootstrap_collect([],expected='a'*40)
