# Direct promotion funnel audit — Москва 365

Read-only audit, production and Yandex were not changed. Raw identifiers are intentionally omitted.

## Scope

| Parameter | Value |
|---|---|
| Channel | Москва 365 |
| Campaign match | `m365_rsya_msk_conv_test2` and generated suffixes |
| Period | 2026-08-13 10:49:57–2026-08-23 06:47:19 MSK (UTC +3) |
| Source filter | `yclid` present and campaign matched; categorized as Yandex Direct |
| Canonical denominator | 221 unique `yclid` |
| Raw TrafficVisit requests | 247 |
| Repeats/revisits | 26 (10.5% of raw requests) |
| Campaign requests without `yclid` | 3 (not counted as canonical paid clicks) |

## Funnel table

Percentages below use 221 unique `yclid` as the canonical Direct-click denominator. “Loss” is the difference from the preceding persisted stage; it is not necessarily a user failure when the signal is client-side or the next action is manual.

| Stage | Count | Of Direct clicks | Loss from previous stage | Evidence/status |
|---|---:|---:|---:|---|
| Direct click / unique `yclid` | 221 | 100.0% | — | PROVEN in `TrafficVisit` |
| `STARTAPP` token present | 221 | 100.0% | 0 | PROVEN in visit record |
| `MAX_LINK_ASSIGNED` | 168 | 76.0% | 53 / 24.0% | persisted landing diagnostic |
| `PAGE_HIDDEN` | 143 | 64.7% | 25 / 14.9% | browser visibility signal |
| `HANDOFF_CONFIRMED` | 107 | 48.4% | 36 / 25.2% | MAX handoff confirmation |
| `MINIAPP_LOADED` | 107 | 48.4% | 0 | `/mini` telemetry |
| `RESOLVE_OK` | 105 | 47.5% | 2 / 1.9% | Mini App resolve |
| verified identity | 105 | 47.5% | 0 | `max_identity_linked` |
| channel-open handoff signal | 105 | 47.5% | 0 | `channel_open_clicked` or `MINIAPP_CHANNEL_HANDOFF_STARTED` |
| approved `user_added` | 4 | 1.8% | not comparable | MAX approval/member event |
| `EXACT` membership | 4 | 1.8% | not comparable | exact identity link + approved membership |

### Last persisted stage for clicks that did not reach channel-open handoff

| Last stage | Unique clicks | Share |
|---|---:|---:|
| `STARTAPP` only | 30 | 13.6% |
| `MAX_LINK_ASSIGNED` | 18 | 8.1% |
| `PAGE_HIDDEN` | 66 | 29.9% |
| `MINIAPP_LOADED` | 2 | 0.9% |
| Total without channel-open handoff signal | 116 | 52.5% |

The 116 are diagnostic losses, not automatically failed subscriptions: the biggest group is an interrupted/undetermined handoff after `PAGE_HIDDEN`; the `STARTAPP` and `MAX_LINK_ASSIGNED` groups indicate no proof that the Mini App loaded.

## Device breakdown

Unique `yclid` is assigned to the first observed device for reporting; raw requests include repeats.

| Device family | Raw requests | Unique `yclid` | Channel-open handoff | Share of device clicks | Not reaching handoff |
|---|---:|---:|---:|---:|---:|
| Android | 209 | 188 | 99 | 52.7% | 89 |
| iPhone/iPad | 9 | 9 | 0 | 0.0% | 9 |
| Other/unknown | 29 | 24 | 6 | 25.0% | 18 |
| Total | 247 | 221 | 105 | 47.5% | 116 |

Android last stages: 17 `STARTAPP`, 15 `MAX_LINK_ASSIGNED`, 55 `PAGE_HIDDEN`, 2 `MINIAPP_LOADED`, 99 channel-open handoff. iPhone/iPad has 9 `STARTAPP` records but no persisted channel-open signal in this scope; this is not proof that the channel was not visually opened, only that the server diagnostic was not recorded.

## Membership / attribution

| Metric | Count | Interpretation |
|---|---:|---|
| Approved `user_added` associated with Direct-linked identities | 4 | PROVEN |
| EXACT memberships | 4 | PROVEN and eligible for controlled conversion |
| Unattributed memberships among Direct-linked visit matches | 0 | no such row in this scope |
| Direct clicks without EXACT membership | 217 | not a loss verdict: most may not have reached/been approved yet |

## 128 clicks vs DB

The Direct UI value `128` must not be compared directly with `247` raw requests or `221` unique `yclid` until the exact same MSK/UTC period and campaign filter are selected. Current DB scope is 10 days and includes 26 revisits plus generated campaign suffixes. Therefore:

- `247` is raw requests, not clicks;
- `221` is the canonical unique-`yclid` count for this scope;
- `26` are repeated/revisit requests;
- `3` campaign-matched requests had no `yclid` and are excluded;
- the remaining discrepancy with `128` is NOT_PROVEN without the exact Direct report date range/filter/export.

## What is and is not proven

- PROVEN: yclid/campaign visit creation and one-time startapp creation for the current DB scope.
- PROVEN: the main persisted reliability loss is before `/mini`; only 107/221 have `MINIAPP_LOADED`.
- PROVEN: Android is the largest cohort and contains the largest absolute pre-MiniApp loss.
- PROVEN: 4 approved memberships have exact attribution; only these are safe for Yandex conversion.
- NOT_PROVEN: a server-side `channel_open` signal is the same as a visible channel screen or a join request.
- NOT_PROVEN: 100% attribution for every MAX application is possible with a shared channel invite; organic/payloadless users must remain untracked.
- BLOCKED for full 128 reconciliation: exact Direct UI export/window/filter is required.

## Goal toward 100%

For the current persisted funnel, the measurable gap to Mini App is `221 - 107 = 114` unique clicks (51.6%); the gap to the channel-open handoff signal is `116` (52.5%). The gap to approved EXACT membership is `217` (98.2%), but it includes manual approval and users who may not have submitted a request; it must not be labelled as technical loss.

No production code, DB records, campaign settings, or Yandex uploads were changed during this audit.
