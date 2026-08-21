# Handoff: MAX Mini App deep-link fix — 2026-08-21

## Итог

```text
ROOT_CAUSE = mobile-like requests selected https://max.ru/<bot>?startapp=...;
             MAX Web could degrade this route to a plain /start event, so the
             registered Mini App was never opened.
FIX = superseded by the real-device correction documented below.
ATTRIBUTION_LOGIC_CHANGED = NO
YANDEX_LOGIC_CHANGED = NO
```

## Что изменено

- `app/main.py`: automatic traffic handoff now always uses
  `build_web_miniapp_deep_link()` and the same one-time `startapp_token`.
- `tests/test_landing_ui_contract.py`: regression contract verifies the web
  Mini App route is used and the old device branch is absent.
- `tests/test_traffic_deep_link.py`: regression contract updated for the
  single web Mini App handoff.
- Migration/env changes: none.

No landing CTA, MAX runtime message, identity binding, membership logic or
Yandex sender was changed.

## Verification

Production image targeted suite:

```text
33 passed
```

Production runtime:

```text
API health = ready / max_mode=real
API MAX_RUN_POLLING = false
Worker MAX_RUN_POLLING = true
```

Fresh browser smoke on the active production promotion landing:

```text
landing = 200
generated target = web.max.ru/<bot>?startapp=<opaque token>
old max.ru profile target = absent
/mini = 200
/api/traffic/resolve = 200
final browser state = target channel Москва 365
```

Raw token, cookies and MAX user identifiers are intentionally omitted.

The requested slug `FZ3kpwshUOqIvA` is not present in the current production
database and returns 404 on the deployed host. The fresh smoke therefore used
the active production promotion landing that resolves to the same Москва 365
traffic profile.

## VPS status

- Production `135.106.166.46`: deploy and health check PASS.
- API/worker single-poller flags: PASS.
- Legacy `37.252.10.67`: BLOCKED for verification because SSH banner exchange
  timed out; no change was made there.

## Final status

```text
DEEPLINK_FIX = PASS
MINI_APP_RUNTIME = PASS
RESOLVE_IDENTITY_CHANNEL_PATH = PASS
PRODUCTION_CODE_CHANGED = YES (minimal handoff only)
PRODUCTION_CONFIG_CHANGED = NO
YANDEX_SEND = DISABLED / NOT TOUCHED
LEGACY_VPS_RECHECK = BLOCKED (SSH banner timeout)
```

## Spinner fallback follow-up

Production report: a MAX Web Mini App could remain on its spinner after a
successful `/api/traffic/resolve` when `WebApp.openMaxLink()` silently failed
to switch the containing MAX view.

Minimal fix in `miniapp/traffic.html`:

- keep `WebApp.openMaxLink(target_url)` as the primary official path;
- after 900 ms, only while the Mini App document remains visible, perform one
  automatic `_blank` anchor handoff to the same validated channel URL;
- do not create a new visit, token or attribution event;
- successful top-level navigation destroys the Mini App before the fallback
  timer fires.

Verification after deploy:

```text
TARGETED_TESTS = 33 passed
API_HEALTH = PASS
API_POLLING = false
WORKER_POLLING = true
BROWSER_SMOKE = landing -> /mini 200 -> /api/traffic/resolve 200 -> Москва 365
SPINNER_VISIBLE_AFTER_3.5S = NO
```

## iPhone regression correction (supersedes the fallback above)

Real iPhone evidence showed that the 900 ms `_blank` anchor fallback did not
perform a native channel handoff. It opened the public `max.ru/join` card
inside the MAX WebView, leaving a second `Перейти в канал` action. That
fallback has therefore been removed from production.

The corrected contract in `miniapp/traffic.html` is:

- Android retains its existing direct `location.replace(target_url)` path;
- every MAX Mini App context exposing the official `WebApp.openMaxLink`
  bridge, including iPhone, uses that bridge and returns immediately;
