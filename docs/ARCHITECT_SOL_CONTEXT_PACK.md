# MAXIBOT Architect Sol Context Pack V1

Read-only export for `ARCHITECT SOL`.

This document describes the repository and the operational contracts that must
be preserved when planning or reviewing MAXIBOT changes. It intentionally omits
secrets, tokens, cookies, private keys, raw production credentials, and PII.

## Source and scope

- Canonical root: `/Users/akhripuniov/Downloads/Codex/MAXIBOT`
- Source repository HEAD: `4ce17268670c2f1f46c71dfc1ef6267dd2504d54`
- Source state: the working tree was not clean when this export was created;
  the named source files were read from the current working tree.
- Included source files: `AGENTS.md`, the three named `docs/`/`research/`
  documents, and the seven named Python files in `app/` and `scripts/`.
- No production credentials or production records are included.

## Architecture

The primary product path is:

```text
Yandex Direct
  -> /go/{public_id} landing
  -> automatic redirect with opaque tracking payload
  -> MAX bot / Mini App
  -> target MAX channel
  -> membership or join request
  -> owner approval
  -> confirmed membership
  -> userDidSubscribe to Yandex Metrica
```

Runtime components:

1. FastAPI in `app/main.py` serves health, traffic, landing, internal ADS and
   integration endpoints.
2. `MaxBotRuntime` in `app/bot/runtime.py` consumes MAX updates and dispatches
   commands/callbacks.
3. `MenuRepository` and `render_screen()` in `app/bot/menu.py` render product,
   promotion, AI, and admin screens.
4. PostgreSQL stores application, traffic, membership, ADS and AI state.
5. Redis carries committed ADS terminal events to the MAX runtime.
6. Caddy terminates production HTTPS and routes hosts to the API.
7. The dedicated VPS MAX worker runs `python -m app.poller` and is the only
   production MAX updates poller.
8. The Mac Ads Codex Worker runs locally under launchd, claims ADS AI work,
   invokes local `codex exec`, and uploads structured results.
9. `app/ads/` contains the ADS read-only data layer, Direct reports, facts,
   trends, AI lifecycle, periodic collection and storage health.
10. `app/traffic/` contains landing attribution, deep links, visits,
    membership reconciliation and Metrica conversion delivery.

The two workers are distinct:

```text
VPS MAX worker: MAX updates -> bot handlers -> MAX API / DB
Mac ADS worker: API claim -> local codex exec -> API result upload
```

## Production topology and invariants

Production host for the MAX poller is `135.106.166.46`.

Expected processes:

```text
VPS
  caddy / HTTPS
  api       MAX_RUN_POLLING=false
  worker    MAX_RUN_POLLING=true  (only MAX poller)
  postgres
  redis

Mac
  launchd Ads Codex Worker
    Python 3.12 environment
    httpx -> production API
    local codex exec
```

Legacy VPS `37.252.10.67` must not run the production MAX worker. The local
compose API must not poll a production MAX account.

Safety invariants:

- `execution_allowed=false` for the current shadow AI contour.
- `recommendation.executed=false`.
- `ads_actions=0` unless a separately approved write task changes this.
- Direct and VK writes remain zero for read-only AI work.
- Yandex Direct report collection is read-only.
- A new AI run requires explicit approval; never create one to diagnose an old
  run.
- Do not use migrations for a presentation-only change.
- Do not expose secrets, cookies, raw frames, full AI context or PII in logs.
- Do not manipulate `incy` through `scutil`; actual Python/httpx connectivity is
  the relevant worker-path test.

## Important files and responsibilities

### Bot and MAX boundary

- `app/bot/runtime.py` — `MaxBotRuntime`, update loop, durable polling marker,
  callback dispatch, `_render()`, `_handle_ads_ai_event()`, daily reports and
  custom-bot polling.
- `app/bot/menu.py` — `MenuRepository`, `render_screen()`, `format_ads_result()`,
  `format_trend_block()`, `normalize_ai_text()`, keyboard construction and
  owner/access checks.
- `app/bot/keyboards.py` — shared keyboard helpers.
- `app/bot/router.py` — routing abstraction.
- `app/bot/texts.py` — text constants.
- `app/integrations/max_client.py` — MAX `send_message`, `edit_message`,
  updates and payload handling.
- `app/integrations/max_connection.py` — MAX client connection setup.

### ADS and AI

- `app/ads/service.py` — `can_access_ads()`, `run_check()`, latest view,
  campaign mapping and result persistence.
