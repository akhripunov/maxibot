# Handoff: promotion funnel reliability and reporting — 2026-08-23

## Scope

Campaign: `m365_rsya_msk_conv_test2`; channel: Москва 365.

This change does not alter the landing → MAX → Mini App → channel handoff,
Yandex OAuth/sender, dynamic per-channel Metrika tag, or membership mechanics.
No Yandex conversion was sent and bulk delivery remains disabled.

## Result

| Item | Status | Evidence |
| --- | --- | --- |
| A. Raw requests vs canonical paid entry reporting | PASS | Owner-scoped aggregate endpoint now separates raw requests, unique campaign-matched yclid values, duplicate/revisit requests, and unmatched requests. |
| B. Android `MAX_LINK_ASSIGNED` → no Mini App reliability | BLOCKED | Production evidence localises the loss after browser-side link assignment and before Mini App load; a safe navigation patch is not proven under the locked mobile contract. |
| C. Payloadless member conversion safety | PASS | Only `EXACT_VERIFIED_STARTAPP_IDENTITY` can queue a conversion. `HIGH`/temporal matches are diagnostics only. |
| D. 100% guarantee for shared invite applications | NOT POSSIBLE with current official evidence | Organic/shared-invite applications can produce `user_added` without a verified tracking identity. They stay untracked and are never uploaded. |

## Production evidence (read-only)

Window: `2026-08-21 03:39 UTC` through `2026-08-23 02:59 UTC` (last 48 hours at query time), channel Москва 365, UTM campaign exact match or generated suffix match.

| Metric | Count | Meaning |
| --- | ---: | --- |
| Raw landing requests, all traffic | 767 | Server-side `TrafficVisit` rows; **not clicks**. |
| Campaign-matched raw requests | 245 | Requests carrying the campaign UTM form. |
| Campaign-matched requests with yclid | 242 | Product-side Direct-attributed entries before de-duplication. |
| Canonical unique campaign yclid | 216 | Unique non-empty yclid values; product-side count, not a Direct API/UI reconciliation. |
| Duplicate/revisit requests | 26 | Additional requests reusing an already-counted campaign yclid. |
| Campaign requests without yclid | 3 | UTM alone is not upload-eligible. |

Deepest observed stage per unique campaign yclid:

| Stage | Count |
| --- | ---: |
| `CHANNEL_HANDOFF_STARTED` | 79 |
| `IDENTITY_BOUND` | 23 |
| `MINIAPP_LOADED` | 2 |
| `MAX_LINK_ASSIGNED` | 82 |
| `LANDING_ONLY` | 30 |

The `MAX_LINK_ASSIGNED` group proves only that landing JavaScript reached the
native destination assignment. It does **not** prove that the OS/MAX client
opened the Mini App. In the same window the group is mainly Android (69 of
82); this is a real loss boundary, not an attribution guess.

## Implemented contract

### Product reporting

New read-only, verified-owner endpoint:

```text
GET /admin/traffic/funnel-report?channel_id=...&campaign=...&hours=...
```

It returns aggregate-only fields:

- `direct_attributed_unique_yclid`
- `raw_landing_requests`
- `duplicate_or_revisit_requests`
- `campaign_matched_missing_yclid_requests`
- `other_or_unmatched_requests`
- `deepest_stage_by_unique_yclid`

It never returns yclid, startapp token, MAX user ID, URL, cookies, or UA.
The output explicitly says that product-side unique yclid is not a
Yandex Direct API/UI click reconciliation.

### Conversion safety

`can_send_offline_conversion()` now accepts only `EXACT` with a non-empty
identifier. A temporal `HIGH` candidate is retained for diagnostics but cannot
create an `OfflineConversion` row.

New `traffic_memberships.attribution_reason` is a bounded, non-PII category.
For future official `user_added` events it records one of:

- `EXACT_VERIFIED_STARTAPP_IDENTITY`
- `NO_VERIFIED_STARTAPP_IDENTITY`
- `MULTIPLE_VERIFIED_IDENTITY_VISITS`

