# Handoff: traffic funnel diagnostic telemetry — 2026-08-21

## Result

```text
DIAGNOSTIC_TELEMETRY_IMPLEMENTED = YES
MIGRATION_REQUIRED = NO
RAW_TOKENS_STORED_BY_NEW_CODE = NO
RAW_COOKIES_STORED_BY_NEW_CODE = NO
RAW_MAX_USER_ID_STORED_BY_NEW_CODE = NO
PRODUCTION_API_DEPLOYED = YES
API_HEALTH_READY = PASS
API_POLLING = false
SOLE_PRIMARY_WORKER = PASS
LEGACY_VPS_CHECK = OUT_OF_SCOPE
```

## What was added

The existing `traffic_events` table now stores idempotent diagnostic stages for
each visit:

```text
LANDING_RENDERED
STARTAPP_READY
REDIRECT_STARTED
MINI_APP_LOADED
RESOLVE_OK
```

Existing identity and membership events remain unchanged. Diagnostic events do
not contain a MAX identity and cannot create attribution or conversions.

Each diagnostic stage is deduplicated by `diagnostic:<stage>:<visit_id>`.
Context is reduced to a low-cardinality device family and referrer hostname;
browser signal payloads are restricted to an allowlist of bounded scalar
fields.

The read-only `/admin/traffic-debug` response now returns presence flags,
status, device family and diagnostic stage names. It no longer returns raw
`yclid`, `startapp_token` or `max_user_id` values.

## Files changed

- `app/main.py`
- `tests/regression/test_traffic_diagnostic_stages.py`

No migration was added because the existing `traffic_events` schema is
sufficient.

## Tests

PASS:

- `python3.11 -m py_compile app/main.py`
- targeted diagnostic contract tests: 5/5
- existing landing and mobile handoff static contracts: relevant assertions PASS

BLOCKED:

- full pytest in the local macOS interpreter: dependencies such as `pydantic`
  are not installed; this is an environment blocker, not a reported production
  test failure.

## Production verification

- API container running and `/health/ready` returned `status=ready`.
- API `MAX_RUN_POLLING=false`.
- Exactly one running production worker; worker `MAX_RUN_POLLING=true`.
- Only API was rebuilt/recreated; worker configuration and attribution/Yandex
  logic were not changed.
- The legacy VPS is out of scope. Production verification is limited to
  `135.106.166.46`.

## Diagnostic interpretation

For the reported incomplete click, the expected evidence is now directly
queryable without exposing secrets:

```text
LANDING_RENDERED + STARTAPP_READY + REDIRECT_STARTED
without MINI_APP_LOADED
  => browser/MAX handoff stopped before Mini App

MINI_APP_LOADED without RESOLVE_OK
  => Mini App loaded but resolve/init-data path failed

RESOLVE_OK without channel_opened
  => channel handoff failed after identity resolution
```

No incomplete visit is eligible for `userDidSubscribe` or Yandex conversion
without the existing exact identity and membership rules.

## Remaining blocker

None for the diagnostic telemetry change in the active production contour.
