# MAXIBOT Metrica Reconciliation 0099 — scoped risk contract

Status: OWNER-authorized for one release scope; this document is not evidence that a gate has passed.

## Fixed transition

- Repository: `akhripunov/maxibot`
- Production source baseline: `eda64bd2d0118009853e4e06f0a9c888ad19fd5a`
- Required starting database head: `0098_durable_autopilot_execution_claim_v1`
- Sole migration file: `alembic/versions/0099_metrica_upload_reconciliation_v1.py`
- Sole target head: `0099_metrica_upload_reconciliation_v1`
- Authorization source: direct OWNER instruction “Metrica Reconciliation V1 + Migration 0099” for PR #62.

The release workflow must require the exact source baseline, exact starting head, exact approved diff path set, passing PostgreSQL rehearsal, fresh backup plus isolated restore, exact artifact identity, and an exclusive production release. The migration remains blocked for every other baseline, migration, or diff. This is a process authorization and is not represented as a cryptographic OWNER signature.

The exact one-time candidate path set is:

- `.github/actionlint.yaml` — registers the existing `maxibot-production` self-hosted runner label for actionlint validation.
- `.github/workflows/maxibot-native-release.yml`
- `alembic/versions/0099_metrica_upload_reconciliation_v1.py`
- `app/bot/menu.py`
- `app/db/models.py`
- `app/integrations/yandex_metrica.py`
- `app/traffic/metrica_upload_tracker.py`
- `docs/release/metrica-0099-risk-contract.md`
- `scripts/postgres_migration_rehearsal.py`
- `tests/release/test_kamal_release_workflow.py`
- `tests/test_metrica_upload_tracker_reconciliation.py`
- `tests/test_postgres_migration_rehearsal.py`
- `tests/test_release_prod_tooling.py`
- `tests/test_yandex_metrica_upload_status_logging.py`

## Permitted schema effects

On `offline_conversion_queue`, add the six 0099 reconciliation fields and the non-unique `(status, metrica_upload_next_check_at)` index. Existing queue rows and conversion fields must remain unchanged. No conversion POST is part of migration or release.

Lock acquisition for additive columns is bounded to five seconds. The index is created concurrently. If a lock timeout or index operation fails, do not deploy the new application or downgrade the database. Leave the prior application running, read the exact schema and Alembic state, and repair forward only under this same exact authorization after identifying a partial 0099 state.

## Backup and recovery

Before migration, create a new custom-format PostgreSQL backup, validate it with `pg_restore --list`, and restore it into a disposable disk-backed PostgreSQL instance using `--network none`. Compare the restored Alembic head, schema/table inventory, extensions, and conversion queue row count with production. Preserve the backup and Compose recovery baseline.

If migration or deployment fails, do not downgrade or restore over the live database. A retry is eligible only while the application SHA remains the fixed baseline and Alembic remains 0098, after read-only inspection proves that any already-created 0099 columns have the exact declared types/nullability/default, any same-name polling index has only the expected two columns and is either valid or invalid/not-ready, and no concurrent index build is active. The migration is explicitly resumable from those enumerated partial states and the normal workflow requires a new backup/isolated restore before retry. Any other partial schema blocks and requires OWNER review. Application rollback does not roll back the schema.

The isolated PostgreSQL rehearsal seeds 1,000 foreign-key-valid visit, membership, and queue rows with varied subscribe/unsubscribe and upload states. It hashes every pre-existing queue column before and after migration and verifies that both the exact production-baseline ORM model and the candidate ORM model can load the rows at 0099. This is compatibility evidence, not a substitute for the production backup and isolated restore gates.

If the application deploy fails after the database reaches 0099, the approved recovery is application-only Kamal rollback to the exact previous production SHA `eda64bd2d0118009853e4e06f0a9c888ad19fd5a`. The workflow must verify the database remains at 0099 and must not run Alembic downgrade or restore over production. This narrow rollback is eligible only when API and Worker are each on either that rollback SHA or the single current release SHA; any third SHA or other DB head blocks recovery. Leave the additive 0099 schema in place and use a forward-compatible application recovery path; destructive schema rollback requires a separate OWNER decision.

After that exact application rollback, a forward resume is permitted only when production application SHA is again exactly `eda64bd2d0118009853e4e06f0a9c888ad19fd5a`, DB head is exactly 0099, all six columns match their exact declared types/lengths/nullability/default, and the polling index is exactly `(status, metrica_upload_next_check_at)` with both `indisvalid` and `indisready` true. It must still pass the same exact candidate-path and frozen-path checks and create a new backup plus successful isolated restore immediately before application deployment. The resume path verifies the schema and data and skips `alembic upgrade`; it does not repeat DDL or downgrade. Any other state remains blocked for OWNER review.

## Stop conditions

- Production source SHA is not the fixed baseline, database head is not 0098, or the current queue schema differs from the expected 0098 shape.
- Any changed path is outside the one-time workflow allowlist or any additional migration is present.
- Tests, attribution/security regressions, independent QA, PostgreSQL rehearsal, backup validation, isolated restore, image identity, or Worker compatibility fails.
- A release/migration is already active, a migration result is ambiguous, or post-deploy DB head/runtime SHA is not exact.

No condition in this contract authorizes mass status repair, historic counter attribution, repeated offline-conversion POSTs, destructive downgrade, or recovery over the working database.
