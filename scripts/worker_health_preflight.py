#!/usr/bin/env python3
"""Read-only Docker collector; concatenate app/worker_health.py before SSH stdin."""
import argparse
import datetime
import subprocess
import sys

if 'read_heartbeat' not in globals():
    from app.worker_health import read_heartbeat, evaluate_runtime
    import json
    import time


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise ValueError('docker_read_failed')
    return result.stdout


def snapshot(container):
    item = json.loads(command(['docker', 'inspect', container]))[0]
    state = item['State']
    env = dict(x.split('=', 1) for x in item['Config']['Env'])
    if any(env.get(flag) != 'false' for flag in ('ADS_AUTOPILOT_ENABLED', 'ADS_WRITE_ACTIONS_ENABLED')):
        raise ValueError('worker_ads_flags_not_false')
    release = item['Config'].get('Labels', {}).get('org.opencontainers.image.revision')
    if not isinstance(release, str) or len(release) != 40:
        raise ValueError('worker_source_unknown')
    return {'id': item['Id'], 'started_at': state['StartedAt'],
            'restart_count': item['RestartCount'], 'status': state['Status'],
            'oom_killed': state['OOMKilled'], 'paused': state['Paused'],
            'dead': state['Dead'], 'release': release, 'pid': 1}


def heartbeat(container, sample, max_age):
    raw = command(['docker', 'exec', container, 'sh', '-c',
                   'cat "${MAX_WORKER_HEARTBEAT_PATH:-/tmp/maxibot-worker-heartbeat.json}"'])
    return read_heartbeat(raw, max_age=max_age, expected_release=sample['release'], expected_pid=1)


def collect(container, interval, startup_grace, max_age, expected_release=None,
            sample_fn=snapshot, heartbeat_fn=heartbeat, sleep_fn=time.sleep, clock_fn=time.monotonic):
    deadline = clock_fn() + startup_grace
    while True:
        before = sample_fn(container)
        if expected_release and before['release'] != expected_release:
            raise ValueError('worker_release_mismatch')
        if before.get('oom_killed') or before.get('paused') or before.get('dead'):
            raise ValueError('worker_state_unhealthy')
        try:
            if before['status'] != 'running':
                raise ValueError('worker_not_running')
            first = heartbeat_fn(container, before, max_age)
            break
        except ValueError as exc:
            # Only initial boot may wait for its first successful long poll.
            started = datetime.datetime.fromisoformat(before['started_at'].replace('Z', '+00:00'))
            age = time.time() - started.timestamp()
            if age >= startup_grace or clock_fn() >= deadline:
                raise ValueError('primary_loop_unverified:' + str(exc)) from exc
            sleep_fn(min(2, max(0, deadline-clock_fn())))
    # An intentional release restart has already happened: start a fresh baseline.
    sleep_fn(interval)
    after = sample_fn(container)
    second = heartbeat_fn(container, after, max_age)
    ok, reason = evaluate_runtime(before=before, after=after, heartbeat=second,
                                  heartbeat_before=first, expected_release=before['release'], legacy_success=False)
    result = {'status': 'PASS' if ok else 'FAIL', 'reason': reason,
              'interval_seconds': interval, 'restart_before': before['restart_count'],
              'restart_after': after['restart_count'], 'restart_delta': after['restart_count']-before['restart_count'],
              'release': before['release'], 'heartbeat_age_seconds': round(second['age_seconds'], 3)}
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--container', default='maxibot-vps-worker-1')
    parser.add_argument('--interval', type=int, default=35)
    parser.add_argument('--startup-grace', type=int, default=90)
    parser.add_argument('--max-age', type=int, default=90)
    parser.add_argument('--expected-release')
    args=parser.parse_args()
    if not (30 <= args.interval <= 60 and 0 <= args.startup_grace <= 120 and 30 <= args.max_age <= 120):
        parser.error('health observation bounds invalid')
    try:
        result=collect(args.container,args.interval,args.startup_grace,args.max_age,args.expected_release)
    except (ValueError, KeyError, TypeError, IndexError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({'status':'FAIL','reason':str(exc)[:180]}))
        return 1
    print(json.dumps(result))
    return 0 if result['status']=='PASS' else 1

if __name__=='__main__':
    raise SystemExit(main())