This makes payloadless/organic applications visible without assigning a yclid
by time or proximity. Existing rows retain their prior status; no historical
membership or conversion was changed.

## Android reliability finding

The official MAX Mini App contract accepts a startapp payload, and the opaque
token format is URL-safe. Successful Android flows already prove that the
server token and target channel are valid. The failed cohort reaches
`MAX_LINK_ASSIGNED` but has neither Mini App load nor an identity event.

Changing the handoff to add a timer, iframe, extra token, direct-channel
bypass, or unproven HTTPS fallback is prohibited by `AGENTS.md` and would risk
the known-good iPhone route. Therefore no unproven Android navigation change
was deployed.

Required controlled next check for a real Android device from the failed
environment:

1. current MAX app version and installed-app state;
2. open a fresh promotion link with a new yclid;
3. compare `MAX_LINK_ASSIGNED`, `PAGE_HIDDEN`, `MINI_APP_LOADED`,
   `max_identity_linked`, and `MINIAPP_CHANNEL_HANDOFF_STARTED`;
4. only if the issue reproduces with a supported client, propose one
   device-specific change and run the locked iPhone MAX + Safari smokes before
   release.

## Files changed

- `app/traffic/funnel_reporting.py`
- `app/traffic/attribution.py`
- `app/db/models.py`
- `app/bot/menu.py`
- `app/main.py`
- `alembic/versions/0030_membership_attribution_reason.py`
- `tests/test_funnel_reporting.py`
- `tests/test_traffic_attribution.py`
- `tests/test_exact_user_added_attribution.py`
- `tests/regression/test_promotion_reporting_and_exact_gate.py`

## Migration and environment

- Migration: `0030_attr_reason` (adds `traffic_memberships.attribution_reason`
  and its index).
- New environment variables: none.
- Existing Yandex dry-run/sender configuration: unchanged.

## Tests

Local focused suite: `33 passed`.

Candidate inside the production Python 3.12 image:

```text
44 passed
```

It includes traffic attribution, exact identity gate, canonical reporting,
landing contracts, stage telemetry, and the locked mobile handoff contract.

Local Python 3.9 cannot run config-importing tests due the pre-existing project
use of Python 3.10+ union annotations; this is not a release result. Production
candidate testing was performed on Python 3.12.

## Deployment / production checks

- Host: `135.106.166.46` only.
- API image rebuilt and API + worker were recreated after migration.
- `alembic current`: `0030_attr_reason (head)`.
- `/health/ready`: PASS (internal and `https://go.maxibot.pro`).
- API polling: `false`.
- Worker polling: `true`.
- Exactly one worker is running.
- Legacy VPS was not accessed or changed; it is explicitly outside this task's
  deployment scope.
- Rollback copies on VPS:
  - `/opt/maxibot/app/main.py.pre-reliability-20260823`
  - `/opt/maxibot/app/bot/menu.py.pre-reliability-20260823`
  - `/opt/maxibot/app/db/models.py.pre-reliability-20260823`
  - `/opt/maxibot/app/traffic/attribution.py.pre-reliability-20260823`
  - `/opt/maxibot/app/main.py.pre-exact-report-20260823`

## Exact remaining blocker

`ANDROID_CUSTOM_DEEPLINK_TO_MINIAPP = BLOCKED` until a controlled real Android
reproduction identifies whether the supported MAX client receives the assigned
custom scheme. Current server evidence cannot distinguish an OS/client launch
loss from a client-side Mini App startup failure, and the locked iPhone route
must not be changed by inference.

## Architect handoff

No claim is made that every application is advertising-originated. The enforced
product guard is:

```text
official user_added
  + one channel-scoped MaxIdentityLink
  + verified startapp / Mini App identity
  + yclid on the same visit
  => EXACT and conversion eligible

anything else
  => organic/untracked diagnostic state, never Yandex
```
