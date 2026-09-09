# QA process

Run the single entry point:

```bash
./scripts/qa_check.sh
```

The runner executes static checks, unit tests, integration tests, the full regression baseline, Docker build, staging health, and staging E2E in that order. Any mandatory failure stops the gate and writes `QA_VERDICT = NO_GO` to `RELEASE_STATUS.md` and an immutable file in `releases/`.

The default run never deploys production. Configure `QA_STAGING_URL` and `QA_STAGING_E2E_CMD` to make staging mandatory. For local Docker Desktop on macOS, use the isolated staging stack:

```bash
QA_LOCAL_DOCKER=1 ./scripts/qa_check.sh
```

This builds the candidate image, starts the local Postgres/Redis/API stack with a unique Compose project and isolated API port (`18000` by default), waits for `/health/ready`, runs health/mini-app/traffic smoke checks, and removes only that QA stack on exit. Set `QA_STAGING_PORT`, `QA_HEALTH_RETRIES`, and `QA_HEALTH_INTERVAL` when needed.

To deploy after a GO, an owner must explicitly run:

```bash
OWNER_APPROVED=1 ./scripts/qa_check.sh DEPLOY
```

The deploy command must be supplied through `PRODUCTION_DEPLOY_CMD`; smoke uses `PRODUCTION_URL/health/ready`. A failed production smoke invokes `PRODUCTION_ROLLBACK_CMD` when configured and records the rollback result. Previous and candidate SHAs are recorded before deployment.

The runner stores logs under `.qa-logs/` (override with `QA_LOG_DIR`) and keeps immutable release records under `releases/`. Secrets must be passed through the environment and are not printed by the runner.

QA artifact persistence is monitored separately from product QA. Before running
it, provide `QA_RESULT_INGEST_URL` and the secret `QA_RESULT_INGEST_TOKEN`
through the operator/CI secret environment. The URL must be HTTPS; local Docker
QA may use the runner's default localhost ingest URL. The token is sent only as
the `X-MAXIBOT-Admin-Token` header and never enters QA artifacts. Missing,
unreachable, or unauthorized ingest records
`QA_ARTIFACT_PERSISTENCE = WARN` while retaining local artifacts. It does not
turn an otherwise fully green product execution into NO_GO. Product failures,
unknown critical cases, mandatory skips, staging failures, and E2E failures
remain fail-closed and produce `QA_VERDICT = NO_GO`.

Production SHA parity is read only from `QA_PRODUCTION_SHA` (an exact commit
SHA supplied by deployment metadata) or `QA_PRODUCTION_URL/health/ready` field
`commit_sha`. The application exposes that field from `MAXIBOT_COMMIT_SHA`.
Release markers and human-readable release IDs are not treated as commit SHAs;
when the field is unavailable the dashboard reports `UNKNOWN`.

`RELEASE_STATUS.md` separates executed pytest counts (`TOTAL_TESTS`, `PASSED`, `FAILED`, `SKIPPED`) from registry counts (`TOTAL_TEST_CASES`, `AUTOMATED_CASES`, `MANUAL_CASES`, `SEMI_AUTOMATED_CASES`). `NEW_*_CASES` fields are calculated against the previous release report; they are not aliases for the total registry size.

Functional verification uses only four statuses: `VERIFIED_WORKING`, `WORKING_UNTESTED`, `BROKEN`, and `UNKNOWN`. Any `BROKEN_CRITICAL` or `UNKNOWN_CRITICAL` case forces `QA_VERDICT = NO_GO`, even when automated tests and staging smoke are green.

The current local staging stack uses `.env.staging`, API port `18000`, PostgreSQL port `55432`, Redis port `56379`, and named resources prefixed `maxibot_staging_`. It is independent from the production Compose project and is safe to remove with the matching QA Compose project only.

The test inventory and audit are documented in `qa/TEST_CASES.md`, `qa/TEST_AUDIT.md`, and the preservation manifest `qa/EXISTING_TESTS_MANIFEST.md`. The host Python version is not a valid release runtime unless it satisfies `pyproject.toml`; the Docker image is the authoritative runtime for the local gate.

## Mobile MAX handoff manual acceptance checklist

This checklist is mandatory after any change to `app/main.py`,
`app/traffic/deep_link.py`, or `miniapp/traffic.html`. Automated contract tests
do not replace these real-device checks.

Use a fresh promotion visit/token for each run. Do not record or publish raw
tokens, cookies, MAX user IDs, or personal data.

1. **iPhone from MAX — MANUAL / CRITICAL**: open the promotion URL from a MAX
   chat on a real iPhone or iPad. Confirm the same visit reaches the MAX Mini
   App and then the intended target channel without a required landing button
   tap or Mini App button tap. Mark `VERIFIED_WORKING` only after the target
   channel is visibly opened.
2. **iPhone from Safari — MANUAL / CRITICAL**: open the promotion URL in Safari
   on a real iPhone or iPad, accept the iOS “Открыть в MAX” prompt when shown,
   and confirm the Mini App then opens the intended target channel. Mark
   `VERIFIED_WORKING` only after the target channel is visibly opened.
3. **Android — MANUAL / CRITICAL / BLOCKED**: keep this case `BLOCKED` until a
   real Android E2E is performed. The expected branch is
   `window.location.replace(target_url)`; do not infer PASS from source checks
   or iPhone evidence.

If either iPhone check fails, the release is `BLOCKED`/`NO_GO`; restore the
last known-good handoff before trying another handoff variant. Attach only
sanitized evidence (timestamp, device path, HTTP status, final result) to the
Dev handoff.

## Test-case registry

Before a GO, QA audits `qa/TEST_CASES.md`: affected cases, new cases, automation mode, and regression membership. The registry includes the critical set and manual cases that cannot yet be automated. The release status records total, automated, manual, critical, executed, passed, failed, blocked, and newly added case counts. A missing case for new functionality or a bug fix is a NO_GO.
