# Текущая автоматическая схема — Native First V3

Событие `push` в `main` автоматически запускает **Tests → Build/GHCR → Kamal Deploy → Runtime Verify** для одного exact SHA. Контрольный успешный push-run: [#40634112830](https://github.com/akhripunov/maxibot/actions/runs/40634112830). Обычный релиз не требует отдельного `workflow_dispatch` или ручного Deploy.

## Pre-push optimization — scope-based fast path

The preparation phase ends at confirmed `main` push. It is distinct from
the automatically measured GitHub Actions duration after push.

1. Check current exact `origin/main` SHA, worktree status, diff scope and
   active conflicting releases once. Respect OWNER authorization: a push
   to `main` triggers production, including documentation-only pushes.
2. Read only task-relevant policy. On an actual GitHub/GHCR transport, DNS,
   TLS, authentication, authorization or permission error, read the current
   [Linear «GitHub доступ»](https://linear.app/bestuzhev/document/github-dostup-7970682d1b80)
   and follow its applicable diagnostic steps. Successful access needs no
   runbook read. Use `docs/ORCHESTRATION_PROTOCOL.md` FAST only for plain,
   non-policy docs with no executable/protected impact;
   `AGENTS.md`, release policy, CI/Kamal and other protected paths are FULL.
3. Make minimal changes in an isolated worktree or branch if the current Mac
   checkout is stale/dirty. Never overwrite unrelated changes.
4. Run applicable scoped local checks; leave full hosted Tests/Build and all
   production gates intact. Do not duplicate the full local suite for
   unaffected non-policy docs unless a current policy explicitly requires it.
5. Use one existing authorized Git/GitHub publication path. On ambiguous API
   timeout, first read back commit SHA / branch ref / Actions run; never
   blindly retry writes. Publish to `main` only when production-triggering
   release is authorized, otherwise create a branch/PR.
6. Measure each pre-push checkpoint: task received (or NOT_VERIFIED),
   preflight complete, edit complete, checks complete, commit created, push
   confirmed by remote SHA, Actions run created. Do not equate commit date
   with push date. Report objective waits/failures separately.

Goal: reduce unnecessary pre-push work while retaining approval, frozen,
security, CI, GHCR and Kamal runtime gates. The prior 2–4 minute pre-push
target is unverified until measured across multiple comparable changes. On a
healthy access path this policy removes one unnecessary Linear runbook read;
its time saving is NOT_VERIFIED until measured. For requested benchmarks only,
record runbook reads and their wait separately from Git and release timings.

## Current Native Release

`.github/workflows/maxibot-native-release.yml` is the only routine production
release workflow. Its push-triggered path is hosted Tests → Build/GHCR → Kamal
Deploy → Runtime Verify. The Build publishes one immutable linux/amd64 image
with exact source labels, SBOM and provenance. Deploy selects that successful
Build by exact SHA and digest; it does not rebuild the image. The production
Environment remains the source of runtime secrets and branch restrictions.

The workflow uses stock Kamal 2 for API and Worker. It preserves the API/Worker
image identity, API polling off, one Worker/Poller, the 512 MiB Worker limit,
PostgreSQL and Redis health, persistent volumes, migrations and runtime health.
Only the first transition from Compose to Kamal was a one-time procedure; its
workflow and instructions have been retired after the transition. Routine
release and manual rollback use Native Release, never Compose Apply or an old
controller.

App-only releases do not run a database backup/restore rehearsal. A migration
runs only when the candidate diff and live Alembic state match the committed
migration risk contract. That path runs the isolated PostgreSQL rehearsal,
fresh custom-format backup, `pg_restore` validation, isolated restore and exact
schema/data checks before one guarded Alembic Apply. Kamal starts only after
migration Apply passes. Failed or uncertain Apply is reconciled read-only; no
automatic database downgrade or restore-over-production is permitted.

## Isolated release-mode acceptance

The workflow decision tests exercise three isolated candidate diffs before any
production release: (1) app-only: Tests and Build run, schema/backup are skipped,
Kamal replaces API and Worker, then Runtime Verify runs; (2) DB-only: Tests and
Build run, the authorized migration rehearsal and backup/restore gates precede
Schema Apply, Kamal is skipped, and Runtime Verify confirms the existing API and
Worker IDs/start times did not change; (3) app+DB: Tests and Build run, migration
gates and Schema Apply precede Kamal API/Worker replacement and Runtime Verify.
The exact step durations for an actual release are available from the native
GitHub Actions step/job timestamps and summary; isolated unit tests do not claim
production-stage timings. No synthetic production migration is run for this
acceptance.

Manual rollback is owner-authorized and uses the previous successful immutable
GHCR image with stock Kamal. It requires proven application/schema compatibility
and full post-rollback Runtime Verify. The workflow never auto-rolls back.

## Backup destination status

Bunny S3 is the approved target for independent persistent backups in Storage
Zone `maxibot-backups`. Migration of the existing backup process is not complete
until S3 API availability, zone region, retention/RPO/RTO, encryption, object
readback/checksum, and isolated restore drill are confirmed. Until that evidence
exists, preserve the current recovery copies and backup/restore gates. Do not
claim Bunny backups are active or remove existing copies based only on a
successful upload command.

## Failure handling and evidence

GitHub Actions logs and summaries are the release execution record; GitHub
Deployments record the exact application SHA and final status. Kamal health and
runtime checks provide container-level evidence. A failed step blocks all later
mutation. After an uncertain API write or production Apply, first read back the
exact run/ref/deployment and reconcile live runtime; never blindly repeat it.

Record exact SHA, run IDs, image digest, migration state, Kamal result, Runtime
Verify and any separate user/device smoke in the existing Release Journal and
Release Queue. Keep credentials and personal data out of logs and Linear.
