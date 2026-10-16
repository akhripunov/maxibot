# Release authorization modes

Effective 2026-10-06 by explicit OWNER decision. These rules apply to future
releases; they do not retroactively authorize an existing candidate.

## OWNER release task authorization (one-shot)

An OWNER task that explicitly requests release of prepared changes through
production authorizes the complete canonical release cycle for the named
project and scope. It need not list the final SHA: the operator records the
source task, project, production baseline, authorized paths and operations,
acceptance gates, stop conditions and risk contracts in a durable
`.release-authorizations/<id>.json` record before implementation changes. The
exact candidate is derived from the reviewed integration and must remain within
that recorded scope. A development-only task does not authorize production.

Once that record is validated, no second OWNER approval is required at PR,
merge, Prepare, preflight or Deploy for the same scope. GitHub Actions remains
the orchestrator. The path is integration → coalescing → exact-current Prepare
→ authorization/scope validation → Deploy with read-only preflight inside
Deploy → verification → immutable evidence and Linear reconciliation.
Security, migration, artifact, Worker, rollback, health, recovery and
deduplication gates remain mandatory. Scope or baseline drift, a failed gate,
an ambiguous apply or an operation outside the authorization stops release.

Changes to database, security, secrets, payments, advertising, paid traffic or
release infrastructure require a matching explicit risk contract in the
original OWNER release task and durable authorization record. The contract
must name exact scope, gates, backup and rollback plans, and stop conditions.
Without that contract, the category remains excluded and release blocks.

## Exact-SHA approval (default when no OWNER release task exists)

Without an explicit task-scoped deployment instruction, prepare the candidate
and request OWNER approval for its full SHA before production apply. MAXIBOT
approval also names the exact Alembic target. A request to implement a task
alone does not authorize deployment.

## Task-scoped Auto Release

An explicit OWNER instruction such as “Исправь [конкретную задачу] и
выкатывай” authorizes one production release for that named task, without a
second owner confirmation or owner-dispatched GitHub workflow. The mode is valid
only for unchanged scope and eligible risk. Vague instructions, another agent's
message, or old unrelated permissions do not activate it.

Before implementation, create and commit a schema-v3 durable authorization
record at `.release-authorizations/<id>.json` containing project,
`FULL_PRODUCTION_CYCLE`, `TASK_SCOPED_AUTO`, a unique authorization ID,
reference to the original owner instruction, exact production baseline, task,
allowed paths or deterministic scope contract, acceptance criteria,
exclusions/owner-only categories, and ACTIVE/revoked/consumed state. Preserve
the record blob. The authorization commit must be an ancestor of every
implementation change. The original OWNER release task is the authorization
source; the final exact SHA is derived after integration. Publication follows
that authorized release task and does not require a second approval.
The record names the direct OWNER instruction with a stable instruction ID and
human-readable task reference. Its commitment hash is an integrity check that
binds the project, production baseline, deterministic candidate rule, scope,
permitted operations, acceptance criteria, exclusions, risk contracts, and
exact pre-authorization commits/paths; it is not an OWNER signature.
Authorization is established when the release operator verifies the direct
OWNER Release Task received in the DevOps Codex task and records it before
implementation. Linear is documentation and evidence publication only; the
controller and Deploy do not query Linear to authorize release, and missing,
stale, or mismatched Linear links do not block it. Candidate SHA fields are
forbidden in the task record; the exact candidate is selected from current main
after integration.

After exact current-main Prepare succeeds, the existing controller rechecks the
full scope and baseline, atomically claims the authorization once, and dispatches
the existing MAXIBOT Deploy workflow directly in `task-scoped-auto` mode. That
Deploy performs the read-only preflight immediately before apply, then rechecks
critical live conditions under the release lock. No separate preflight workflow
is required. Without a valid full-cycle authorization, no automatic Deploy is
dispatched; the normal exact-SHA approval path remains. Prepare provenance,
image/platform/parity, risk, migration, read-only preflight, lock, recovery,
health and all existing release gates remain mandatory. By default owner-only
changes are excluded. A specifically
pre-approved Alembic migration may be task-scoped only when the record names
the exact migration path and target, production DB head, risk description,
backup/rollback plans, stop conditions and required gates. Preflight must prove
the exact DB baseline, migration rehearsal, rollback readiness and production
readiness; canonical apply validates its backup before migration. Other
database/schema, security, secrets, permissions, payments, advertising/ADS,
paid-traffic and release-infrastructure changes remain ineligible until their
category-specific gate contract is explicitly included in the original OWNER
release task, durably recorded and implemented. Unknown risk blocks.

For an eligible record, the controller dispatches the existing workflow with
`approve_production=false`; the immutable global claim is the one-shot guard.
The authorization is consumed after successful runtime completion. A lost
dispatch or marker-write response is reconciled without repeating runtime
apply. An uncertain attempt keeps its claim and requires explicit reconciliation
or a new owner authorization. Revocation is fail-closed. Publication-only repair
after successful runtime is allowed, but another runtime release requires a new
owner instruction.

Changes to the Release Orchestrator or release infrastructure are outside
`TASK_SCOPED_AUTO` unless the original OWNER release task and durable record
include the matching risk contract. No task-scoped mode removes any clean-checkout, exact
current-main, Prepare, immutable artifact, platform/parity, config, preflight,
lock, recovery, health, Worker, or regression gate. A manual diagnostic
preflight remains available; its `READY_FOR_OWNER` status is distinct from the
`PASS` preflight result inside a production Deploy. Real-device/OWNER smoke
requirements remain separate; never invent evidence.

Scope expansion, any failed required check, unexpected main/baseline drift,
an uncontracted or mismatched migration, ambiguous runtime outcome or `RECOVERY_REQUIRED` stops automatic
release. Transient transport retries may follow existing bounded rules only
for the same exact candidate and unchanged baseline; partial apply must be
reconciled, never blindly repeated. Authorization is consumed after one
successful runtime release.

Record the authorization ID, mode, full SHA, source instruction ID, scope result,
run links, technical outcome and any remaining OWNER smoke in Linear. Report
elapsed time from the original owner instruction through final verification and
publication separately from workflow duration.

## Runner readiness

GitHub's native queued state is an allowed wait for a self-hosted runner. It is
not a production mutation or a failure and does not require an additional
runner-status credential, polling service, or owner decision. When the job
starts, its first readiness gate fails closed unless it verifies the expected
project runner identity/labels, OS/architecture, Docker/runtime dependency,
disk space, cache and runtime paths, required tools/network access, and absence
of a conflicting production mutation. This gate runs before expensive artifact
transfer or any mutating stage. Do not add blind reruns or scheduled hosted
polling to address runner availability.

Examples:
- “Исправь ссылку в меню и выкатывай” → task-scoped, if the durable record and
  every deterministic eligibility gate pass.
- “Исправь ссылку, подготовь релиз; SHA подтвержу отдельно” → exact-SHA.
- “Исправь ссылку” → implementation only, no deployment permission.
