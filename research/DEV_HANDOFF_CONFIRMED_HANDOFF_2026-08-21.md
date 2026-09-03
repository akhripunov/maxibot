# Confirmed MAX handoff — 2026-08-21

## Result

```text
HANDOFF_ATTEMPTED = implemented
HANDOFF_CONFIRMED = implemented
NEW_VISIT_OR_TOKEN = NO
ATTRIBUTION_CHANGED = NO
YANDEX_CHANGED = NO
PRODUCTION_DEPLOY = PASS
```

## What changed

The traffic funnel now distinguishes two safe diagnostic stages using the
existing `TrafficEvent` table:

- `HANDOFF_ATTEMPTED`: recorded when the landing has issued the existing MAX
  handoff, with only device family and handoff kind;
- `HANDOFF_CONFIRMED`: recorded only when `/mini` is actually requested with
  the existing startapp token and resolves it to an existing visit.

The confirmation is therefore server-observed Mini App entry, not an assumed
success after `window.location`. Events are deduplicated and do not create a
visit, token, identity link, membership, or conversion.

`/admin/traffic-debug` exposes the stage names and presence flags only. Raw
tokens, cookies, init data, and user IDs are not added to diagnostics.

## Files

- `app/main.py`
- `tests/regression/test_confirmed_max_handoff.py`

No migration or env changes.

## Verification

- `python3.11 -m py_compile app/main.py` — PASS.
- Confirmed-handoff static contract tests — PASS.
- Existing diagnostic-stage static tests — PASS.
- Existing mobile handoff contract tests — PASS.
- Existing landing UI static tests — static checks PASS; one test could not
  import the project because the local environment lacks `pydantic`.
- Full pytest — BLOCKED: `pytest` is not installed in the local environment.

## Production

Only `app/main.py` was copied to `135.106.166.46`, with a rollback copy saved
before replacement. Only the API image was rebuilt and the API container was
recreated with `--no-deps`; worker, DB, Redis, env and polling configuration
were not changed.

```text
API_HEALTH_READY = PASS
API_POLLING = false
WORKER_POLLING = true
RUNNING_WORKER_COUNT = 1
PRODUCTION_CONFIG_CHANGED = NO
```

## Interpretation

This patch confirms whether MAX actually reaches `/mini`; it does not claim to
repair an external MAX Web first-open failure by itself. A visit with
`HANDOFF_ATTEMPTED` but without `HANDOFF_CONFIRMED` is a proven break before
Mini App entry and is the safe basis for the next handoff experiment.

```text
STATUS = DEPLOYED_DIAGNOSTICS_ONLY
```
