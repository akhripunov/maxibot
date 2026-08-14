# MAX dynamic binary codec investigation

Date: 2026-08-13

## Current verdict

```text
DYNAMIC_BINARY_CODEC = PARTIAL
DYNAMIC_OPCODE_59 = NOT_RUN
DYNAMIC_OPCODE_77 = NOT_RUN
REAL_PENDING_LIST = NOT_RUN
REQUEST_RESPONSE_CORRELATION = PARTIAL
RECONNECT_STATE_RECOVERY = PASS (non-destructive reconnect only)
PENDING_REMOVAL_CONFIRMED = NOT_RUN
MEMBERSHIP_CONFIRMATION = NOT_RUN
TWO_PENDING_ISOLATION = BLOCKED_NO_TEST_USERS
AUTO_APPROVE_WORKER = PARTIAL
PRODUCTION_FLAG_DEFAULT = OFF
FINAL_VERDICT = NOT_PRODUCTION_READY
```

## What was implemented

`app/integrations/max_binary_codec.py` is a secrets-free isolated codec
prototype. It structurally decodes the proven 12-byte envelope, opcode 59/77,
the observed 24-bit request field, MessagePack maps/lists/strings, and MAX
bigint extension values used for chat/user IDs. It dynamically builds the
observed pending and approve payload shapes from runtime values; no captured
binary frame is sent or embedded in the module.

It also has a narrow identity fallback for the observed `c7 be 41` custom
contact wrapper. That wrapper is not fully specified; the fallback extracts
only the already-proven `contact.id` and `names` fields and rejects other
unknown structures.

## Validation

```text
/private/tmp/max-auth-probe-venv/bin/python -m pytest -q \
  tests/test_max_binary_codec.py tests/test_max_web_session.py tests/test_auto_approve.py
20 passed
```

Validated locally:

- known opcode 59 request fixture decode;
- known opcode 77 fixture decode;
- known pending response identity extraction;
- dynamic runtime chat/user/request values;
- encode/decode round-trip for the proven request shape;
- one-user payload shape, malformed input, and unsupported values.

## Blockers to live E2E

The request fixture's numeric header field is not visibly echoed by the known
response fixture: request and response values differ. Existing evidence does
not prove whether correlation is by another envelope field, a server-side
mapping, or a transport sequence. The custom contact wrapper is also only
partially decoded. Sending opcode 59 to a live account before resolving this
would risk mis-correlating a push/response; sending opcode 77 would be a
destructive action without a currently available, explicitly identified test
pending user.

Accordingly, the new codec is not wired into `MaxWebSession` for live traffic,
and no real opcode 59 or 77 was sent in this task. The existing research probe
remains a historical PoC only; its captured approve fixture is not used by the
production module.

Production defaults remain:

```text
MAX_AUTO_APPROVE_ENABLED=false
MAX_PENDING_POLL_ENABLED=false
YANDEX_CONVERSION_DRY_RUN=true
```
