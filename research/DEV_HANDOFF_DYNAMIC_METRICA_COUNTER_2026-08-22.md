# Handoff: dynamic per-channel Yandex Metrika tag

## Result

- `DYNAMIC_COUNTER_PER_CHANNEL = PASS`
- `LANDING_TAG_FAIL_CLOSED = PASS`
- `MASS_SENDER_ENABLED = NO`
- `OLD_CONVERSIONS_MODIFIED = NO`
- `PRODUCTION_API_DEPLOY = PASS`
- `PRODUCTION_HEALTH = PASS`
- `API_MAX_RUN_POLLING = false`
- `WORKER_MAX_RUN_POLLING = true`
- `LEGACY_VPS_CHECK = BLOCKED_SSH_BANNER_TIMEOUT`

## What changed

Traffic landing now renders a Yandex Metrika tag only from the `yandex_counter_id` belonging to the resolved `TrafficProfile`, and only when that profile has Yandex enabled. Counter IDs are accepted only as positive numeric values. Missing, blank, non-numeric, or non-positive values produce no tag; no global counter is used as a fallback.

The visit, yclid/UTM, startapp, MAX identity, attribution, membership, and sender logic were not changed.

## Files

- `app/traffic/metrica_tag.py` — validation and safe tag renderer.
- `app/main.py` — inserts the channel-scoped tag into traffic landing HTML.
- `tests/regression/test_metrica_landing_counter_scope.py` — isolation and fail-closed contract tests.

No migration and no new environment variable are required.

## Verification

- Dynamic counter tests in production image: `4 passed`.
- Landing UI contract + dynamic counter tests in production image: `15 passed`.
- `py_compile` for changed Python modules: PASS.
- Existing traffic/mobile regression subset: `25 passed, 1 failed`; the failure is a pre-existing unrelated runtime source-contract baseline in `tests/regression/test_landing_fresh_startapp_token.py` (expected `_recent_traffic_routes` assignment absent from current `app/bot/runtime.py`). No unrelated runtime fix was made.
- Production internal health: `{"status":"ready","service":"maxibot","max_mode":"real"}`.
- Public `https://go.maxibot.pro/health/ready`: PASS.
- Production API container healthy; worker remained running and was not recreated.
- Legacy `37.252.10.67`: not verified because SSH repeatedly timed out during banner exchange; no legacy change was made.

## Existing old conversions

Read-only production scan found three old `Москва 365` conversions. All three have yclid present, `attribution_status=EXACT`, database status `sent`, and conversion time within the 21-day window. The channel-scoped counter is `111595093` (numeric value only; no secret or raw yclid is included here).

The current Yandex upload list contains three one-row uploads, all `LINKAGE_FAILURE`. This is distinct from upload acceptance: the files were accepted by the API, but Yandex did not link them to a Metrika visit.

The explicitly requested one-row retry was not executed. The safe execution gate rejected it because the database rows are already marked `sent` and the Yandex upload list does not expose row-level yclid linkage sufficient to prove that a retry would not duplicate an existing upload. No old conversion was resent and no DB state was changed.

## Interpretation / next step

The new tag can create a Metrika hit for future traffic on the correct channel counter. It cannot create a historical Metrika visit retroactively, so old `LINKAGE_FAILURE` rows cannot be assumed recoverable without a fresh qualifying Direct visit. The next safe test is one new real Direct click after this deploy, followed by one new approved EXACT membership and one controlled conversion upload. Bulk sender remains disabled.

## Architect decision needed

Approve or reject a future one-row retry only after adding durable row-to-upload linkage/idempotency and a verified row-level linkage-failure check. Until then, do not resend the three old rows.
