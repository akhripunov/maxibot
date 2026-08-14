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

`RELEASE_STATUS.md` separates executed pytest counts (`TOTAL_TESTS`, `PASSED`, `FAILED`, `SKIPPED`) from registry counts (`TOTAL_TEST_CASES`, `AUTOMATED_CASES`, `MANUAL_CASES`, `SEMI_AUTOMATED_CASES`). `NEW_*_CASES` fields are calculated against the previous release report; they are not aliases for the total registry size.

Functional verification uses only four statuses: `VERIFIED_WORKING`, `WORKING_UNTESTED`, `BROKEN`, and `UNKNOWN`. Any `BROKEN_CRITICAL` or `UNKNOWN_CRITICAL` case forces `QA_VERDICT = NO_GO`, even when automated tests and staging smoke are green.

The current local staging stack uses `.env.staging`, API port `18000`, PostgreSQL port `55432`, Redis port `56379`, and named resources prefixed `maxibot_staging_`. It is independent from the production Compose project and is safe to remove with the matching QA Compose project only.

The test inventory and audit are documented in `qa/TEST_CASES.md`, `qa/TEST_AUDIT.md`, and the preservation manifest `qa/EXISTING_TESTS_MANIFEST.md`. The host Python version is not a valid release runtime unless it satisfies `pyproject.toml`; the Docker image is the authoritative runtime for the local gate.

## Test-case registry

Before a GO, QA audits `qa/TEST_CASES.md`: affected cases, new cases, automation mode, and regression membership. The registry includes the critical set and manual cases that cannot yet be automated. The release status records total, automated, manual, critical, executed, passed, failed, blocked, and newly added case counts. A missing case for new functionality or a bug fix is a NO_GO.
