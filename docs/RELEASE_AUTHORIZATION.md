# Release authorization modes

## OWNER release task — full automatic cycle

Effective 2026-10-20 by direct OWNER instruction in MAXIBOT ME-45: use the
current GPoint V5 authorization model. An explicit OWNER task to release named
changes authorizes one complete canonical production cycle, including in-scope
technical fixes and replacement SHAs. Do not request another human approval at
PR, merge, Prepare, preflight or Deploy for that same task, scope and baseline.
A development-only request does not authorize publication or production.

Before new implementation, commit a schema-v3 durable record at
`.release-authorizations/<id>.json`. Bind the project and production environment,
unique authorization ID, direct OWNER instruction ID and human-readable task
reference, exact production baseline, deterministic current-main selection rule,
allowed paths and operations, acceptance criteria, exclusions, risk contracts,
and ACTIVE/revoked/consumed state. Record the canonical scope commitment hash;
this is an integrity check, not an OWNER signature. The operator must verify the
direct human instruction. Linear stores evidence and task context; it is not
queried to authorize release and a Linear URL is not required in the record.

The exact candidate is selected from reviewed current main after integration;
never pin it in the task record. The record must remain unchanged. Prior
prepared implementation is permitted only when every exact historical Git
commit and path is inventoried in `preauthorized_commits` and
`preauthorized_paths`, within the allowed scope. This includes explicitly
inventoried historical merge objects and their complete first-parent changes.
All new implementation commits must descend from the authorization commit.
Use normal merge preserving that history, never squash the authorization and
implementation together. Check the complete production-to-candidate diff.

The canonical path is integration → bounded quiet/coalescing → exact-main
Prepare → immutable GHCR images and OCI receipt/readback → authorization and
scope validation → existing Deploy with read-only preflight → Apply → runtime
verification → immutable GHCR evidence, GitHub Deployment and Linear.
Publication must be covered by the OWNER task or separate permission.

## Explicit risk contracts

Bounded low-risk tasks use `TASK_SCOPED_AUTO`. Database migrations and release
infrastructure require a matching explicit risk contract in the OWNER task and
durable record. Release infrastructure is no longer categorically excluded
and does not require repeated exact-SHA approval when that contract is valid.
The infrastructure contract names exact paths and baseline, permitted release
operations, required gates, risk description, backup and rollback plans, and
stop conditions. Without this contract the category remains excluded.

The existing database migration contract must name exact migration paths,
production DB head, target, required rehearsal/backup/rollback/health gates and
stop conditions. No migration is allowed merely because release infrastructure
is authorized. Security, credentials, permissions, payments, advertising/ADS
and paid-traffic changes remain excluded unless their own category contract is
implemented and reviewed. Unknown risk blocks. API polling stays false, only
the production worker polls, and ADS write flags stay off.

## Claims, retries and recovery

Keep one immutable activation claim per task authorization. A successful
exact-main Prepare may select an in-scope replacement candidate without another
OWNER approval after confirmed safe failure. Revalidate the full history,
current main, Prepare provenance, unchanged production baseline and live DB
head. An active Deploy blocks another dispatch. Immutable per-event guards
prevent duplicate events from dispatching twice.

An uncertain dispatch or partial Apply never permits blind retry. Reconcile
exact prior immutable evidence and read-only runtime first. A claim is never
rewritten to make a different candidate appear approved. A successful runtime
consumes the authorization once; a new unrelated production release requires
a new OWNER task. Revoked, consumed or ambiguous authorizations block Apply.
Publication failure after runtime completion uses metadata-only recovery of the
same Deployment, never a second Apply.

All exact SHA, clean-checkout, image/platform/parity, provenance, security,
preflight, lock, backup, migration, rollback, Worker, health and recovery gates
remain mandatory. Real-device/OWNER smoke is separate from technical release
completion. Publish exact identities, runs, timings and remaining smoke in
Linear; never invent E2E evidence.

## Exact-SHA fallback

When there is no valid OWNER full-cycle task, request approval for the exact
candidate SHA and exact Alembic target before production. This fallback must
not be imposed again on an already authorized in-scope automatic task.

## Runner readiness

Native GitHub queued state is an allowed wait for the trusted project Mac
runner. On start, verify runner identity/labels, OS/architecture, tools, disk,
cache/runtime paths, network and absence of conflicting production mutation.
Docker runs on the VPS through SSH, not on the Mac. Never use a browser to
coordinate Actions or add hosted polling/reruns to compensate for a queued job.
