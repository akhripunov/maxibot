# MAX auto-approve transport readiness

Date: 2026-08-13

## Verdict

```text
MAX_WEB_SESSION_TRANSPORT = PARTIAL
AUTO_APPROVE_INTEGRATION = PARTIAL
FINAL_VERDICT = NOT_PRODUCTION_READY
```

The application-facing transport contract is implemented and fail-closed, but
the undocumented MAX binary codec is still an injected dependency. The real
dynamic binary encoder/decoder and live reconnect integration are not proven
from the available evidence, so this must not be enabled in production.

## Implemented

- Dynamic application request IDs (`maxibot-N`) and a pending-future map.
- Correlation by request ID; unsolicited push frames and unknown request IDs
  are retained as pushes and never complete an RPC waiter.
- Explicit session states: disconnected, connecting, authenticating,
  authenticated, auth-required, rate-limited, and error.
- `connect()`, `authenticate()`, `reconnect()`, `list_pending_join_requests()`
  and guarded `approve_join_request()` interfaces.
- Opcode 59 and 77 payloads are built from current method arguments; the
  production module does not read captured frames.
- Strict channel allowlist and exactly-one-user guard before opcode 77.
- Approve has no automatic destructive retry. Reconnect only re-authenticates;
  callers must list and re-verify pending/member state before another approve.
- Existing `MaxAutoApproveWorker` now supports an explicit channel allowlist,
  duplicate terminal-result suppression, and an optional membership verifier.
- Configuration examples preserve all safety defaults as disabled/dry-run.

## Important blocker

`UnconfiguredMaxCodec` intentionally refuses to send guessed or captured MAX
binary frames. The research probe under `research/max_ws_reproduction/` still
contains private captured fixtures for prior evidence, but it is not the
production transport and is not used by `app/integrations/max_web_session.py`.
Until a separately reviewed, dynamically proven codec is injected, the
transport verdict remains `PARTIAL`.

## Tests

Command:

```text
/private/tmp/max-auth-probe-venv/bin/python -m pytest -q tests/test_max_web_session.py tests/test_auto_approve.py
```

Result: `15 passed`.

The full repository command (`pytest -q`) was also attempted. Collection is
blocked by missing pre-existing environment dependencies (`pydantic`, `httpx`,
and `sqlalchemy`) in the temporary test environment; this is unrelated to the
changed transport tests. The changed modules pass `py_compile`.

Covered by fake transport tests: request correlation, push interleaving,
timeout/auth-required, reconnect without RPC replay, pending identity parsing,
single-user guard, fail-closed default, duplicate poll suppression, channel
allowlist, membership verification, failed response handling, and network/auth
failure paths.

No real-MAX integration test was run and no new destructive approve was sent.

## Changed files

- `app/integrations/max_web_session.py`
- `app/traffic/auto_approve.py`
- `app/core/config.py`
- `.env.example`
- `tests/test_max_web_session.py`
- `tests/test_auto_approve.py`
- this report

No Attribution Engine code, database models, migrations, or funnel code was
changed.

## Security and flags

The production module does not log or embed tokens, cookies, raw auth frames,
or captured payloads. Private capture fixtures remain under the existing
ignored `research/max_ws_reproduction/private/` directory. This workspace has
no `.git` directory, so a repository-level `git status`/diff check could not be
performed here; no private capture file was edited or added to the changed
application/test files.

The following defaults remain off:

```text
MAX_AUTO_APPROVE_ENABLED=false
MAX_PENDING_POLL_ENABLED=false
YANDEX_CONVERSION_DRY_RUN=true
```
