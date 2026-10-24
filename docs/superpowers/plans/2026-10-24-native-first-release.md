# MAXIBOT Native First Release Implementation Plan

> **For agentic workers:** use the approved OWNER specification and this plan; execute locally in the isolated checkout. Primary implementation is inline, with a fresh bounded reviewer. No publication or production authority is implied by plan completion.

**Goal:** replace release orchestration with one project-specific native GitHub Actions pipeline, retaining every necessary safety check.

**Architecture:** GitHub Actions → official Docker BuildKit/cache → GHCR immutable digests → OpenSSH → Compose V2 → independent Verify. No reusable workflow, shared release repository, custom controller, ledger, lock service or backup controller.

**Tech Stack:** GitHub Environments/Deployments/concurrency, pinned official Actions, Docker/Compose, OpenSSH, existing project tests and minimal project safety checks.

**Spec:** ../specs/2026-10-24-native-first-release.md (OWNER text with trailing whitespace normalized; original attachment SHA256 `82aba5fa2adf6ccfe046e4307ba61c9d489fa82d83cd05bcf6bd9244339c4e62`). The OWNER addendum below overrides its shared-VPS assumption.

**Canonical task:** ME-51. Handoff/evidence goes in this existing Linear issue and the active Release Queue; this document is a plan, not a second handoff registry.

**Local base:** `50335ab097ad49e8833120f9f5000c855b1acbc0`. Unrelated dirty primary checkouts are excluded.

## OWNER addendum — mandatory

- Projects use different VPS. Use independent repository concurrency. Do not create a cross-project flock without a proven shared resource. Add host-local flock only for a demonstrated conflict on that host, held for the complete conflicting operation.
- GSales services intentionally stopped stay stopped unless their explicit start is authorized. Record names/status/reason, select exact services, use --no-deps where proven safe, and independently verify the stopped services after release. No blanket GPoint compose up and no stopped-service state manager.
- Check other backup locations/storage/freshness. Before risky PostgreSQL changes, require successful restore in an isolated environment plus an applicable current backup/risk contract. No irreversible/incompatible migration until recovery is proven.
- Continue safe local implementation. Only affected protected operations block. Do not add a lock/backup/state controller.

## Scope and authority

Local implementation/test/review/commits are authorized. OWNER selected ROLE=DEVOPS in this chat after the initial DEV selection. GitHub publication, hosted runs, new secrets/access permissions, production cutover, database/ACL mutation and starting GSales are not authorized by the current local-only scope. Existing V5 remains active until a separately authorized verified cutover. Completed local work is LOCAL_PENDING_RELEASE, not COMPLETE/READY_FOR_RELEASE.

## Phase A evidence — observed on 24 October 2026

- Published main and runtime: `50335ab097ad49e8833120f9f5000c855b1acbc0`.
- VPS: `135.106.166.46`; API ready; exactly one worker container observed. Functional poller proof is still separate.
- Installed GHCR image: `ghcr.io/akhripunov/maxibot-vps-api@sha256:2bf4861707829125946d490cb73ca6a9e566975aa7a167ceef6bad21a4f8c826`.
- Alembic: `0098_durable_autopilot_execution_claim_v1`.
- Latest observed backup: 2026-10-24 14:20 UTC, 70,519,450 bytes. A listing is not a restore test.
- Docker Compose `2.40.3`; `flock` exists. No shared VPS with GPoint.
- Baseline full suite: 1640 passed, 1 skipped, 42 subtests, 86.24 seconds.
- Deploy 40100349556: 13:57:16–14:29:52 UTC = 32m36s workflow duration. Mac deploy job 28m16s; setup step 1m43s, provenance recheck 4m34s, receipt resolution 2m08s, preflight/apply 15m29s. These stage timestamps do not separate Docker Pull from Verify.
- Seven active workflow files exist, of which five are release related. The Telegram executor and instruction-sync workflows are outside automatic deletion scope.

Latest-eight-run API snapshots showed terminal runs. An exhaustive queued/in_progress check was incomplete due intermittent TLS timeouts; absence of all active operations is NOT_VERIFIED and must be rechecked before any cutover. GitHub connector reads and SSH Git fetch worked independently of intermittent CLI HTTPS timeouts. No network/TLS/credential settings were changed.

## Review Focus