- `app/ads/events.py` — Redis terminal event channel and parser.
- `app/ads/facts.py` — deterministic facts and fact semantics.
- `app/ads/analysis.py` — analysis/context preparation.
- `app/ads/v2.py` — normalized entities, observations and provenance.
- `app/ads/providers/yandex.py` — Yandex Direct report provider.
- `app/ads/llm.py` — provider contract, bounded prompt and schema semantics.
- `app/ads/trends.py` — deterministic trend findings.
- `app/ads/periodic.py` — periodic collection orchestration.
- `app/ads/outcomes.py` — outcome persistence.
- `app/ads/storage_health.py` — read-only storage diagnostics.
- `app/ads/storage_governance.py` — retention/downsampling/projection policy.
- `schemas/campaign_diagnosis_schema_v1.json` — structured AI output schema.
- `scripts/ads_codex_worker.py` — Mac worker claim/exec/upload lifecycle.

### Traffic, attribution and Yandex

- `app/traffic/deep_link.py` — opaque token generation, bot/Mini App deep
  links and strict target validation.
- `app/traffic/attribution.py` — candidate scoring, `EXACT` decision and
  offline-conversion gate.
- `app/traffic/analytics.py` — funnel aggregation.
- `app/traffic/membership_reconciliation.py` — membership state repair.
- `app/traffic/metrica_sender.py` — `userDidSubscribe` delivery.
- `app/traffic/metrica_upload_tracker.py` — upload status tracking.
- `app/integrations/yandex_direct.py` — read-only Direct reports.
- `app/integrations/yandex_metrica.py` — Metrica client.
- `app/integrations/yandex_oauth.py` — OAuth state/token persistence; never log
  token contents.

### Runtime/deployment/schema

- `app/main.py` — FastAPI startup, health, traffic and internal endpoints.
- `app/poller.py` — VPS MAX polling entrypoint.
- `docker-compose.yml` — local topology; polling is false.
- `docker-compose.vps.yml` — VPS topology; only worker polling is true.
- `deploy/Caddyfile` — HTTPS host routing and reverse proxy.
- `deploy/com.maxibot.ads-codex-worker.plist` — Mac launchd definition.
- `scripts/ads_codex_worker_launchd.sh` and
  `scripts/ads_codex_worker_launchd_runtime.sh` — local worker wrappers.
- `alembic/versions/` — database migrations; avoid for UI-only work.
- `research/MAXIBOT_SINGLE_POLLER_RUNBOOK.md` — single-poller contract.
- `docs/ORCHESTRATION_PROTOCOL.md` — preflight, delegation, QA and release
  rules.
- `docs/ROLE_REGISTRY.md` — canonical role ownership.

## Real execution paths

### User UI and `/start`

```text
MAX update
  -> MaxBotRuntime.handle()
  -> action/screen dispatch
  -> MenuRepository.render()
  -> render_screen()
  -> MaxBotRuntime._render()
  -> MAX edit_message() or send_message()
```

`/start` and ordinary menu issues are first poller/runtime problems, not
renderer problems. Check the VPS worker and single-poller invariant before
editing copy.

### AI Promotion

```text
AI Продвижение screen
  -> ads_check:{channel_id}
  -> access and channel ownership checks
  -> app.ads.service.run_check()
  -> read-only snapshot and AdsAiRun
  -> WAITING_FOR_CODEX
  -> Mac worker claim
  -> CODEX_PROCESSING
  -> local codex exec
  -> structured JSON
  -> backend schema/evidence validation
  -> result persistence / CODEX_COMPLETED
  -> Redis event
  -> VPS MaxBotRuntime._handle_ads_ai_event()
  -> repo.render("ads:{channel_id}")
  -> edit original UI message
```

The callback does not execute Codex. It creates bounded work. A terminal event
must call the same renderer as the normal dashboard, not construct a second
AI-result string.

### `ads_check:*`

`MaxBotRuntime.handle()` extracts the channel ID, checks `can_access_ads()` and
ownership, calls `run_check()`, then renders the waiting state. A new run must
not be created by status refresh or event handling.

### AI result sender

`_handle_ads_ai_event()` reloads committed state, calls `repo.render()`, and
uses `edit_message()` with `disable_link_preview=True`. `_render()` applies the
same preview suppression to AI result screens and can use `force_new=True` when
MAX retained an old preview attachment.

### Traffic and deep-link path

```text
landing receives yclid/UTM
  -> opaque click/visit identity
  -> automatic redirect to MAX bot/Mini App
  -> bot_started/startapp binds MAX user to token
  -> bot supplies target channel link
  -> user_added/join signal
  -> membership and visit binding
```

The landing must not require a button click. For mobile MAX handoff, preserve
the established iPhone/iPad, Android and desktop behavior; changes require the
mobile contract regression test and two fresh real-device smokes.

## Key code semantics

### Renderer boundary

