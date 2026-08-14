# MAX Attribution Engine v2 — verdict

## Join request

`JOIN_REQUEST_SIGNAL = NOT_AVAILABLE` in the current integration.

The code accepts update types `join_request_created` and `join_request` in
`app/bot/runtime.py`, and stores them in `traffic_events` and
`max_join_events`. However, there is no MAX parser branch that reconstructs a
join request, no polling query that creates one, and the webhook subscription
requests it only when `MAX_JOIN_REQUEST_SIGNAL=true`. Therefore a
`requested_at` value is used only when a real MAX update supplies a timestamp;
otherwise it is not invented from webhook receipt time.

Current timestamp handling:

- `timestamp` or `created_at` from the raw MAX update is converted to UTC in
  `MaxBotRuntime._traffic_membership`;
- if no usable raw timestamp exists, the repository uses server receipt time
  and the event remains an ordinary signal, not proof of native request time;
- manual approval matching is disabled without a real join-request signal,
  unless the channel is explicitly configured as auto-approve.

Sanitized payload shape currently accepted (only fields actually read by the
parser):

```json
{
  "update_type": "join_request_created",
  "channel_id": "<MAX chat id>",
  "user": {"user_id": "<MAX user id>"},
  "timestamp": 1760000000000
}
```

This is an accepted parser shape, not evidence that MAX is currently sending
this event in production. No `invite_id`, `yclid` or source field is assumed.

## MAX open signal

`MAX_OPEN_CONFIRMED` is not available. MAX does not currently provide a
documented callback, return URL, postMessage or server confirmation used by
this project.

v2 adds only a probabilistic browser signal:

`MAX_OPEN_LIKELY = page_hidden_after_redirect`

It is generated from `visibilitychange`/`pagehide` telemetry sent with
`navigator.sendBeacon()` by an optional, short-lived auto-redirect HTML
variant. `window.blur` is stored as telemetry but does not create a likely
open by itself. The variant is disabled by default:

```env
TRAFFIC_BROWSER_OPEN_TELEMETRY_ENABLED=false
MAX_OPEN_CONFIRMED_ENABLED=false
```

## Attribution v2

Open signal adds a bounded score bonus and explanatory reasons. It never turns
multiple candidates into `HIGH`; the strongest result with alternatives is at
most `MEDIUM`. A single candidate with a likely-open signal is `HIGH`, but it
remains algorithmic and is not `EXACT`.

Browser events use the existing `traffic_events` table. No additional table
was introduced.
