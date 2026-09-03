# Handoff: approved exact conversion preflight — 2026-08-24

## Scope

Read-only production verification after one owner-approved membership in the
`Москва 365` channel. No code, configuration, Yandex settings, or queue sender
were changed.

## Evidence (sanitized)

- Official production worker received one `user_added` event.
- The resulting `TrafficMembership` is `EXACT` with reason
  `EXACT_VERIFIED_STARTAPP_IDENTITY`.
- The membership resolves to one visit with all required evidence present:
  `yclid`, one-time `startapp`, and matching verified MAX identity.
- Exactly one offline-conversion queue row exists for this membership.
- The queued goal is `userDidSubscribe`; the identifier is present but not
  disclosed.
- Queue state is `dry_run`; nothing was sent to Yandex and bulk sending remains
  disabled.

## Controlled upload result

Owner explicitly authorized exactly one send after the preflight. The existing
controlled sender selected the sole fresh `EXACT` dry-run entry and sent one
CSV row to the channel-scoped counter.

- Yandex upload accepted: `YES`.
- Upload ID: `1186624075`.
- Initial provider status: `UPLOADED` (accepted for asynchronous processing;
  this is not yet proof of visit linkage).
- Local queue row: `sent`, `attempts=1`.
- Bulk sender: still disabled (`YANDEX_CONVERSION_DRY_RUN=true`).

## Status

| Check | Status |
|---|---|
| Official `user_added` received | PASS |
| Membership → exact advertising visit | PASS |
| Correct goal queued | PASS |
| Duplicate queue entry | PASS (none) |
| One controlled Yandex upload | PASS — accepted, initial status `UPLOADED` |
| Metrica visit linkage | PASS — UI shows `Привязан` |

## Files / migrations / env

- Files changed: this handoff only.
- Migrations: none.
- Environment changes: none.

## Production state

- Active host: `135.106.166.46`.
- Production worker processed the official event.
- No Yandex upload was performed in this verification.

## Final provider verification

The owner-facing Yandex Metrica **Офлайн-конверсии** report was checked after
processing. The new row for the controlled upload shows:

- target `userDidSubscribe`;
- visit binding `Привязан`;
- one conversion under upload `1186624075`.

No raw yclid, token, or MAX user identifier is recorded in this handoff.