`format_ads_result()` is the canonical user-facing AI renderer. It formats
spend, conversions, CPA, membership conversion, confidence, timezone and
shadow-mode text. `normalize_ai_text()` handles compatible legacy persisted
summary wording at display time.

User-facing rules:

- stored timestamps remain UTC; UI uses `Europe/Moscow`;
- `LOW/MEDIUM/HIGH` become Russian labels;
- `MAXIBOT_QUALIFIED_MEMBERSHIP_RATE` means
  `qualified_memberships / tracked_landing_visits`;
- it must not be called attribution coverage or campaign matching coverage;
- campaign-specific matching may remain `UNKNOWN`;
- external URLs in AI prose are not allowed to create MAX previews;
- internal evidence IDs stay out of ordinary user-facing prose unless
  explicitly designed for that screen.

### Worker lifecycle

`scripts/ads_codex_worker.py` contains the important functions:

- `prompt(claim)` — creates a bounded Russian-language prompt and forbids
  Direct/VK writes.
- `findings_grounded(claim)` — checks evidence references against supplied
  context.
- `_fail(...)` — records safe stage/error telemetry without secrets.
- `_telemetry(...)` — records bounded execution metadata.
- `once()` — claims, executes, parses, uploads and handles failure.

The worker must use the same Python 3.12 environment in interactive tests and
launchd. Launchd has a different `PATH`, `HOME`, working directory and auth
visibility from an interactive shell.

### Attribution and Metrica gates

`app/traffic/attribution.py` separates candidate decision from sendability:

- `resolve_exact_identity_visit()` resolves identity-linked visits;
- `decide()` and `decide_scored()` produce attribution decisions;
- `can_send_offline_conversion()` enforces the offline send gate.

`EXACT` attribution is strong evidence of visit linkage, but an offline
conversion still needs the required `yclid`. ClientID is not a replacement for
`yclid`. A missing `yclid` must be explicit (`blocked / yclid_required`), not a
silent pending record.

`metrica_sender.py` performs the actual `userDidSubscribe` delivery. Interpret
queue fields together: `status`, `attempts`, `last_error`, upload status,
upload check time and `sent_at`. `attempts=1` can mean one successful send, not
one retry.

## ADS Data Layer and AI Director

ADS V2 stores normalized entities, observations, provenance, report runs,
collection runs, snapshots, health and governance data. Collection and Yandex
report requests are read-only in the current contour.

Facts passed to AI are deterministic and bounded. The AI must:

- use only supplied facts and evidence IDs;
- distinguish `UNKNOWN` from `ZERO`;
- distinguish campaign totals from dimensional rows;
- treat `SUPPORTED + 0 rows` as different from request failure;
- use freshness, sample size and confidence;
- treat `VOLATILE`/insufficient history as non-strong signal;
- never invent trends;
- never propose execution in the current shadow mode.

## Deployment and runtime traps

Production code runs from a release directory and Docker image, not directly
from the local checkout. For every deploy verify:

1. active release directory and image tag;
2. API and worker image parity;
3. API `MAX_RUN_POLLING=false`;
4. worker `MAX_RUN_POLLING=true`;
5. exactly one production MAX poller;
6. legacy VPS worker stopped with restart policy `no`;
7. `/health/live` and `/health/ready`;
8. runtime release markers;
9. no migration or unapproved data/write action;
10. Mac launchd installed script and environment when worker code changed.

Common failure modes:

- local code is fixed but the VPS still runs an older release;
- API and worker were rebuilt with different tags;
- launchd uses an old copied script;
- `edit_message()` preserves an earlier MAX preview attachment;
- a stale callback overwrites a valid screen;
- health is green while the MAX poller is stopped;
- a second poller causes duplicate menus/updates;
- system Python is too old for the project annotations;
- tests are run with Python 3.11/3.9 instead of `.venv312`;
- Codex command sandbox DNS failure is mistaken for a real Mac/VPN failure;
- `scutil` reports VPN state inaccurately for Network Extension/TUN;
- a persisted legacy AI result is mistaken for the current renderer;
- `ClientID` is incorrectly used as a substitute for `yclid`;
- a `VOLATILE` three-day signal is reported as a stable trend.

## Practical debugging playbook

1. Classify the symptom: text, button, preview, stale edit, `/start`, AI
   status, data or permissions.
2. For UI, open `app/bot/menu.py`; locate `render_screen()` and the exact
   screen/formatter.
3. Open `app/bot/runtime.py`; trace callback routing, `_render()`,
   `_handle_ads_ai_event()`, edit/send arguments and preview flags.
4. Identify the exact callback, especially `ads_check:{channel_id}`.
5. Identify the production release and running container image.
6. Check the responsible process: VPS MAX worker for `/start`/callbacks, Mac
   Ads worker for claim/exec/upload, periodic runtime for collection.
