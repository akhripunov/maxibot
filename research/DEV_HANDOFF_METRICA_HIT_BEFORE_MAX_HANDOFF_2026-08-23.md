# Handoff: Metrika hit before MAX handoff

## Result

- `ROOT_CAUSE_CONFIRMED = YES`
- `METRICA_TAG_PRESENT_IN_LANDING = YES`
- `METRICA_PAGEVIEW_SENT_BEFORE_NAVIGATION = YES`
- `MAX_HANDOFF_PRESERVED = YES`
- `PRODUCTION_DEPLOY = PASS`
- `API_HEALTH = PASS`
- `API_MAX_RUN_POLLING = false`
- `WORKER_MAX_RUN_POLLING = true`
- `MASS_SENDER_CHANGED = NO`
- `ATTRIBUTION_FLOW_CHANGED = NO`

## Root cause

The landing rendered the channel-scoped Metrika tag and immediately executed
`window.location.replace(...)`. The tag loader is asynchronous, so on a fast
MAX handoff the browser could leave before `mc.yandex.ru` registered the
landing visit. The server still retained `yclid`, but Yandex had no matching
Metrika visit; offline uploads were accepted while the UI showed `Не привязан`.

This explains the screenshot without changing the exact-attribution gate.
It does not prove that every historical row failed for the same reason, and it
does not repair historical visits retroactively.

## Change

- Metrika initialization uses `defer: true`.
- The landing sends one explicit `ym(counter, "hit", current_url, ...)`.
- The existing MAX deep link is opened from the callback after the hit is sent.
- A `tag.js` error event preserves the old MAX handoff if Metrika is
  unavailable.
- A single-flight guard prevents duplicate navigation if callback and error
  paths race.
- No new visit, token, Yandex goal, membership rule, or conversion is added.

## Files

- `app/traffic/metrica_tag.py`
- `app/main.py`
- `tests/regression/test_metrica_landing_counter_scope.py`

No migration or new environment variable.

## Verification

- Targeted regression: `12 passed`.
- Python 3.11 `py_compile`: PASS.
- Python 3.9 local test imports still have the known baseline `str | None`
  incompatibility in `app/core/config.py`; this is unrelated to the fix.
- Production API image switched and healthy on `135.106.166.46`.
- Production HTML smoke: HTTP 200; deferred init, explicit hit callback,
  error fallback, MAX handoff, and dedupe guard all present.
- API polling: `false`.
- Sole production worker polling: `true`; worker was not restarted.

## Yandex status boundary

The existing screenshot row remains `Не привязан` because Yandex processes the
uploaded row against the visit data it already has. The fix cannot create a
Metrika visit for that old click. Do not resend old rows or enable bulk sender
based on this deploy. The next proof requires one new real Direct click after
the deploy, followed by the normal exact membership and one controlled upload.

## Final status

- `NEW_TRAFFIC_METRIKA_LINKAGE = READY_FOR_CONTROLLED_TEST`
- `OLD_CONVERSION_REPAIR = NOT_PROVEN`
- `BULK_YANDEX_SENDER = DISABLED`
- `READY_FOR_FULL_PRODUCTION_CONVERSION_CLAIM = NO` until a fresh Direct click
  is observed as a Metrika visit and its one controlled conversion becomes
  linked/accepted.
