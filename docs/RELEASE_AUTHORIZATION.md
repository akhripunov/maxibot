# MAXIBOT release authorization

## Ordinary release

The only ordinary application/database release path is `.github/workflows/maxibot-native-release.yml`. A protected push to `main` runs hosted Tests → Build/GHCR → Kamal Deploy → Runtime Verify. PR workflows are checks-only.

Because a push to `main` can update production, publish only a clean exact candidate whose complete diff is covered by the OWNER's authorization for that task. Branch protection and the GitHub `production` Environment remain in force. The workflow must honor the repository's current Environment settings; do not remove or bypass required protection.

The workflow dispatch interface is limited to an OWNER-authorized rollback to a previously successful exact image. Routine deployment is push-triggered and never requires a separate dispatch.

## Migration authorization

For a schema change, the task authorization must identify the application baseline, source and target Alembic revisions, exact pending migration set, affected data, compatibility and recovery constraints. The current migration contract in `docs/release/migration-risk-contract.json` binds those checks to the approved paths and schema.

Deploy reads the live application and database state before mutation. It proceeds only when the source/target and pending set match the task contract, no competing release exists, and the backup plus isolated restore checks pass. Apply is forward-only. Never perform an automatic database downgrade or repeat an uncertain Apply without read-only reconciliation.

## Evidence and stop conditions

Use the GitHub run and GitHub Deployment for exact SHA, GHCR digest, CI, Apply and Runtime Verify facts. Record task authorization and factual outcomes in the existing Linear issue and release journal. Do not expose secrets or treat a process authorization as a cryptographic signature.

Stop on an unauthorized path, unexpected baseline or schema, digest mismatch, failed backup/restore, ambiguous partial migration, unhealthy service, or failed Worker/Poller invariant. Do not bypass branch, Environment, frozen-path, database, or runtime gates to produce a green result.

Historical V2/V5 and Compose controllers are retired; they are not fallback deployment entrypoints. Recovery is the exact compatible Kamal application rollback with the database left at its current revision, followed by a verified forward resume if required.