7. Read production DB state only after the path is known; inspect safe metadata
   from `ads_ai_runs`, `traffic_visits`, `traffic_memberships` and conversion
   queues.
8. Compare the working manual path with launchd environment: Python, PATH,
   HOME, Codex binary, auth visibility, cwd, timeout and network.
9. Run targeted tests in `.venv312`.
10. Change the smallest layer that owns the symptom, then verify production
    release/parity/health and relevant E2E behavior.

## Current project state and technical debt

Working/current contracts:

- production API and VPS worker are separate services with one MAX poller;
- Mac worker is the local AI execution bridge;
- ADS AI result rendering is unified and localized;
- traffic attribution and Metrica gates are explicit;
- product UI and admin storage UI are separated;
- current AI contour is shadow/read-only;
- external AI URLs are sanitized before user-facing rendering.

Partial/legacy areas:

- campaign-specific matching can legitimately remain unavailable;
- historical AI summaries may need display-time normalization;
- granular/periodic ADS data requires separate controlled validation;
- release directories and local working tree can drift;
- some old test failures are baseline failures and must be compared before
  attributing them to a new change.

Dangerous areas:

- polling cursor advancement and single-poller policy;
- `AdsAiRun` claim/lease/status transitions;
- mobile deep-link handoff;
- attribution and `yclid` send gate;
- Caddy/TLS routing;
- launchd environment and installed runtime copy;
- any code that mixes product renderer with admin/storage metrics.

## Architect Sol fast context

1. Start with repository preflight and identify the active release.
2. Never trust local `git diff` as production evidence.
3. `/start` first means check the VPS MAX worker.
4. There must be one production MAX poller.
5. API polling is false; worker polling is true.
6. The Mac Ads worker is not the MAX poller.
7. `ads_check:*` creates bounded work; it does not run Codex.
8. Use an existing waiting run for diagnosis when explicitly requested.
9. The event handler must reuse the canonical AI renderer.
10. MAX edit and send paths both need preview testing.
11. Use `force_new` when stale MAX attachments survive an edit.
12. AI prose is untrusted at the message boundary.
13. URL sanitization is display-only; do not rewrite historical AI JSON.
14. `UNKNOWN` is not `ZERO`.
15. Campaign total is not the sum of dimensions.
16. `VOLATILE` is not a stable trend.
17. Membership conversion is not attribution coverage.
18. `EXACT` still needs `yclid` for offline conversion.
19. ClientID cannot replace `yclid`.
20. Queue `attempts` must be read with upload status and error fields.
21. Python 3.12 is the worker/test runtime.
22. launchd environment differs from the shell.
23. Sandbox DNS errors do not prove a Mac/VPN failure.
24. `scutil` is not Network Extension ground truth.
25. Landing navigation is automatic; do not add a required click.
26. Mobile handoff changes require real-device QA.
27. Storage health belongs only in `/admin`.
28. UI-only changes normally need no migration.
29. Do not create AI runs without explicit approval.
30. Keep Direct/VK writes and `ads_actions` at zero for shadow work.
31. Check the legacy VPS after every production deploy.
32. Health 200 does not prove polling or E2E.
33. New release must have API/worker parity.
34. No secrets, raw contexts, cookies or PII in logs/handoffs.
35. Unit tests are necessary but do not establish production PASS alone.

## New Dev bootstrap

Read in this order:

1. `AGENTS.md`.
2. `docs/ORCHESTRATION_PROTOCOL.md`.
3. `docs/ROLE_REGISTRY.md`.
4. `research/MAXIBOT_SINGLE_POLLER_RUNBOOK.md`.
5. `app/bot/runtime.py` and `app/bot/menu.py`.
6. `app/ads/service.py` and `scripts/ads_codex_worker.py`.
7. `app/traffic/deep_link.py`, `attribution.py` and `metrica_sender.py`.

For a bug, trace the full path before editing: callback/command, handler,
service, renderer, sender, active release and process. For AI bugs additionally
trace claim, lease, Codex executable, JSON/schema/evidence validation, upload,
Redis event and final MAX edit. For attribution bugs trace visit, `yclid`,
identity, membership, owner approval, attribution candidate and Metrica queue.

Do not start a second poller, run production Codex on the VPS, use system
Python, modify Direct/VK during a read-only task, treat ClientID as `yclid`,
add a landing click, delete user messages, or declare E2E from unit tests only.

## Export footer

```text
CONTEXT_PACK_SOURCE_SHA = 4ce17268670c2f1f46c71dfc1ef6267dd2504d54
FILES_INCLUDED = 11 requested source/instruction files
FILES_SKIPPED = none of the requested files
SECRET_SCAN = PASS
```
