# Handoff: Yandex next-conversion preflight — 2026-08-24

## Scope

Read-only production readiness check for the next `Москва 365` Direct
conversion. No Yandex conversion was uploaded, no production code/config was
changed, and the bulk sender remains disabled.

## Three historical failure modes

1. The traffic landing initially had no channel-scoped Metrica tag.
2. Early uploads used the numeric management goal ID as CSV `Target` instead
   of the JavaScript-event identifier `userDidSubscribe`.
3. After the tag was added, the landing could leave for MAX before the async
   tag registered the page hit, so Yandex had the `yclid` in MAXIBOT but no
   matching Metrica session.

## Current production evidence

- Production landing: `https://go.maxibot.pro/80988024303860`.
- Channel-scoped counter present: `111595093`.
- The initial HTML loads `mc.yandex.ru/metrika/tag.js`.
- The tag initializes the channel counter with `defer: true`.
- The landing calls `ym(counter, "hit", window.location.href, callback)` and
  performs the existing MAX handoff from that callback.
- The Metrica load-error branch preserves the MAX handoff.
- The active goal is a JavaScript-event/action goal named
  `userDidSubscribe`, with an exact condition.
- Sender code uses CSV `Target=userDidSubscribe`.
- API is healthy; API polling is `false`; the sole production worker polling
  flag is `true`.

The official Yandex documentation says that `Yclid`, `DateTime`, and the
JavaScript-event goal identifier in `Target` are sufficient for a Direct
offline conversion. `ClientID` is optional (although recommended as an
additional precise identifier). Matching is possible only when Yandex already
has a Metrica session for the supplied identifier and the conversion is within
the 21-day window.

## Existing upload status

The latest historical upload remains `LINKAGE_FAILURE`. The Metrica report
reason is: no session was found for the supplied `Yclid` before the conversion
time. This is an immutable historical result and is not evidence that the
post-fix landing still fails. Earlier numeric-target uploads failed for the
separate missing-goal reason.

## Browser smoke

A production Chrome smoke using an explicit non-Direct test marker reached
MAX Web and loaded `/mini`. It created no eligible conversion and contained no
real `yclid`. The browser-control network stream switched to the MAX target
before retaining the first Metrica request, so this smoke proves handoff
continuity but is not independent proof of Yandex session ingestion.

## Status

- `CHANNEL_SCOPED_METRICA_TAG = PASS`
- `TARGET_USER_DID_SUBSCRIBE = PASS`
- `HIT_BEFORE_MAX_HANDOFF_CONTRACT = PASS`
- `COUNTER_READY = PASS`
- `GOAL_READY = PASS`
- `API_HEALTH = PASS`
- `SINGLE_POLLER = PASS`
- `OLD_UPLOADS_RECOVERED = NO`
- `NEXT_NEW_DIRECT_CONVERSION_PREFLIGHT = PASS`
- `NEXT_NEW_DIRECT_CONVERSION_LINKED_BY_YANDEX = NOT_PROVEN`
- `BULK_SENDER = DISABLED`

## Exact next acceptance

Use one new real Direct click generated after the hit-before-handoff deploy.
Before upload, require the existing exact chain:

`yclid -> TrafficVisit -> same startapp -> verified MAX identity -> official user_added`.

Upload exactly one `userDidSubscribe`, then wait for the Metrica Offline
conversions report to show that it is matched to a session. Only that result is
the final proof that the historical three defects are closed end to end.
