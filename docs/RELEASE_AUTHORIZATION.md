# MAXIBOT production release authorization

## Ordinary release

The single production path is `.github/workflows/maxibot-native-release.yml`.
A `push` to `main` runs hosted Tests → Build/GHCR → Kamal Deploy → Runtime
Verify for that exact SHA. The `production` Environment retains its secrets and
branch restrictions; routine deploy does not require a manual reviewer. A task
that asks for development only does not authorize publication to `main`, since
that push starts a production release. The OWNER must authorize the named scope
and the release it triggers.

There is no Prepare/Deploy controller, claim/consume state machine, Compose
deploy engine, or TASK_SCOPED_AUTO path in the ordinary release. No routine
`workflow_dispatch` is used. Never force-push, blindly retry an uncertain
Apply, bypass branch protection, or use another project's authorization.

## Required release gates

- Candidate SHA is exact and comes from the successful `main` run. Tests and
  Build must pass before Deploy; Deploy consumes the immutable GHCR digest and
  verifies `linux/amd64`, source revision, API/Worker image parity, SBOM and
  provenance.
- The `production` Environment remains in use with its secrets and branch
  restrictions. The workflow fails closed on missing authorization context,
  image identity mismatch, frozen-path failure, unavailable dependencies, or
  runtime verification failure.
- Kamal 2 manages API/Worker. API polling stays disabled; exactly one Worker
  polls MAX. Worker memory remains capped at 512 MiB. PostgreSQL, Redis, their
  volumes, uploads and data identity are checked before and after deployment.
- Alembic is the migration authority. A migration requires a path- and
  baseline-bound risk contract, passing hosted rehearsal, fresh backup and
  isolated restore evidence, and one guarded Apply before Kamal. Application
  rollback never downgrades PostgreSQL automatically.
- Runtime Verify must confirm the deployed SHA, API health, Worker heartbeat
  and singleton, database/cache health, data volumes, and configured HTTPS
  endpoints. A failed or uncertain Apply requires read-only reconciliation
  before any recovery operation.

## Rollback

Manual rollback is a separate owner-authorized operation through the same
Native Release workflow. It must select a previously successful immutable
image, verify database/schema compatibility and the one-Worker invariant, then
use stock Kamal rollback and the full Runtime Verify. No automatic rollback is
performed. If database compatibility cannot be proven, stop and reconcile; do
not downgrade Alembic or restore over the live database.

## Migration authorization record

The committed migration risk contract describes the approved source/target
revision, scope, rehearsal, backup/restore, recovery plan and stop conditions.
The workflow validates the contract against the candidate diff and live
Alembic head. A contract does not authorize unrelated paths or a different
production baseline. A changed baseline, partial schema state, or changed
scope blocks Apply for a new owner decision.

## Evidence

Record exact SHA, GitHub run, immutable digest, migration result, Kamal result,
Runtime Verify and any user-facing smoke status in the existing MAXIBOT Release
Journal and Release Queue. Do not record missing evidence as PASS. Never put
credentials, user data or raw production configuration in GitHub logs or Linear.
