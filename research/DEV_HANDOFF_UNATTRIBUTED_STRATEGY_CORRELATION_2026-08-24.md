# Handoff: UNATTRIBUTED memberships vs Direct strategy changes — 2026-08-24

## Scope

Read-only production investigation for channel `Москва 365` and campaign
`m365_rsya_msk_conv_test2`. No database, code, campaign, attribution or Yandex
conversion state was changed.

## Result

| Question | Result |
| --- | --- |
| Does switching Maximum conversions/clicks remove `yclid` or `startapp`? | **NO EVIDENCE / NOT THE CONTRACT** |
| Does the active ad still target the MAXIBOT tracking landing? | **YES** |
| Is the latest UNATTRIBUTED membership one of today's two Direct clicks? | **NO** |
| Is there a separate paid-click reliability loss? | **YES — one Android click stopped after browser handoff and before Mini App load** |
| Was an unproven conversion queued or sent? | **NO** |

## Production evidence (sanitized)

Latest membership:

- official `user_added`: `2026-08-24 07:23:16 MSK`;
- attribution: `UNATTRIBUTED`;
- reason: `NO_VERIFIED_STARTAPP_IDENTITY`;
- channel-scoped identity visits for this MAX user: `0`;
- all identity visits for this MAX user: `0`;
- recorded user-scoped traffic events: only `user_added`;
- conversion queue: none.

Today's Direct-attributed entries happened later:

| Time MSK | Environment | Deepest evidence | Identity |
| --- | --- | --- | --- |
| 07:32:06 | Android 12 / Chrome 148 | Mini App resolve, verified identity and channel handoff | BOUND |
| 07:35:13 | Android 10 / Chrome 151 | `MAX_LINK_ASSIGNED` + `PAGE_HIDDEN`; no Mini App request/update | UNBOUND |

Therefore the 07:23 membership cannot be attributed to either 07:32 or 07:35
click. Approval time is after the unknown request time, but there is no verified
identity for that member anywhere in the retained tracking data, so assigning a
nearby yclid would be fabrication.

The earlier UNATTRIBUTED membership (`2026-08-23 05:31:57 MSK`) has the same
identity evidence shape: zero `MaxIdentityLink` rows and only `user_added` for
that user. Direct traffic existed in the broad time window, but there is no
user-level bridge that connects this member to any of those visits.

## Direct configuration check

The authenticated Direct UI shows campaign `713735649`, group `5790974711`,
active ad `1919096139958490651` targeting:

```text
https://go.maxibot.pro/80988024303860
```

The current campaign strategy shown in the UI is conversion optimization. The
official Direct contract treats the strategy as traffic/bidding optimization;
the ad `Href` is a separate ad field. No evidence was found that changing the
strategy rewrote the destination URL.

## Root-cause boundary

`UNATTRIBUTED` is not caused by the conversion-vs-click bidding mode in the
available evidence. It means the approved MAX user never completed either
verified Mini App initData binding or a payload-bearing official bot start for
this tracking visit.

The 07:35 paid click exposes a real but separate boundary:

```text
landing + yclid
  -> startapp token ready
  -> custom MAX link assigned
  -> browser page hidden
  -> NO Mini App load
  -> NO bot_started payload
  -> NO MaxIdentityLink
```

This is localized to the Android browser/native MAX handoff. One Android
environment succeeded minutes earlier, so it is intermittent/client-specific,
not a server token or target-channel failure.

## Safe fix status

No navigation patch was deployed. `AGENTS.md` locks the real-device-proven
mobile contract and forbids speculative timer, iframe, second token, required
landing click, or direct-channel bypass. Those changes could break the proven
iPhone path and/or destroy exact attribution.

The product already fail-closes correctly:

- verified tracking identity -> `EXACT` -> eligible for controlled Yandex send;
- no verified tracking identity -> `UNATTRIBUTED` -> no conversion queued.

## Remaining blocker

`ANDROID_BROWSER_TO_MINIAPP_RELIABILITY = BLOCKED` pending a controlled
reproduction on the failing class (Android 10 / Chrome 151 with the installed
MAX client), followed by the required iPhone-in-MAX and iPhone-Safari regression
smokes for any proposed handoff change.

## Status

- `STRATEGY_CHANGE_CAUSES_UNATTRIBUTED = NOT_PROVEN / CONTRADICTED BY TIMING`
- `ACTIVE_AD_USES_TRACKING_LANDING = PASS`
- `LATEST_UNATTRIBUTED_IS_TODAYS_DIRECT_CLICK = NO`
- `EXACT_CONVERSION_GATE = PASS`
- `ANDROID_HANDOFF_RELIABILITY = BLOCKED`
- `PRODUCTION_CHANGED = NO`
- `YANDEX_CONVERSION_SENT = NO`
