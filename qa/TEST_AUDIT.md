# Existing test audit — 2026-08-13

## Scope

The audit covered all existing `tests/**/*.py`, the application health and landing routes, Alembic migrations, Dockerfiles and Compose files.

## Findings and actions

- Existing coverage already exercises bot menu/runtime, MAX payloads and clients, traffic attribution/deep links, Yandex integrations, landing UX, demo flows and runtime entrypoints.
- The original suite was flat. Tests are now additionally classified under `tests/unit/`, `tests/integration/`, `tests/regression/`, `tests/e2e/` and `tests/smoke/`; the original tests remain in the regression baseline to avoid losing coverage.
- The Mac host Python is 3.9.6 while the project requires Python 3.12+. Local host pytest therefore cannot be used as a release signal. The QA gate runs tests inside the Python 3.12 candidate image.
- Staging previously referenced production `.env`; it now uses `.env.staging`, local stub MAX mode, local-only credentials and explicitly named staging resources.
- The initial staging E2E probe referenced `/traffic-test`, which is not a smoke page. It now checks the existing `/health/live`, `/mini` and `/traffic-test/app` routes.
- A new QA contract test caught and fixed a parameterized-port assertion during the first Docker QA run. The fixed test is retained in the regression suite.

## Baseline result

The completed Docker QA cycle ran 244 tests: 244 passed, 0 failed, 0 skipped. No production credentials or production deployment were used.

## Functions currently without direct automated endpoint coverage

The registry is built over the existing tests, but a passing regression suite does not mean every route is covered. These are explicit `NOT_COVERED` gaps:

- Health/readiness: `/health`, `/health/traffic-ready` (live/ready are covered by staging smoke, not a direct endpoint test).
- MAX ingress: `/max/webhook` and the full HTTP-to-runtime webhook path.
- Traffic identity and membership: `/api/traffic/identify`, `/api/traffic/channel-open`, `/api/traffic/check-subscription`, `/traffic/events/browser`.
- Traffic diagnostics: `/admin/traffic-debug`, `/admin/traffic/attributions`, including authorization and redaction behavior.
- Admin data API: channels, dialogs, message/image delivery, block/unblock, receiving, and template HTTP endpoints.
- Public conversation API: `/api/public/start` and `/api/public/messages`.
- OAuth callback: the happy/error HTTP callback flow, not just URL construction and token exchange.
- Traffic handlers: `/traffic-test`, `/traffic-test/click`, `/go/{public_id}`, and public slug redirect handlers.
- Demo HTTP route: `/api/demo/menu` (demo tests cover the runtime/client path, not this HTTP route).

The latest runtime delta was specifically added to coverage: promoting the clicked callback message MID to the canonical service panel and duplicate inbound message suppression are covered by `TC-BOT-006` and `TC-BOT-007`.

The single-poller safety improvement is covered by `TC-OPS-001` and
`tests/regression/test_single_poller_safety.py`; the operational contract is
documented in `docs/ARCHITECT_SOL_CONTEXT_PACK.md` and the mandatory project
instruction in `AGENTS.md`.

Manual MAX join/member confirmation remains `MANUAL` because it requires a dedicated MAX account/channel and owner approval.

## Current candidate delta audit — 2026-08-28

The latest developer handoffs added or changed the following covered areas:

- mobile MAX handoff contract and real-device acceptance: `TC-TRAFFIC-013` through `TC-TRAFFIC-016`;
- browser/Mini App diagnostic telemetry and confirmed handoff stages: `TC-TRAFFIC-017` and `TC-TRAFFIC-018`;
- exact verified conversion and funnel reporting gates: `TC-TRAFFIC-019`;
- official `bot_added` owner/link safety: `TC-MAX-007`;
- system MAXIBOT owner-scoped promotion: `TC-BOT-011`;
- Yandex offline target and Direct API error handling: `TC-YANDEX-003` and `TC-YANDEX-004`;
- exact-count OAuth recovery: `TC-OAUTH-003`.
- AI result external-link sanitization: `TC-ADS-001`.

These cases are mapped to the current automated tests in the registry and are
included by the full `tests`/`tests/regression` commands. Sanitized owner
evidence then verified the external MAX join/member flow and Android handoff;
an isolated local production-like candidate deploy and rollback also passed.
The iPhone MAX/Safari cases remain manual evidence-backed cases and were not
replayed by this QA session.

Manual evidence, source-level assertions and local rollback are recorded
separately; none is substituted for a different environment or live production
verification.

The current AI hotfix has automated coverage, but real MAX rendering of the
result card was not performed because no isolated live MAX owner session was
available. The live render remains a manual acceptance item and is not inferred
from the stub-client tests.