- Candidate/main or production baseline drift must stop mutation; include the complete unreleased diff and unresolved operations, not just this task's commit.
- Dispatch inputs must never become arbitrary shell/Dockerfile/service paths; official action pins and job permissions stay explicit.
- UI-only work must not rebuild backend Docker images; common dependency changes must still run all necessary gates.
- A stopped service, Mac Worker or sole MAX poller must not be accidentally started/replaced by generic Compose commands.
- Restore of an old dump does not prove current backup coverage, roles/ACLs or compatibility after migrations; failed Verify is not successful Deploy.

## Task 1 — audit and gate inventory

- [x] Record clean task base, preserve primary checkout changes, read current shared rules/active Queue/journals and exact source contracts.
- [x] Read production runtime/containers and existing backup inventory without mutation; identify stopped-GSales contract and separate VPS.
- [ ] Complete full active-run/history reconciliation, migration heads/rollback identity, external backup storage/restore scope and remaining security gate inventory before declaring Phase A complete.
- [ ] Measure each existing suite/stage separately; use existing run telemetry instead of creating a telemetry service. Do not label combined stages as separately measured.

## Task 2 — dormant native CI/build implementation (current local slice)

**Files:** `.github/native-release/ci-build.yml`, `tests/test_native_build_boundary.py`; GPoint additionally `infra/production/Miniapp.Dockerfile`.

**Consumes:** exact source SHA selected by OWNER dispatch, existing dependency files and tests. **Produces:** CI_BUILD_ONLY image digest(s) in native job summaries/step outputs; no deployment approval.

- [x] Write security-boundary tests first; observe missing-candidate failures (8 per project).
- [x] Implement OWNER/current-main guards, read-only test jobs and isolated hosted package-publish jobs; use official immutable action pins, pip/pnpm caches, BuildKit type=gha and direct GHCR pushes.
- [x] Retain full existing MAXIBOT pytest invocation and GPoint workspace/type/release/root checks. These build templates do not replace all canonical disposable DB/image/Eros/Playwright gates yet.
- [x] Provide separate Mini App static image containing /dist; include its actual shared contracts, compile no backend, and never run an application process in that image.
- [x] Verify unit boundaries and actionlint. Keep candidates outside .github/workflows so no second pipeline can execute.
- [ ] Prove each native Docker build, GHCR digest/run provenance and warm-cache behavior in an authorized isolated hosted rehearsal. A local build cannot prove GitHub cache or registry publication.

## Task 3 — complete mandatory CI/image eligibility before activation

**Files to change:** staged ci-build.yml; existing tests/rehearsals under scripts/ and tests/ only where necessary to separate safety from obsolete transport. Do not duplicate application test logic.

- [ ] Map every canonical Prepare gate to a native job/step and retain test/migration/image/ACL/Eros/GSales/Worker/security checks. Do not treat the current CI_BUILD_ONLY templates as that mapping.
- [ ] Run independent test groups as native jobs; use simple reviewed git diff/component boundaries and full fallback for shared dependencies. Do not introduce an impact-analysis framework.
- [ ] Exercise exact built images against isolated PostgreSQL/Redis and migrations; retain MAX linux/amd64/API-worker parity, single poller safety and frozen-path guard. Required real-device smoke remains separate.
- [ ] Retain required GPoint Playwright flows, AGATA/GSales PostgreSQL/ACL tests, Eros integration and Mac Worker compatibility. Keep intentionally stopped services stopped; testing a disposable GSales service does not authorize starting production GSales.
- [ ] Prove failure prevents image eligibility/deploy and that untrusted PR code cannot receive production/package-write credentials.

## Task 4 — native authorization, deploy and Verify

**Files:** final `.github/workflows/release.yml` assembled from the staged candidate; only existing necessary safety helpers or small stateless checks may remain. No new orchestrator/state service.

- [ ] Express one explicit dispatch-bound project/SHA/scope/baseline/risk contract through native workflow inputs and existing authorization checks. Preserve revoked/consumed/unknown-history handling; agree and review any change in how durable authorization consumption is evidenced before replacing it. Native Deployments track installation, not OWNER authority.
- [ ] Verify current main, exact digests, successful build/run provenance, platform, baseline/live DB heads and absence of conflicting Apply immediately before mutation. Use native Environment production and concurrency cancel-in-progress:false. Do not silently remove existing Environment protection.
- [ ] Configure separately authorized least-privilege SSH/host key access; use standard OpenSSH and registry pull by digest. No production build, archive transport, mutable latest or secrets in logs.
- [ ] Use explicit Compose services and preserve volumes/networks/Caddy/data. MAX: stop/replace the sole poller in an order that never creates a second poller, verify API/worker parity and functional heartbeat. GPoint: preserve all three stopped GSales services; do not couple Mini App switch to backend rebuild or installed Mac Worker.
- [ ] Run native health/status/digest/SHA/DB and business smoke checks. Fail the deployment job on failed Verify. Reconcile uncertain Apply read-only; never blind-retry. Roll back an image only when schema/runtime compatibility is proven; a PostgreSQL rollback is a separate protected operation.