- an anchor is retained only as a last-resort non-MAX-browser fallback when
  the bridge is absent;
- no timer performs a second channel navigation.

Regression assertions in `tests/test_traffic_deep_link.py` require the bridge
path and prohibit the removed channel fallback timer.

Production verification after the correction:

```text
TARGETED_TESTS = 33 passed
API_HEALTH = PASS
API_POLLING = false
WORKER_POLLING = true
SOLE_PRODUCTION_WORKER = PASS
BROWSER_SMOKE = landing -> /mini 200 -> /api/traffic/resolve 200 -> Москва 365
IPHONE_NATIVE_CHANNEL_HANDOFF = BLOCKED pending one fresh owner-device retest
```

No migration or environment-variable change was required. Attribution,
membership and Yandex code/config were not changed.

## Safe iPhone UX refinement

Frame-by-frame review of the owner iPhone recording proved the functional
flow completed without an extra tap, but the Mini App's own spinner was
visible briefly before the channel opened. The MAX system splash and the
short bot-chat background transition are client-controlled.

The application-owned bridge screen is now visually silent for the first
1600 ms. `/api/traffic/resolve` still starts immediately and the official
`WebApp.openMaxLink` handoff is unchanged. If the handoff is genuinely slow,
the existing status appears after 1600 ms and its manual fallback remains
available after 3200 ms. There is no additional navigation timer.

```text
ATTRIBUTION_FLOW_CHANGED = NO
STARTAPP_TOKEN_CHANGED = NO
CHANNEL_HANDOFF_CHANGED = NO
TARGETED_TESTS = 33 passed
EXTERNAL_MINI_HTML = updated / PASS
BROWSER_SMOKE_FINAL_TARGET = Москва 365 / PASS
API_HEALTH = PASS
API_POLLING = false
WORKER_POLLING = true
IPHONE_VISUAL_RETEST = BLOCKED pending fresh owner recording
```

## Real Safari correction (supersedes the all-device web.max route)

A fresh owner recording started outside MAX proved that an iPhone Safari
request redirected to `https://web.max.ru/<bot>?startapp=...` does not hand
off to the installed native client. Safari remained on the MAX Web setup/help
page. This is a functional Direct-funnel failure, not a cosmetic issue.

`app/main.py` now selects the official Mini App URL by device while preserving
the exact same one-time token:

```text
iPhone/iPad/Android/mobile -> https://max.ru/<bot>?startapp=<same-token>
desktop browser           -> https://web.max.ru/<bot>?startapp=<same-token>
```

This does not use a custom scheme and does not alter visit creation, initData
validation, identity binding, channel resolution, membership or Yandex.

```text
REAL_SAFARI_PRE_FIX = FAIL (stopped on web.max.ru setup page)
MASKED_IPHONE_TARGET_AFTER_FIX = https://max.ru/<bot>?startapp=MASKED
MASKED_DESKTOP_TARGET_AFTER_FIX = https://web.max.ru/<bot>?startapp=MASKED
TARGETED_TESTS = 33 passed
API_HEALTH = PASS
API_POLLING = false
WORKER_POLLING = true
REAL_SAFARI_POST_FIX = BLOCKED pending one fresh owner-device retest
```

## iOS automatic-launch correction

The next real Safari recording proved that an automatic navigation to the
official HTTPS `max.ru/<bot>?startapp=...` URL also remains in Safari and
renders the public bot profile. On iOS, that Universal Link did not hand off
to the installed app without a user gesture.

The mobile automatic target now matches the already captured competitor
runtime exactly:

```text
mobile  -> max://max.ru/<bot>?startapp=<same-token>
desktop -> https://web.max.ru/<bot>?startapp=<same-token>
```

There is one top-level navigation attempt, no iframe, no retry timer, no new
visit and no token replacement. Attribution and downstream code remain
unchanged.

