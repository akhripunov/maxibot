# MAX single-flight and state-verification hardening

Date: 2026-08-13

## Implementation status

```text
PENDING_SINGLE_FLIGHT = WORKING (mocked)
PENDING_STRUCTURAL_CLASSIFICATION = WORKING (mocked)
APPROVE_SINGLE_FLIGHT_PER_CHAT = WORKING (mocked)
POST_APPROVE_PENDING_VERIFICATION = WORKING (optional worker path)
MEMBERSHIP_VERIFICATION = WORKING (injected verifier)
REAL_AUTO_APPROVE_E2E = BLOCKED_NO_TEST_PENDING
FINAL_VERDICT = NOT_PRODUCTION_READY
```

## Changes

- `MaxWebSession` serializes opcode 59 reads per session and does not treat a
  frame as a pending response unless it has opcode 59 and a structurally valid
  `members`/`contact.id` shape. Unrelated correlated frames are retained as
  pushes and the waiter times out instead of returning a random frame.
- `MaxWebSession` rejects concurrent destructive operations for the same chat,
  while retaining the existing one-user/current-pending/allowlist guards.
- `PendingJoinRequest.to_dict()` exposes normalized `chat_id`, `user_id`,
  `name`, and `status` fields.
- `MaxAutoApproveWorker` now has a per-chat single-flight lock. With
  `verify_pending_after_approve=True`, it performs a fresh pending read after
  opcode 77 and returns `STATE_NOT_CONFIRMED` if the target remains pending.
  An injected membership verifier can then produce the explicit confirmed
  status. No destructive retry is added.

## Tests

```text
/private/tmp/max-auth-probe-venv/bin/python -m pytest -q \
  tests/test_max_binary_codec.py tests/test_max_web_session.py tests/test_auto_approve.py
23 passed
```

Tests cover unrelated-frame rejection, serial pending reads, per-chat worker
single-flight, state verification hooks, one-user guard, codec fixtures and
malformed input.

No real opcode 59 or opcode 77 was sent in this task. No UI approve was used.
There is no currently identified, explicitly authorized pending test user for
a new destructive E2E, so the live E2E remains blocked rather than consuming a
random user.

Production defaults remain disabled:

```text
MAX_AUTO_APPROVE_ENABLED=false
MAX_PENDING_POLL_ENABLED=false
YANDEX_CONVERSION_DRY_RUN=true
```