## Task 5 — backups and Operate

- [ ] Finish inventory of existing cron/remote backup storage and retention; do not create a new backup system merely because one location is stale.
- [ ] Before a risky migration, require a recent scope-correct backup, successful isolated restore including required extensions/roles/ACLs, tested recovery command and compatibility contract. Existing old-dump restore is useful evidence, not permission to mutate production.
- [ ] Verify restart policy, existing availability/Worker monitoring, alerts, logging and resource retention. Report unknown offsite/recovery facts explicitly.

## Task 6 — authorized sequential cutover

- [ ] Recommend MAXIBOT first because observed production equals main. Freeze a fresh baseline and independently reviewed clean exact candidate. Obtain only the genuinely missing operation/risk authorizations; do not ask again within a valid complete authorized cycle.
- [ ] GPoint cutover must exclude or separately authorize the 125-path pending Eros/DB/Worker scope. Do not silently roll main back, release those migrations or reinterpret this infrastructure task as ME-36 authorization.
- [ ] Rehearse new pipeline isolated: cache, GHCR digest, host-key failure, service selection, lock lifetime where needed, rollback/failure and unchanged-neighbor state. Keep legacy executable mechanism until this evidence exists.
- [ ] In the authorized window, use native GitHub workflow-disable controls for legacy dispatch/controllers before activating the new path; ensure no old run can still Apply. Disable is reversible and is not premature source deletion. Switch one project, Verify, then consider the second.
- [ ] Only after live Verify PASS update native Deployment/issue/Queue with exact identities. Local-only scope currently blocks this phase, not Tasks 2–3.

## Task 7 — legacy retirement after verified project cutover

- [ ] Inventory exact references before deletion. Candidate MAX legacy workflows: maxibot-prepare.yml, maxibot-auto-preflight.yml, maxibot-deploy.yml, maxibot-release-diagnostics.yml, maxibot-linear-reconcile.yml. Candidate GPoint: gpoint-prepare.yml, gpoint-auto-preflight.yml, gpoint-deploy.yml, gpoint-recover-interrupted.yml, gpoint-linear-reconcile.yml.
- [ ] Remove only orchestration/transport code no longer called: coalescing/controllers/dispatch/recovery wrappers, Docker save/load archive transport, obsolete release-state clients and Mac deployment-runner wiring. Preserve useful safety helpers/tests/audit history and the actual Mac product Worker. Names containing release/preflight alone are not deletion evidence.
- [ ] Use rg across workflows, scripts, package scripts, tests, docs and role instructions; every retained executable dependency must be accounted for. Update instructions with OWNER-scoped authority; do not loosen production checks.
- [ ] Verify native end-to-end execution again, report exact removed files and measured before/after timings. Only then mark COMPLETE for that project; global COMPLETE requires both projects.

## Current result

CI_BUILD_ONLY local slice; migration/cutover/removal are not complete. No active workflow, production service, database, secret, runner or network setting was changed. Final verification, clean commit/base, independent review and scoped handoff-gate evidence belong in ME-51; no claim that a scoped checker attests to production readiness.

## Local review and verification ledger

- Static boundary tests: RED on missing candidate, GREEN after implementation. Added an actual shared-contracts source assertion for Mini App; RED→GREEN after adding its COPY.
- Fresh bounded independent reviewer found one P2: staged dependency installs omitted the existing patched pip bootstrap. Added ordering regression (RED), restored pip==26.2.2 before installs, verified GREEN (9 checks per repo). No critical finding. Reviewer smoke-tested both templates; this is not canonical QA or a production review.
- Minor deferred: whole-file static boundary assertions do not individually attest each parsed privileged job or verify action repository allowlists. Treat them as smoke checks alongside actionlint, explicit review and future hosted/production gates.
- Both staged templates pass actionlint v1.7.12; shell blocks are additionally syntax-checked locally. No hosted cache/publication/deployment result is claimed.
- GPoint independent Mini App image built locally for linux/amd64. Its static index contains the supplied build-SHA marker; no application process was started. Final source-bound identity is recorded in the task issue after the clean commit build.
- Legacy workflows/controllers and runtime state remain unchanged. The pending tasks above are real remaining migration work, not completed acceptance criteria.
