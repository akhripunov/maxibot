# MAXIBOT Native First V3 + Kamal 2 migration

## Target

Retain the useful Native First V3 CI, tests, BuildKit cache, GHCR image publication, exact source SHA and digest, SBOM/provenance, frozen-path guard, and backup/restore checks. The ordinary workflow has three jobs: `tests` → `build` → `deploy`. The deploy job uses the GitHub `production` Environment and stock Kamal 2 commands. Do not add a release controller, custom authorization state, Compose deploy engine, or artifact transport for image metadata.

Kamal manages only API and Worker. API uses both the default `kamal` network and `maxibot-vps-frontend`; DB and Redis stay on `maxibot-vps-internal` and are additionally attached to `kamal` with `db`/`redis` aliases during the one-time cutover. Caddy stays on `maxibot-vps-frontend`. The Worker is the only MAX poller and stays capped at 512 MiB.

## Implemented locally

- Adapted `.github/workflows/maxibot-native-release.yml` in place; removed the duplicate Kamal workflow and artifact upload/download route.
- Preserved the full Python CI job and its frozen-path and isolated backup/restore gates.
- Build publishes a run/attempt-scoped GHCR tag with exact source labels, immutable digest output, BuildKit cache, SBOM, and provenance. Its summary prints the source SHA, run ID/attempt, and digest so OWNER can select the completed build.
- Deploy is manual, main-only, owner-actor gated, exact-current-main SHA bound, serialized, and waits at GitHub Environment `production`. Dispatch inputs identify the successful main Build run and its digest; Deploy checks run status, SHA, workflow path, GHCR tag/digest, labels, and platform. Its job does not depend on or rerun Build. There are exactly three jobs.
- Deploy takes a read-only production baseline, verifies backup/restore, refuses schema/frozen-policy changes, verifies the existing Kamal topology and API connectivity to DB, Redis, and Caddy, and then deploys only API and Worker through Kamal.
- Post-deploy assertions compare DB/Redis/Caddy identities and named volumes, verify API uploads volume, network reachability, health, Alembic head, Worker heartbeat, single poller, memory limit, and stopped legacy Compose API/Worker containers.
- Updated obsolete Native First deploy assertions to cover Kamal behavior.
- Drafted `docs/KAMAL_FIRST_CUTOVER.md` as a separate one-time stop-before-start procedure; its protected execution path and rollback rehearsal are still required before a production cutover.

## Verification and blockers

- Targeted Kamal and build-boundary tests: PASS (27 tests).
- Disposable real Docker network test: PASS; API reached PostgreSQL on `kamal`, Redis on `kamal`, and Caddy on `maxibot-vps-frontend` while DB/Redis retained `maxibot-vps-internal`.
- Pinned Kamal 2.12.0 CLI `config`: PASS; resolved image is `ghcr.io/akhripunov/maxibot-vps-api:<version>`.
- Pinned Kamal 2.12.0 role configuration: PASS; its effective API docker args include `--network kamal` and `--network maxibot-vps-frontend`, with the `api` alias on frontend.
- YAML parse and `bash -n`: PASS; exactly three jobs and 13 run steps.
- Full suite: NOT VERIFIED. System Python 3.9 cannot import this Python 3.12 project. Bundled Python 3.12 is available, but pytest/application dependencies are absent and package-index DNS is unavailable.
- GitHub Environment reviewer/self-review settings: NOT VERIFIED from available read-only interfaces.
- Production baseline, owner-managed credential rotation, production backup/restore, and Compose-to-Kamal cutover: NOT RUN. Do not change production until these gates are freshly verified and separately authorized.

## Remaining work

1. Obtain a working local Python 3.12 dependency set and run the full suite.
2. Complete and review the protected one-time Compose-to-Kamal cutover execution path and recovery rehearsal. Keep it outside the ordinary workflow.
3. Resolve the GitHub Environment reviewer configuration and OWNER's required credential-rotation gate in one consolidated OWNER decision if policy changes or approval are needed.
4. Do not retire legacy production release implementation until Kamal cutover and production verification succeed. Keep changes local and unpublished until explicitly authorized release preparation.
