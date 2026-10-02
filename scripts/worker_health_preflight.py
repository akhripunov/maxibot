#!/usr/bin/env python3
"""Read-only worker health evaluator.

The release runner may stream this file over SSH (``python3 -``) so no helper
is installed on the VPS. Input is one JSON object containing ``before``,
``after`` and optional ``heartbeat``/``legacy_success`` fields.
"""

from __future__ import annotations

import argparse
import json
import sys

from app.worker_health import evaluate_runtime, read_heartbeat


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-release")
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        heartbeat = payload.get("heartbeat")
        heartbeat_before = payload.get("heartbeat_before")
        if isinstance(heartbeat, str):
            heartbeat = read_heartbeat(heartbeat, now=payload.get("now"), expected_release=args.expected_release,
                                       expected_pid=payload.get("after", {}).get("pid"))
        if isinstance(heartbeat_before, str):
            heartbeat_before = read_heartbeat(heartbeat_before, now=payload.get("now"), expected_release=args.expected_release,
                                              expected_pid=payload.get("before", {}).get("pid"))
        ok, reason = evaluate_runtime(
            before=payload["before"], after=payload["after"], heartbeat=heartbeat,
            legacy_success=False,
            expected_release=args.expected_release,
            heartbeat_before=heartbeat_before,
        )
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAIL", "reason": f"invalid_input:{exc}"}))
        return 2
    print(json.dumps({"status": "PASS" if ok else "FAIL", "reason": reason}, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
