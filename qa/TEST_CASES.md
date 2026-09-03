# MAXIBOT test-case registry

This is the single QA registry. Test cases are retained across releases. A case may be changed or removed only when the requirement is removed, officially changed, or the case is a documented duplicate.

This registry is layered over the preserved pre-existing suite listed in `qa/EXISTING_TESTS_MANIFEST.md`; it does not replace that suite. Coverage gaps and functions currently marked `NOT_COVERED` are listed in `qa/TEST_AUDIT.md`.

`CRITICAL_REGRESSION` is the set marked `CRITICAL` below. A blocked or not-run critical case is not a pass.

| ID | Название | Компонент | Приоритет | Тип | Режим | Предусловия | Шаги | Ожидаемый результат | Автотест | Последний успешный запуск | Статус |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TC-BOOT-001 | Приложение импортируется и стартует | app/runtime | CRITICAL | SMOKE | AUTOMATED | Python 3.12+, dependencies installed | Import app and start ASGI process | Process starts without exception | tests/test_runtime_entrypoints.py | — | ACTIVE |
| TC-DB-001 | База данных доступна | database/health | CRITICAL | SMOKE | AUTOMATED | Staging DB is configured | Request `/health/ready` | HTTP 200 and database ready | docker-compose.vps.yml healthcheck | — | ACTIVE |
| TC-DB-002 | Существующие данные читаются | database/API | CRITICAL | REGRESSION | AUTOMATED | Seeded or existing staging data | Read channels and dialogs | Existing rows remain readable | tests/test_scenarios.py | — | ACTIVE |
| TC-DB-003 | Migration chain upgrades cleanly | alembic | CRITICAL | INTEGRATION | SEMI_AUTOMATED | Disposable staging DB | Run `alembic upgrade head` | All migrations apply without destructive failure | alembic/versions/ | — | ACTIVE |
| TC-BOT-001 | Main menu renders counters | bot/menu | HIGH | REGRESSION | AUTOMATED | Stub client | Render main menu | Menu contains channel and bot counters | tests/test_core.py | — | ACTIVE |
| TC-BOT-002 | Bot navigation and back preserve one service message | bot/runtime | HIGH | REGRESSION | AUTOMATED | Stub client | Start bot, navigate, go back | Existing service message is reused/edited | tests/test_scenarios.py | — | ACTIVE |
| TC-BOT-003 | Receiving toggle is persisted and rendered | bot/runtime | HIGH | REGRESSION | AUTOMATED | Stub client | Toggle receiving state | Callback and state agree | tests/test_scenarios.py | — | ACTIVE |
| TC-BOT-004 | Templates can be changed | bot/menu | HIGH | REGRESSION | AUTOMATED | Stub client | Edit both message templates | New text is stored and rendered | tests/test_scenarios.py | — | ACTIVE |
| TC-BOT-005 | Block, unblock and admin reply work | bot/runtime | HIGH | REGRESSION | AUTOMATED | Stub client and conversation | Block, unblock, reply | State changes and reply is delivered | tests/test_scenarios.py | — | ACTIVE |
| TC-BOT-006 | Callback promotes clicked message to canonical panel | bot/runtime | HIGH | REGRESSION | AUTOMATED | Stub client and callback update with message MID | Navigate from a callback | Callback is acknowledged, clicked MID becomes canonical, and the normal renderer updates that panel | tests/regression/test_latest_runtime_changes.py | — | ACTIVE |
| TC-BOT-007 | Duplicate inbound MAX message is suppressed | bot/runtime | HIGH | REGRESSION | AUTOMATED | Repeated update with same external message ID | Handle the update twice | Only the first logical message is processed | tests/regression/test_latest_runtime_changes.py | — | ACTIVE |
| TC-BOT-008 | Custom bot channel readiness is unambiguous | bot/custom-bot | HIGH | REGRESSION | AUTOMATED | Custom bot and channel configuration fixtures | Inspect ready and unbound channel states | Personal link is shown only when exactly one bound channel is ready; ambiguous inbound is blocked | tests/regression/test_custom_bot_channel_readiness.py | — | ACTIVE |
| TC-MAX-001 | MAX payload normalization accepts production wrappers | integrations/max | CRITICAL | REGRESSION | AUTOMATED | Fixture payloads | Normalize bot/message/callback updates | Correct user, chat and payload IDs are recovered | tests/test_update_payload.py | — | ACTIVE |
| TC-MAX-002 | Real client uses safe documented HTTP contract | integrations/max | HIGH | INTEGRATION | AUTOMATED | Mock HTTP transport | Send/edit/read member calls | Correct method, path, headers and IDs are used | tests/test_real_max_client.py | — | ACTIVE |
| TC-MAX-003 | MAX binary codec fails closed | integrations/max | HIGH | REGRESSION | AUTOMATED | Sanitized fixtures | Decode valid and malformed frames | Valid frames decode; malformed frames raise safe error | tests/test_max_binary_codec.py | — | ACTIVE |
| TC-MAX-004 | Connected channel link is discovered and scoped | integrations/max/channel-sync | CRITICAL | REGRESSION | AUTOMATED | Stub MAX chat response | Process bot-added sync | Correct chat link is persisted for the matching channel | tests/test_max_channel_sync.py | — | ACTIVE |
| TC-MAX-005 | Auto-approve remains disabled by default | traffic/auto-approve | CRITICAL | REGRESSION | AUTOMATED | Feature flag OFF | Run pending worker | No approval RPC is sent | tests/test_auto_approve.py | — | ACTIVE |
| TC-TRAFFIC-001 | Landing redirects without a required click | landing | CRITICAL | E2E | AUTOMATED | Active landing with valid target | Open landing URL | Browser automatically redirects to MAX | tests/test_landing_ui_contract.py | — | ACTIVE |
| TC-TRAFFIC-002 | Landing stores yclid and UTM attribution | traffic/landing | CRITICAL | REGRESSION | AUTOMATED | Test DB | Open landing with query parameters | Visit stores attribution fields | tests/test_landing_ui_contract.py | — | ACTIVE |
| TC-TRAFFIC-003 | Target is scoped to the landing channel | traffic/redirect | CRITICAL | REGRESSION | AUTOMATED | Multiple channels/profiles | Open landing and redirect | Only matching valid target is used; no fallback | tests/test_traffic_deep_link.py | — | ACTIVE |
| TC-TRAFFIC-004 | Opaque start payload binds MAX user to visit | traffic/deep-link | CRITICAL | REGRESSION | AUTOMATED | Stub MAX update | Start bot with opaque payload | User-to-click binding is created without leaking yclid | tests/test_traffic_deep_link.py | — | ACTIVE |
| TC-TRAFFIC-005 | Ambiguous attribution never sends conversion | traffic/attribution | CRITICAL | REGRESSION | AUTOMATED | Candidate fixtures | Evaluate zero/one/multiple candidates | Only exact verified identity is eligible | tests/test_traffic_attribution.py | — | ACTIVE |
| TC-TRAFFIC-006 | userDidSubscribe requires verified membership and yclid | traffic/metrica | CRITICAL | REGRESSION | AUTOMATED | Mock Metrica sender | Send subscription event | Event is sent only after both gates pass | tests/test_metrica_sender.py | — | ACTIVE |
| TC-TRAFFIC-007 | Traffic v2 scoring stays conservative | traffic/attribution | HIGH | REGRESSION | AUTOMATED | Scored candidate fixtures | Compare single and multiple candidates | Ambiguous candidates never become HIGH incorrectly | tests/test_traffic_v2.py | — | ACTIVE |
| TC-TRAFFIC-008 | Invalid MAX target blocks redirect | traffic/redirect | CRITICAL | REGRESSION | AUTOMATED | Missing or hostile target | Attempt redirect | Request is blocked with no foreign fallback | tests/test_traffic_deep_link.py | — | ACTIVE |
| TC-TRAFFIC-009 | Expired startapp visit is not reused | traffic/deep-link | CRITICAL | REGRESSION | AUTOMATED | Expired unbound visit | Open landing again | Old startapp token is expired and a fresh opaque token is created | tests/regression/test_landing_fresh_startapp_token.py | — | ACTIVE |
| TC-TRAFFIC-010 | Exact user-added attribution is channel-scoped | traffic/attribution | CRITICAL | REGRESSION | AUTOMATED | Exact identity candidate fixtures | Resolve one or multiple visit candidates | One exact visit resolves; none or ambiguous candidates remain unattributed | tests/test_exact_user_added_attribution.py | — | ACTIVE |
| TC-TRAFFIC-011 | Identity PoC binds YCLID to MAX user and join request | traffic/identity-poc | HIGH | REGRESSION | AUTOMATED | In-memory PoC fixtures | Create visit, resolve signed token, observe join | Only the bound user/channel produces attribution; token is signed and one-time | tests/test_identity_poc.py | — | ACTIVE |
| TC-HEALTH-001 | Liveness endpoint responds | api/health | CRITICAL | SMOKE | AUTOMATED | App running | Request `/health/live` | HTTP 200 and alive status | app/main.py health routes; staging smoke | — | ACTIVE |
| TC-HEALTH-002 | Critical worker starts | worker/poller | CRITICAL | SMOKE | SEMI_AUTOMATED | Staging deployment | Inspect worker container and logs | Worker is running without critical errors | docker-compose.vps.yml | — | ACTIVE |
| TC-OPS-001 | Exactly one production poller is enabled | deployment/runtime | CRITICAL | REGRESSION | AUTOMATED | Application and VPS Compose files | Inspect polling defaults and worker override | Local/API containers have `MAX_RUN_POLLING=false`; only VPS worker has `true` and runs `app.poller` | tests/regression/test_single_poller_safety.py | — | ACTIVE |
| TC-OPS-002 | Daily channel report is Moscow-time and fail-closed | worker/daily-reports | HIGH | REGRESSION | AUTOMATED | Report time and count fixtures | Calculate due date and render report | Prior Moscow day is selected; unknown counts/source are not guessed | tests/test_daily_reports.py | — | ACTIVE |
| TC-MAX-006 | Channel MAX connection resolution is explicit | integrations/max-connection | HIGH | REGRESSION | AUTOMATED | Primary/custom/legacy row fixtures | Resolve channel transport | Primary and custom clients are selected explicitly; invalid states fail closed | tests/test_max_connection_resolution.py | — | ACTIVE |
| TC-TRAFFIC-012 | Landing public ID is generated safely | traffic/landing | HIGH | REGRESSION | AUTOMATED | ID generator | Generate public landing ID | ID is a numeric 14-digit public identifier | tests/test_landing_public_id.py | — | ACTIVE |
| TC-TRAFFIC-013 | iPhone MAX handoff keeps the real-device-proven platform branch | traffic/mobile-handoff | CRITICAL | REGRESSION | AUTOMATED | Current landing and Mini App source | Validate mobile landing, iOS anchor, Android and MAX Web branches | iPhone cannot fall through to `openMaxLink`; same startapp is used and no timer creates a second navigation | tests/regression/test_mobile_max_handoff_contract.py | 2026-08-21 | ACTIVE |
| TC-TRAFFIC-014 | Fresh iPhone handoff from MAX opens the target channel | traffic/mobile-handoff | CRITICAL | SMOKE | MANUAL | Real iPhone/iPad, fresh promotion visit | Open promotion URL from a MAX chat | Mini App opens and then the intended channel opens without a required click | QA_PROCESS.md mobile MAX checklist | 2026-08-21 | ACTIVE |
| TC-TRAFFIC-015 | Fresh iPhone handoff from Safari opens the target channel | traffic/mobile-handoff | CRITICAL | SMOKE | MANUAL | Real iPhone/iPad Safari, fresh promotion visit | Open promotion URL in Safari and accept the iOS MAX prompt | Mini App opens and then the intended channel opens without a required click | QA_PROCESS.md mobile MAX checklist | 2026-08-21 | ACTIVE |
| TC-TRAFFIC-016 | Android handoff is separately accepted on a real device | traffic/mobile-handoff | CRITICAL | SMOKE | MANUAL | Real Android device, fresh promotion visit | Open promotion URL and follow Android branch | `window.location.replace(target_url)` reaches the intended channel; source checks alone do not pass this case | QA_PROCESS.md mobile MAX checklist | 2026-08-21 | BLOCKED until real Android E2E |
| TC-TRAFFIC-017 | Traffic diagnostic stages are safe, idempotent and non-converting | traffic/diagnostics | HIGH | REGRESSION | AUTOMATED | Sanitized telemetry fixtures | Record landing/Mini App stages and inspect aggregate diagnostics | Allowlisted, bounded, idempotent diagnostics never create attribution or conversion | tests/regression/test_traffic_diagnostic_stages.py | 2026-08-28 | ACTIVE |
| TC-TRAFFIC-018 | Confirmed MAX handoff records proof without minting a new visit | traffic/diagnostics | HIGH | REGRESSION | AUTOMATED | Handoff attempt/confirmation fixtures | Record browser and Mini App handoff stages | Confirmation requires the real Mini App route and remains tied to the existing visit | tests/regression/test_confirmed_max_handoff.py | 2026-08-28 | ACTIVE |
| TC-TRAFFIC-019 | Exact verified identity is the only conversion queue gate | traffic/conversion | CRITICAL | REGRESSION | AUTOMATED | Funnel and exact identity fixtures | Evaluate matching, ambiguous and payloadless memberships | Only exact verified startapp identity queues one Yandex conversion; diagnostics do not mutate conversion state | tests/regression/test_promotion_reporting_and_exact_gate.py, tests/test_funnel_reporting.py | 2026-08-28 | ACTIVE |
| TC-MAX-007 | Official bot-added channel connection fails closed and remains owner-scoped | integrations/max/channel-sync | CRITICAL | REGRESSION | AUTOMATED | Official bot-added payload fixtures | Process valid, conflicting, non-channel and invalid-link events | Only authorized channel metadata with a valid official link is connected; conflicts and invalid links are rejected | tests/test_bot_added_auto_connect.py | 2026-08-28 | ACTIVE |
| TC-BOT-011 | System MAXIBOT promotion remains owner-scoped | bot/promotion | HIGH | REGRESSION | AUTOMATED | System promotion fixtures | Render system promotion and callbacks | System bot, Yandex and landing scopes are isolated and unauthorized callbacks are blocked | tests/regression/test_owner_maxibot_promotion.py | 2026-08-28 | ACTIVE |
| TC-YANDEX-003 | Offline conversion target uses the configured goal identifier | integrations/yandex | HIGH | REGRESSION | AUTOMATED | Offline target source contract | Build conversion upload payload | Configured target/goal identifier is used; numeric goal ID is not substituted | tests/regression/test_yandex_offline_target_contract.py | 2026-08-28 | ACTIVE |
| TC-YANDEX-004 | Direct API errors are explicit and read-only | integrations/yandex-direct | HIGH | REGRESSION | AUTOMATED | Mock Direct API responses | Fetch campaign and handle HTTP/body errors | Read failures are surfaced and no error is treated as empty success | tests/test_yandex_direct.py | 2026-08-28 | ACTIVE |
| TC-OAUTH-003 | OAuth recovery retry is exact and count-guarded | integrations/yandex | HIGH | REGRESSION | AUTOMATED | Exact recovery batch fixtures | Attempt a bounded recovery retry | Only the approved exact row can retry; duplicate/bulk retry is blocked | tests/test_oauth_recovery_exact_batch.py | 2026-08-28 | ACTIVE |
| TC-BOT-009 | Promotion menu keeps approved navigation contract | bot/menu | HIGH | REGRESSION | AUTOMATED | Menu source and channel | Open promotion channel menu | Expected short Yandex, landing, analytics, settings and back items are present; MAX target check is not exposed | tests/regression/test_promotion_menu_contract.py | — | ACTIVE |
| TC-BOT-010 | Promotion summaries render safe channel and Yandex state | bot/menu | HIGH | REGRESSION | AUTOMATED | Promotion summary fixtures | Render connected and unconnected summaries | Missing values use safe placeholders and no secrets/internal flags leak | tests/test_promotion_channel_summary.py, tests/test_promotion_yandex_summary.py | — | ACTIVE |
| TC-OAUTH-001 | Yandex OAuth state and token exchange are safe | integrations/yandex | HIGH | REGRESSION | AUTOMATED | Mock OAuth transport | Create state and exchange code | State is validated; secret is not exposed | tests/test_yandex_oauth.py | — | ACTIVE |
| TC-OAUTH-002 | Yandex callback stores counter for the selected channel | integrations/yandex | HIGH | REGRESSION | AUTOMATED | Callback flow source contract | Complete OAuth callback | Only selected channel profile receives the counter ID | tests/test_yandex_callback_counter.py | — | ACTIVE |
| TC-UI-001 | Mini app loads and protects API calls | miniapp | HIGH | E2E | MANUAL | Signed-in MAX test user | Open mini app, load dialogs, open chat | UI loads, auth failures are visible, no data leak | miniapp/index.html; miniapp/app.js | — | ACTIVE |
| TC-EXT-001 | Real MAX channel join flow works | external/MAX | CRITICAL | E2E | MANUAL | Dedicated test bot/channel and credentials | Follow landing, open bot, join channel | User is linked to the intended channel and membership is confirmed | — | — | ACTIVE |
| TC-EXT-002 | Production-like deploy and rollback smoke | deploy/production | CRITICAL | SMOKE | MANUAL | Owner approval and rollback image | Deploy candidate, smoke, rollback if needed | Health, DB, workers and critical flows remain available | scripts/qa_check.sh | — | ACTIVE |

