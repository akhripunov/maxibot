from datetime import datetime, timedelta, timezone

import pytest

from scripts.worker_health_preflight import collect
from app.worker_health import read_heartbeat
import json


def snap(*, started=None, restarts=0, oom=False, status="running", release="a" * 40, ident="cid"):
    return {"id": ident, "started_at": started or datetime.now(timezone.utc).isoformat(),
            "restart_count": restarts, "status": status, "oom_killed": oom,
            "paused": False, "dead": False, "release": release, "pid": 1}


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


def test_stale_malformed_wrong_release_and_pid_heartbeats_fail_closed():
    for bad in [hb(1), hb(101, release="b" * 40), hb(101, pid=2), {"version": 1}]:
        with pytest.raises(ValueError):
            run_collect([snap(), snap()], [hb(100), bad])
