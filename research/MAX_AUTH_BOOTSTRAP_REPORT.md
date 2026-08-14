# MAX auth bootstrap / autonomous read-path

Date: 2026-08-13

```text
CDP_BOOTSTRAP_AVAILABLE = NO
AUTH_BOOTSTRAP = BLOCKED_NO_CDP_ENDPOINT
AUTHORIZED_MAX_SESSION = NOT_RUN
AUTH_STATE_SAVED = NO
AUTH_STATE_PERSISTENCE = IMPLEMENTED_AND_TESTED
AUTH_STATE_RESTART_TEST = NOT_RUN
BROWSER_DEPENDENCY_AFTER_BOOTSTRAP = UNKNOWN
HEADLESS_WS_RECONNECT = NOT_RUN
MAX_BINARY_CODEC_ATTACHED_TO_PRODUCTION = NO
DYNAMIC_OPCODE_59 = NOT_RUN
REAL_PENDING_LIST = NOT_RUN
TEST_USER_FOUND = NO
DYNAMIC_OPCODE_77 = NOT_RUN_INTENTIONALLY
PRODUCTION_FLAGS = OFF
FINAL_VERDICT = NOT_PRODUCTION_READY
```

## Safe implementation state

`MaxAuthState` and `MaxWebSession.from_auth_state(...)` are implemented. The
auth state is a minimal owner-readable JSON file containing operator-supplied
WebSocket headers; it is not created from a browser profile and is not logged.
Invalid or missing state returns sanitized `MAX_AUTH_STATE_INVALID` or
`MAX_AUTH_EXPIRED` categories.

The binary codec has fixture-derived payload encoding/decoding tests, but it is
not attached to live `MaxWebSession`: the auth envelope and response
correlation are not proven dynamically. Connecting it by reusing a captured
AUTH frame would violate the production requirement.

## Blocker

No CDP endpoint or saved non-secret auth-state file was available in the
workspace. Browser cookies, localStorage, sessionStorage, profiles and token
stores were not inspected. The proper next step is operator bootstrap: start
an already-authenticated Chrome/Chromium with remote debugging, provide its
CDP endpoint, and supply only the minimum approved auth material through a
secret-safe mechanism. Manual login/MFA remains operator-only.

No opcode 59 or opcode 77 was sent. No pending user was inspected or accepted.
