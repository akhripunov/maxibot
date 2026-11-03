# MAXIBOT Native First V3 + Kamal 2

## Release path

`Push main → Tests → Build/GHCR → Kamal Deploy → Runtime Verify`

The sole release workflow is `.github/workflows/maxibot-native-release.yml` with three jobs:

1. **Tests** runs the full Python 3.12 suite, workflow/actionlint checks, frozen-path and security regressions, Kamal configuration validation, and migration checks on isolated PostgreSQL when the candidate includes an authorized migration.
2. **Build** builds once on a GitHub-hosted runner with BuildKit cache, publishes the run-scoped image to GHCR, and records exact SHA, immutable digest, linux/amd64, SBOM and provenance.
3. **Deploy** runs only after successful Tests and Build. It uses the `production` Environment, rechecks current main and production baselines, verifies the exact image digest and labels, and applies the already-published image through pinned Kamal 2 with `--skip-push`.

A normal app-only release does not apply Alembic. When the exact candidate requires an authorized additive migration, the same Deploy job verifies the live source revision and pending set, checks the approved schema contract, creates a fresh database backup, validates it with `pg_restore`, restores it into an isolated `--network none` container, applies Alembic once from the immutable image, then deploys API and Worker and verifies runtime.

## Production safety

Deploy preserves the existing PostgreSQL, Redis, uploads volumes and Docker networks. It checks the production API/Worker source SHA, database and Redis health, the expected Alembic head, GHCR digest/platform/source labels, one active MAX Poller, API polling disabled, Worker polling enabled, Worker memory limit, health endpoints and Worker heartbeat.

Failures stop the chain. Application rollback uses the exact previously successful image and never downgrades PostgreSQL. An uncertain Apply requires read-only reconciliation before any retry.

The GitHub `production` Environment and repository branch protections are authoritative; workflows must not bypass or silently edit them. Pull-request runs do not receive production secrets and do not deploy. `workflow_dispatch` is reserved for the explicitly authorized rollback, not routine releases.

## Evidence and timing

GitHub Actions is the source of run stage timestamps, test/build/deploy status and summaries. GitHub Deployments records the exact production SHA and digest. Use those timestamps for stage durations; mark unavailable stage timing `NOT_VERIFIED`. Linear holds task decisions and verified release evidence. No parallel release controller, custom telemetry database or manual release queue is maintained.

Historical Compose/V2/V5 procedures are archived only. Do not use them to deploy API or Worker.