## Critical regression set

`TC-BOOT-001`, `TC-DB-001`, `TC-DB-002`, `TC-DB-003`, `TC-MAX-001`, `TC-MAX-004`, `TC-MAX-005`, `TC-MAX-007`, `TC-TRAFFIC-001`, `TC-TRAFFIC-002`, `TC-TRAFFIC-003`, `TC-TRAFFIC-004`, `TC-TRAFFIC-005`, `TC-TRAFFIC-006`, `TC-TRAFFIC-008`, `TC-TRAFFIC-009`, `TC-TRAFFIC-010`, `TC-TRAFFIC-013`, `TC-TRAFFIC-014`, `TC-TRAFFIC-015`, `TC-TRAFFIC-016`, `TC-TRAFFIC-019`, `TC-HEALTH-001`, `TC-HEALTH-002`, `TC-OPS-001`, `TC-EXT-001`, `TC-EXT-002`.

## Current verification status

Statuses below reflect the latest audit evidence, not the existence of implementation code.

| ID | Verification status | Evidence |
|---|---|---|
| TC-BOOT-001 | VERIFIED_WORKING | Full automated suite and staging API startup |
| TC-DB-001 | VERIFIED_WORKING | Staging `/health/ready` and PostgreSQL healthcheck |
| TC-DB-002 | VERIFIED_WORKING | Automated seeded data regression |
| TC-DB-003 | VERIFIED_WORKING | Staging container runs `alembic upgrade head` |
| TC-BOT-001 | VERIFIED_WORKING | Automated bot/menu regression |
| TC-BOT-002 | VERIFIED_WORKING | Automated navigation regression |
| TC-BOT-003 | VERIFIED_WORKING | Automated receiving regression |
| TC-BOT-004 | VERIFIED_WORKING | Automated template regression |
| TC-BOT-005 | VERIFIED_WORKING | Automated block/reply regression |
| TC-BOT-006 | VERIFIED_WORKING | Automated callback panel regression |
| TC-BOT-007 | VERIFIED_WORKING | Automated duplicate inbound regression |
| TC-BOT-008 | VERIFIED_WORKING | Automated custom-bot readiness regression |
| TC-BOT-009 | VERIFIED_WORKING | Automated promotion menu regression |
| TC-BOT-010 | VERIFIED_WORKING | Automated promotion summary regressions |
| TC-MAX-001 | VERIFIED_WORKING | Automated payload regression |
| TC-MAX-002 | VERIFIED_WORKING | Mocked HTTP contract regression |
| TC-MAX-003 | VERIFIED_WORKING | Automated codec regression |
| TC-MAX-004 | VERIFIED_WORKING | Automated channel sync regression |
| TC-MAX-005 | VERIFIED_WORKING | Automated auto-approve safety regression |
| TC-TRAFFIC-001 | VERIFIED_WORKING | Automated landing contract plus staging static route |
| TC-TRAFFIC-002 | VERIFIED_WORKING | Automated attribution regression |
| TC-TRAFFIC-003 | VERIFIED_WORKING | Automated target scoping regression |
| TC-TRAFFIC-004 | VERIFIED_WORKING | Automated opaque payload regression |
| TC-TRAFFIC-005 | VERIFIED_WORKING | Automated conservative attribution regression |
| TC-TRAFFIC-006 | VERIFIED_WORKING | Automated Metrica gate regression |
| TC-TRAFFIC-007 | VERIFIED_WORKING | Automated traffic v2 regression |
| TC-TRAFFIC-008 | VERIFIED_WORKING | Automated invalid target regression |
| TC-TRAFFIC-009 | VERIFIED_WORKING | Automated expired startapp regression |
| TC-TRAFFIC-010 | VERIFIED_WORKING | Automated exact attribution regression |
| TC-TRAFFIC-011 | VERIFIED_WORKING | Automated signed identity PoC regression |
| TC-HEALTH-001 | VERIFIED_WORKING | Staging `/health/live` |
| TC-HEALTH-002 | WORKING_UNTESTED | QA stack has no dedicated worker service |
| TC-OPS-001 | VERIFIED_WORKING | Automated compose/runtime safety regression |
| TC-OPS-002 | VERIFIED_WORKING | Automated daily report regression |
| TC-MAX-006 | VERIFIED_WORKING | Automated MAX connection resolution regression |
| TC-TRAFFIC-012 | VERIFIED_WORKING | Automated public ID regression |
| TC-TRAFFIC-013 | VERIFIED_WORKING | Six automated platform-contract checks plus two real owner iPhone recordings (MAX and Safari) on 2026-08-21 |
| TC-TRAFFIC-014 | VERIFIED_WORKING | Owner recording: fresh iPhone entry from MAX, 2026-08-21 |
| TC-TRAFFIC-015 | VERIFIED_WORKING | Owner recording: fresh iPhone entry from Safari, 2026-08-21 |
| TC-TRAFFIC-016 | VERIFIED_WORKING | Sanitized owner evidence: Android handoff reached target channel |
| TC-TRAFFIC-017 | VERIFIED_WORKING | Automated telemetry safety and diagnostic-stage regression |
| TC-TRAFFIC-018 | VERIFIED_WORKING | Automated confirmed-handoff regression |
| TC-TRAFFIC-019 | VERIFIED_WORKING | Automated exact conversion gate and funnel regressions |
| TC-MAX-007 | VERIFIED_WORKING | Automated bot-added owner/link safety regressions |
| TC-BOT-011 | VERIFIED_WORKING | Automated system MAXIBOT promotion regressions |
| TC-YANDEX-003 | VERIFIED_WORKING | Automated offline target regression |
| TC-YANDEX-004 | VERIFIED_WORKING | Automated Direct API error/read-only regressions |
| TC-OAUTH-003 | VERIFIED_WORKING | Automated exact recovery guard regression |
| TC-OAUTH-001 | VERIFIED_WORKING | Automated mocked OAuth regression |
| TC-OAUTH-002 | VERIFIED_WORKING | Automated Yandex callback scoping regression |
| TC-UI-001 | WORKING_UNTESTED | Static mini app route checked; signed MAX auth flow not run |
| TC-EXT-001 | VERIFIED_WORKING | Sanitized owner evidence: join request completed and member confirmed |
| TC-EXT-002 | VERIFIED_WORKING | Isolated local candidate deploy and rollback smoke passed on 18100/55532/56479; live production untouched |

## Change log

- 2026-08-13: Initial registry created from the existing test suite, health/deployment contracts, traffic flow requirements, and manual MAX scenarios.
