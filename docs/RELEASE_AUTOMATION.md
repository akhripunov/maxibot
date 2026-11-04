# Текущая автоматическая схема — Native First V3

Событие `push` в `main` автоматически запускает **Tests → Build/GHCR → Kamal Deploy → Runtime Verify** для одного exact SHA. Контрольный успешный push-run: [#40634112830](https://github.com/akhripunov/maxibot/actions/runs/40634112830). Обычный релиз не требует отдельного `workflow_dispatch` или ручного Deploy.

## Pre-push optimization — scope-based fast path

The preparation phase ends at confirmed `main` push. It is distinct from
the automatically measured GitHub Actions duration after push.

1. Check current exact `origin/main` SHA, worktree status, diff scope and
   active conflicting releases once. Respect OWNER authorization: a push
   to `main` triggers production, including documentation-only pushes.
2. Read only task-relevant policy once; reuse it while its content and task
   scope are unchanged. On an actual GitHub/GHCR transport, DNS,
   TLS, authentication, authorization or permission error, read the current
   [Linear «GitHub доступ»](https://linear.app/bestuzhev/document/github-dostup-7970682d1b80)
   and follow its applicable diagnostic steps. Successful access needs no
   runbook read. Under the current MAXIBOT rule, FAST is limited to narrow
   non-policy documentation. Executable changes use FULL; a bounded ordinary
   code FAST category is only a proposal and requires OWNER approval before it
   becomes active. Reuse exact checks recorded in the existing SHA-bound DEV
   handoff when source, tests, dependencies, tools and conditions still match;
   do not rerun the full local suite just because DevOps received the same
   clean candidate. `AGENTS.md`, policy, security, attribution, DB/migrations,
   release/CI/Kamal, Worker/Poller/runtime and other protected paths use FULL.
3. Make minimal changes in an isolated worktree or branch if the current Mac
   checkout is stale/dirty. Never overwrite unrelated changes.
4. Run applicable scoped local checks. The hosted full Tests, Build/GHCR and
   every production gate remain mandatory. Reuse a local check only when the
   exact candidate tree, relevant tests, dependencies, tools and conditions
   match; a new SHA still crosses hosted CI. DEV transfers the existing
   SHA-bound handoff JSON and checker result; DevOps validates it against the
   live main and production diff rather than generating a duplicate handoff.
5. Use one existing authorized Git/GitHub publication path. On ambiguous API
   timeout, first read back commit SHA / branch ref / Actions run; never
   blindly retry writes. Publish to `main` only when production-triggering
   release is authorized, otherwise create a branch/PR.
6. For requested timing, record UTC `TASK_START → CODE_READY → PREPUSH_START →
   PREPUSH_PASS → FIRST_PUSH → PR_READY`. `FIRST_PUSH` is confirmed by remote
   branch readback; `PR_READY` is the opened PR with exact base/head, scoped
   diff and required checks started. Record PR check completion, merge and
   Actions start separately. Mark unavailable times `NOT_VERIFIED`; do not use
   commit date as push time. Separate editing, pre-push, queue/wait and hosted
   CI durations.

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

Build intentionally runs for every `main` push, including documentation-only
changes. The protected Deploy job determines app/schema actions from the live
production SHA and database head, and a DB-only migration still needs the exact
candidate image for its guarded migration path. Skipping Build before that
baseline is known would require a separate production-aware pre-Build decision
and a no-image Deploy branch. That adds a second decision path and weakens the
simple exact-SHA artifact flow to save one build; Build is therefore not
path-filtered.

### PR Tests reuse gate

The `tests` job remains mandatory for both PRs and every `main` push. Its one
full `python -m pytest -q` pass includes release-specific and migration
regressions. PR results are not reused on `main` yet: the active repository
ruleset read on 2026-11-04 protects `main` from deletion and non-fast-forward
only; it does not require PRs or successful status checks. The GitHub App could
not read classic branch-protection details (403), so no additional checks are
assumed. Until OWNER configures and enforces required PR/status checks, a direct
push can reach `main`; skipping its full suite would make PR CI an untrusted
condition. Keep the full main Tests job.

After the branch rule exists, reuse still must verify the exact merged PR/run,
completed non-skipped mandatory test jobs, matching tree, allowed merge
ancestry/base, unchanged test environment and all independent release-specific
guards. Any missing or mismatched proof falls back to the full suite. A tree
match alone is insufficient.

### Production concurrency and reconciliation

Concurrency is attached only to the production `deploy` job, not the workflow
or PR tests/build. PRs therefore run independently of a Deploy.
`cancel-in-progress: false` protects an active Apply and `queue: max` retains up
to 100 pending Deploy jobs. GitHub's queue is bounded and ordering is not
guaranteed, so each job rechecks that its SHA is still current `main` after it
gets the slot and again immediately before Kamal Apply; a newer `main` already
present at either gate makes the queued candidate fail before the next mutation.
A push arriving after an Apply has begun remains serialized behind it and runs
its own cumulative release. Every `main` push still runs Tests and Build. The
current-main candidate's production baseline step compares
the complete live-runtime-to-candidate delta, including prior commits. Thus a
reordered or superseded candidate cannot replace the newest state; it is
rejected as stale, and the current candidate's own run remains eligible.
If the 100-pending limit is exceeded, GitHub marks an excess run cancelled;
that is a visible release blocker, not success. Reconcile read-only and rerun
only the current exact-SHA deploy attempt after the group drains. A failed,
cancelled or uncertain Apply also requires readback/reconciliation before any
mutation; do not infer success from the queue or rerun blindly.

The repository pins actionlint 1.7.12, which predates GitHub's documented
`concurrency.queue` field. Its CI invocation ignores only that exact obsolete
unexpected-key diagnostic; workflow regression tests assert the complete
production concurrency block (`group`, `cancel-in-progress: false`, and
`queue: max`). All other actionlint findings remain blocking.

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

The release classifier tests exercise four baseline-to-candidate diffs:
app-only, DB-only, app+DB and infra-only. They verify the expected schema and
Kamal decisions. The PostgreSQL rehearsal separately applies the real 0098→0099
migration to an isolated PostgreSQL service with populated data, validates the
schema and data digests, and checks compatibility with both baseline and
candidate ORM models. The protected production Runtime Verify compares API and
Worker container IDs and `StartedAt` when Kamal is skipped. The full pytest suite
also contains `tests/release/test_release_modes_e2e.py`: on the hosted runner it
applies the real migration to two disposable PostgreSQL databases and checks
controlled API/Worker container IDs and `StartedAt` for DB-only, then verifies
the App+DB container transition only after the target schema is reached. This
is an isolated Docker integration check, not a Kamal CLI deployment; its hosted
result is required before claiming the current candidate passed. No synthetic
migration is applied to production.

The exact step durations for an actual release are available from native GitHub
Actions timestamps and summary; isolated unit tests do not claim production
stage timings.

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

GitHub Actions summaries and the Linear Release Journal use the same fields:
`workflow_sha`, `candidate_sha`, `actual_runtime_api_sha`,
`actual_runtime_worker_sha`, `build_run_id`, `image_digest`,
`app_deploy_required`, `schema_action`, and `runtime_verify_status`. The GitHub
Deployment `sha` (`ref`) identifies the runtime API SHA expected after the
operation: candidate SHA when API deploy runs, verified baseline API SHA when
it is skipped. Its payload separately records workflow/candidate SHA,
immutable digest, Build run ID, expected API/Worker SHA, app/schema actions and
`runtime_verify_status=PENDING`. After successful Runtime Verify, the final
Deployment status description and Actions summary record actual API/Worker
SHA and `runtime_verify_status=PASS`; failure records actual SHAs as
`NOT_VERIFIED` and the failed run status. Linear journal rows should copy these
values rather than treating a configuration-only workflow SHA as a new runtime
application.

Kamal health and runtime checks provide container-level evidence. A failed
step blocks all later mutation. After an uncertain API write or production
Apply, first read back the exact run/ref/deployment and reconcile live runtime;
never blindly repeat it.

Record exact SHA, run IDs, image digest, migration state, Kamal result, Runtime
Verify and any separate user/device smoke in the existing Release Journal and
Release Queue. Keep credentials and personal data out of logs and Linear.