```text
REAL_SAFARI_HTTPS_UNIVERSAL_LINK = FAIL (public bot profile)
MASKED_IPHONE_TARGET_AFTER_FIX = max://max.ru/<bot>?startapp=MASKED
TARGETED_TESTS = 33 passed
API = healthy
API_POLLING = false
WORKER_POLLING = true
REAL_IPHONE_CUSTOM_SCHEME = BLOCKED pending fresh owner-device retest
```

## iPhone channel handoff regression fix — 14:02/14:03 owner recordings

Two fresh owner recordings isolated the failure after the landing handoff:

- both the MAX in-app-browser entry and external Safari entry reached the
  registered Mini App;
- `/mini` and `POST /api/traffic/resolve` returned 200 for the matching
  production visits;
- the Mini App remained on `Открываем канал…`, so visit creation, startapp,
  initData verification and target resolution were not the failing stages;
- the regression was the iPhone branch calling `WebApp.openMaxLink()` for a
  channel invite. The call returned without switching to the channel.

Minimal correction: restore the previously real-device-proven iPhone anchor
handoff (`https://max.ru/join/...`, programmatic `_blank` click) while keeping
Android `location.replace` and the MAX bridge for non-iPhone contexts. No
landing, token, attribution, membership or Yandex logic changed.

```text
FILES_CHANGED = miniapp/traffic.html, tests/test_traffic_deep_link.py
MIGRATIONS = NONE
ENV_CHANGES = NONE
TARGETED_PRODUCTION_TESTS = 33 PASS
API_CONTAINER_HEALTH = healthy
API_POLLING = false
WORKER_POLLING = true
LEGACY_VPS_SINGLE_POLLER_CHECK = BLOCKED (SSH banner timeout)
MAX_IN_APP_ENTRY_TO_MINIAPP = PASS (real recording)
SAFARI_ENTRY_TO_MINIAPP = PASS (real recording)
MINIAPP_TO_CHANNEL_POST_FIX = BLOCKED pending fresh owner iPhone retest
YANDEX_CHANGED = NO
ATTRIBUTION_CHANGED = NO
ROLLBACK = /opt/maxibot/miniapp/traffic.html.rollback-20260821-1409
```

### Fresh owner iPhone acceptance — 14:14/14:15

The owner supplied two fresh real-device recordings after the regression fix.

```text
MAX_IN_APP_BROWSER_ENTRY = PASS
EXTERNAL_SAFARI_ENTRY = PASS
IOS_SYSTEM_OPEN_CONFIRMATION = PRESENT for external Safari (OS-controlled)
MINI_APP_OPENED = YES
START_PARAM_RECEIVED = YES
TRAFFIC_RESOLVE = 200
CHANNEL_MOSCOW365_OPENED = YES
EXTRA_APP_CLICK_AFTER_MINIAPP = NO
IPHONE_REAL_E2E = PASS
```

No additional production change was made after this acceptance. Raw startapp
values and MAX identity were not included in the handoff.

## Regression lock after accepted iPhone flow

The accepted implementation is now protected as a release-blocking contract.

```text
NEW_REGRESSION_FILE = tests/regression/test_mobile_max_handoff_contract.py
AUTOMATED_CHECKS = 6
LOCAL_TARGETED_RESULT = 19 PASS
QA_CRITICAL_CASE = TC-TRAFFIC-013
AGENT_ARCHITECTURE_RULE = ADDED
RUNBOOK = research/MAX_MOBILE_HANDOFF_RELEASE_RUNBOOK.md
RUNTIME_CODE_CHANGED = NO
PRODUCTION_DEPLOY_REQUIRED = NO
```

The tests lock the same mobile startapp token, immediate landing handoff,
iPhone anchor branch, Android replace branch, non-Apple bridge branch, atomic
`/api/traffic/resolve`, and absence of a second timer-driven navigation.
Headless automation still cannot certify the iOS system-to-native handoff;
therefore the runbook requires both owner-device smokes after every future
handoff-affecting release.
