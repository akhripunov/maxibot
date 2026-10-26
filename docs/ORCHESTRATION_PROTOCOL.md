# MAXIBOT orchestration protocol V1

## Canonical hierarchy

```text
OWNER
  ↓
ARCHITECT / ARCHITECT SOL (external ChatGPT Product/System Architect)
  ↓
MASTER TASK
  ↓
DEV (primary autonomous technical implementer and task owner)
  ├── optional DEV specialists
  ├── WEBDEV
  ├── ДИРЕКТОЛОГ
  ├── QA / ТЕСТИРОВЩИК
  └── SYSADMIN
  ↓
DEV FINAL REPORT
  ↓
ARCHITECT / ARCHITECT SOL and OWNER
```

ARCHITECT / ARCHITECT SOL defines product intent, desired behavior, business rules, system constraints, and acceptance criteria. DEV owns routine technical decisions, implementation approach, task state, integration, and technical closeout inside the approved scope. There is no separate ARCHITECT CODEX role or chat prerequisite. DEV proceeds without a separate ARCHITECT CODEX chat and escalates only for a real product or architecture conflict, material scope/behavior change, or protected authorization.

`ARCHITECT SOL` is a compatibility alias for the external ARCHITECT.

## Minimum required roles / FAST_PATH

DEV selects the shortest safe execution route. The default is one
primary implementer; additional roles are added only for actual domain scope,
real blockers, or mandatory independent validation. Existing roles must not be
involved ceremonially. Narrow direct specialist handoffs are allowed.

DEV remains the single window and task-state owner, but need not relay every narrow specialist message. QA is advisory for release readiness.
SYSADMIN is not mandatory for every deployment;
deterministic automated infrastructure guards are preferred for routine
releases, with human SYSADMIN involvement reserved for infrastructure risks,
incidents, secrets/runtime injection, or blockers.

## MASTER TASK contract

Treat a MASTER TASK as the source of truth for WHAT: desired behavior, business
logic, constraints, MUST/MUST NOT rules, safety, and acceptance criteria. Do
not treat filenames, classes, functions, tables, migrations, abstractions, or
other implementation assumptions as facts until the repository is checked.

## Mandatory repository preflight

Before any substantial task, DEV records:

- project root, Git repository status, branch, HEAD, and working tree;
- applicable `AGENTS.md` files and architecture/configuration;
- existing implementation, abstractions, handlers, services, and data flows;
- relevant tests, migrations, and runtime configuration;
- production, security, secret, backup, rollback, and write-safety constraints.

If Git or another required fact cannot be verified, record `UNVERIFIED` or
`BLOCKED`; never convert missing evidence into PASS.

For this workspace the verified canonical roots are:

- `CANONICAL_PROJECT_ROOT=/Users/akhripuniov/Downloads/Codex/MAXIBOT`
- `CANONICAL_GIT_ROOT=/Users/akhripuniov/Downloads/Codex/MAXIBOT`

Every future code MASTER TASK must record the pre-task `git status --short`,
branch, HEAD (or unborn HEAD), and the pre-existing diff/state before assigning
implementation work. Task changes must be reviewed against that baseline.

## Mandatory GitHub/GHCR access preflight

Before touching GitHub/GHCR or asserting that access is blocked, load the current [Linear «GitHub доступ»](https://linear.app/bestuzhev/document/github-dostup-7970682d1b80) through the available Linear integration, then apply only the steps compatible with the current MAXIBOT repository, existing credential scope, and granted permissions. Also load it again after context compaction if its actual contents are no longer present, or immediately after a new GitHub authentication/transport failure.

Maintain these distinct evidence states:

- `GITHUB_ACCESS_RUNBOOK_READ`: current document content successfully retrieved and applicable steps identified.
- `GITHUB_ACCESS_RUNBOOK_NOT_VERIFIED`: content could not be retrieved; record the exact source/tool error and do not pretend a URL or remembered excerpt is a read.
- `GITHUB_ACCESS_BLOCKED`: only after applicable safe diagnostics and bounded attempts from the live runbook were executed (or were impossible for a stated reason), and remote exact-SHA / Actions readback has ruled out a successful ambiguous write.

First classify the error: DNS, host/VPN/proxy routing, TLS/timeout, GitHub API permissions, or proven credential rejection. `gh auth status` failing while the network is unhealthy is **not** proof that the credential was revoked. Preserve valid existing Mac Keychain/CLI credentials; use the documented authorized route and retries. Before repeating a possibly successful push or workflow dispatch, read remote SHA and existing runs. No forced pushes, replacement tokens, relaxed SSH/TLS checking, or VPN/DNS configuration changes without separately authorized scope. Record which live-runbook checks passed, failed, or could not be run. Never reuse another project's credentials or approvals.

## Reuse before create

Before creating a service, model, table, worker, handler, pipeline, tracking
mechanism, configuration, or test abstraction, search for and reuse or extend
the existing implementation. Do not create parallel architecture without an
explicit Architect decision and recorded reason.

## Autonomous Codex execution

Once OWNER or ARCHITECT / ARCHITECT SOL has approved a task's scope and acceptance criteria,
DEV or the assigned implementer carries it through implementation,
tests, review, and fixes. The agent resolves ordinary reversible technical
choices, answers routine questions from specialists, and does not ask OWNER to
re-approve decisions already made. It continues until acceptance passes or a
specific protected authorization, missing evidence, external dependency, or
product/architecture decision objectively blocks progress.

One primary implementer is the default. DEV may use bounded subagents when materially useful and available; they do not substitute for required independent QA. DEV owns technical decisions within scope, integration, and verification. No separate architect chat is required.

This autonomy does not grant new permission to deploy, change paid traffic,
write to external providers, alter secrets/access controls, bypass frozen-path
or QA gates, or expand an approved task. Existing owner-only and release gates
remain controlling.

An explicit OWNER task authorizes only the minimum implementation within its
named scope, even when a task necessarily touches a protected path; the relevant
frozen-path regression and independent gates still apply. Implementation-only
requests do not authorize release. When the branch, worktree, HEAD, or baseline
changes, re-run preflight before reusing a blocker or previous environment
result. New direct OWNER instructions supersede unrelated unfinished task
context; complete already-authorized reversible work before asking about a new
decision.

## Explicit role initialization for new Codex chats

At the start of a specialized chat, select a role explicitly using a `ROLE=`
marker for one role in `docs/ROLE_REGISTRY.md`: `DEV`,
`WEBDEV`, `DIRECTOLOG`, `QA`, or `SYSADMIN`. Do not infer the role from chat
title, model, prior task history, or the person who opened the chat. With no
selected role, ordinary research and assistance are allowed, but role-specific
workflows and delegations do not start automatically.

After role selection, read this protocol, the role registry, the selected
role's applicable repository instructions, and only the relevant canonical
Linear context. Keep role-specific histories separate and keep the role for
the chat unless OWNER explicitly changes it. Recommend a new chat when
specialization changes; never silently repurpose a role chat. `ARCHITECT / ARCHITECT SOL`
is the external product/system architect; this bootstrap does not assign or
change that role.

The supported project default is `gpt-6-luna` with `medium` reasoning for the
primary Codex agent and subagents, configured in `.codex/config.toml`. Project
config is loaded only when the repository is trusted and sets defaults; verify
the active runtime model in a new chat rather than treating the file as proof.

## Negative authorization scenarios

- A request to edit or optimize an ad campaign without explicit action-specific
  OWNER authorization is blocked; no campaign/provider write is attempted.
- A change touching a frozen BOT/CHANNEL path without the required scoped
  approval and regression evidence is blocked; no guard bypass is accepted.
- A GPoint release approval presented for MAXIBOT (or vice versa) is rejected;
  fetch and apply only the target repository's own authorization and evidence.
- Production dispatch without the applicable exact-SHA or eligible,
  unconsumed `TASK_SCOPED_AUTO` authorization and `READY_FOR_OWNER` evidence is
  blocked before apply.
- Missing real-device mobile smoke remains `BLOCKED`/`NOT_VERIFIED`; a unit
  test or headless browser result cannot be reported as device acceptance.

## Minimum sufficient delegation and routing

| Task type | Role(s) | QA required | Deployment possible | Final owner |
|---|---|---:|---:|---|
| Architecture-only analysis | ARCHITECT / ARCHITECT SOL for product/system decisions; DEV for technical analysis | Usually no | No | DEV |
| Bot/backend/DB/API/worker | DEV | Yes | Gated | DEV |
| Site/landing/frontend/browser flow | WEBDEV | Yes when executable behavior changes | Gated | DEV |
| Direct/Metrika/attribution interpretation | ДИРЕКТОЛОГ | Depends on code impact | Advertising writes forbidden by default | DEV |
| Backend + web | DEV + WEBDEV | Yes | Gated | DEV |
| Backend + advertising/attribution | DEV + ДИРЕКТОЛОГ | Yes | Gated | DEV |
| Web + Metrika | WEBDEV + ДИРЕКТОЛОГ | Yes | Gated | DEV |
| Full attribution flow | DEV + WEBDEV + ДИРЕКТОЛОГ | Yes | Gated | DEV |
| Advertising runtime diagnosis | Existing Ads Codex Worker flow | Per existing runtime gate | No automatic advertising write | DEV for product decisions |

## Deployment handoff

Executable release flow is fixed:

```text
DEV exact SHA
  → deterministic tests + exact-SHA technical gates
  → predeploy snapshot/platform/source guard
  → canonical linux/amd64 build
  → SYSADMIN replacement
  → SYSADMIN postdeploy health/polling/errors/parity
  → CHANNEL regression
  → DEV final verdict
```

SYSADMIN may block or roll back a release but cannot approve architecture,
replace QA, own release decisions, or operate advertising campaigns. A failed
platform/source guard blocks replacement before any production container is
recreated.

Do not invoke every role automatically. Specialist roles submit proposals and
structured handoffs; DEV accepts, modifies, or rejects them.

## Frozen production path governance

`BOT` and `CHANNEL` are separate `PAID_TRAFFIC_CRITICAL`
`FROZEN_PRODUCTION_PATHS`. `AGENTS.md` is authoritative: the freeze is
behavioral, not only file-based, and `NO_REFACTOR_FOR_CLEANUP = YES`.
`OWNER_APPROVAL_REQUIRED_FOR_FROZEN_CHANGE = YES`; any direct or shared
dependency impact requires task-scoped approval and `FROZEN_PATH_REGRESSION`.
The machine-readable source is `docs/frozen_paths.yaml`, enforced by
`scripts/frozen_path_guard.sh` as `FROZEN_PATH_GUARD`. If the paths are
unchanged, `FAST_PATH_WHEN_UNCHANGED = YES` applies and no ceremonial role
work is added.

## Dynamic role-chat resolution

Role identity is logical; a Codex task/thread ID is replaceable routing state.
The following IDs are last-known observations only and are not permanent
bindings:

| Role | Last-known chat/task ID | Resolution mode |
|---|---|---|
| DEV | `01a065b2-55eb-7ec0-8e79-4fc19cbf84f5` | `DYNAMIC` |
| WEBDEV | `01a053ab-77b0-7dd0-9690-c036899ab6c5` | `DYNAMIC` |
| ДИРЕКТОЛОГ | `01a0349f-54bc-78c3-8a2f-c6cb70b3cc6f` | `DYNAMIC` |
| QA | `019ffb88-32db-76f1-bdc2-1373b29fca13` | `DYNAMIC` |
| SYSADMIN | `NONE` | `MANUAL_HANDOFF_REQUIRED` |

Before every specialist delegation, DEV must resolve the current
project chat for the requested role and compare it with the last-known ID. The
selection rule is: current MAXIBOT project membership, canonical role name or
marker, available/non-archived state, and the latest owner-established current
chat. If the selected ID changes, update the routing state before delegating.
If no match exists or matches are ambiguous, record
`MANUAL_HANDOFF_REQUIRED` for that specialist and continue independent work.
Do not auto-create a role chat or select a historical task only because its ID
is documented. DEV may use bounded technical subagents where supported; they
do not replace an independent QA gate. An explicit OWNER statement that a new
chat replaces a role is authoritative.

## Specialist handoff

Implementation roles return:

```text
ROLE =
TASK_ID =
STATUS = PASS / PARTIAL / BLOCKED / FAILED
WORK_COMPLETED =
FILES_CHANGED =
DATABASE =
CONFIG =
TESTS =
EVIDENCE =
RISKS =
DEVIATIONS_FROM_PLAN =
OPEN_QUESTIONS =
RECOMMENDATION_TO_DEV =
```

Consultative roles may replace `FILES_CHANGED` with `ANALYSIS` and return
`EVIDENCE` and `RECOMMENDATION`. A material product or system architecture change must return to ARCHITECT / ARCHITECT SOL before implementation; routine reversible technical choices remain with DEV.

## QA and integration review

### Current DEV ↔ QA direct handoff override

For the current release workflow, a narrow direct handoff is allowed: DEV sends
implementation candidates and receives the independent verdict directly in the
canonical QA thread
`019ffb88-32db-76f1-bdc2-1373b29fca13`. Do not create a new QA chat unless the
OWNER explicitly changes the canonical thread.

This direct QA handoff is the canonical independent validation path. DEV owns task state, integration review, exact candidate SHA, release readiness, and final technical reporting.

```text
ARCHITECT / ARCHITECT SOL → DEV → QA → DEV
```

`QA = NO_GO` returns findings directly to DEV; DEV fixes only the reported
issue and resubmits to QA. After `QA = GO`, the gated sequence is:

```text
QA → DEV → SYSADMIN predeploy → DEV deploy → SYSADMIN postdeploy
  → DEV final report → ARCHITECT / ARCHITECT SOL / OWNER
```

DEV may route a routine candidate directly to QA and receive its verdict, while
QA remains independent and returns its validation verdict. Commit, deployment, and release actions remain subject to canonical repository and OWNER gates.

### Cross-role lead implementation

For a task involving multiple implementation roles, ARCHITECT / ARCHITECT SOL may
name one `LEAD_IMPLEMENTER` only when needed; otherwise DEV remains lead. The
lead (`DEV`, `WEBDEV`, or another suitable specialist) records each role's scope and coordinates narrow specialist handoffs,
assembles the unified candidate, sends it to QA, and owns technical closeout.

DEV and WEBDEV may hand off directly to each other when the architecture is
already approved. Specialists may coordinate implementation but may not silently
redefine product behavior, core architecture, attribution semantics, or security
policy. A material mismatch must be returned as
`ARCHITECTURE_BLOCKER = YES` directly to external `ARCHITECT / ARCHITECT SOL`.

The canonical QA contract is defined by [QA_ROLE.md](../QA_ROLE.md),
[QA_PROCESS.md](../QA_PROCESS.md), [qa/TEST_CASES.md](../qa/TEST_CASES.md),
[RELEASE_STATUS.md](../RELEASE_STATUS.md), and
[scripts/qa_check.sh](../scripts/qa_check.sh). Do not create a parallel QA
protocol, registry, release format, or regression framework.

After implementation, the required flow for applicable code/runtime work is:

```text
IMPLEMENTER SELF-TEST
  ↓
DEV SELF-REVIEW
  ↓
QA → canonical QA process → QA_VERDICT = GO / NO_GO
  ↓
DEV FINAL REVIEW
  ↓
DEV_FINAL_REPORT
```

The canonical full QA command is `./scripts/qa_check.sh`. Applicable staging
checks use the isolated local Docker process specified in `QA_PROCESS.md`,
including separate app, PostgreSQL, volumes, `.env.staging`, ports, static,
unit, integration, regression, build, health, and E2E checks.

QA returns `GO`, `NO_GO`, `NOT_RUN`, or `TIMEOUT` as advisory information and
does not change `CANDIDATE_READY`, `RELEASE_READY`, or `DEPLOY_ALLOWED` by
itself. For candidate commit trees containing `scripts/release_policy.py`,
readiness comes from deterministic technical evidence: targeted tests, full
suite with zero failures, frozen-path guard where applicable, exact source SHA,
exact `linux/amd64` image, API/worker parity, platform preflight, migration
rehearsal when applicable, release dry-run, production rehearsal, and
post-deploy health/SHA readback. Trees predating that policy marker retain the
historical policy. Defects reproduced by mandatory deterministic gates remain
blockers. Bugs require regression coverage, and new functionality requires new
or updated canonical test cases. Preserve the existing TC-ID, classification,
priority, and critical-regression rules.

QA may repair QA tooling within the existing policy, but must not rewrite
application business logic to make a gate pass. If application changes are
needed, QA returns `NO_GO` or `BLOCKED` and DEV fixes the reported issue within the approved scope.

Production credentials are not used in staging by default. If a read-only check
cannot run without them, QA reports `PRODUCTION_CREDENTIAL_REQUIRED=YES` and
DEV chooses the safe method. Production writes from staging remain
forbidden without explicit owner authorization.

After handoffs, DEV reviews actual implementation, full diff when
Git is available, unexpected files, duplicate architecture, migrations,
schemas, tests, advisory QA findings, regression, secrets, auth, permissions,
destructive behavior, runtime configuration, production impact, rollback, and
all MASTER TASK criteria. Any unchecked mandatory item remains UNVERIFIED or
BLOCKED.

## Final report and status semantics

The final report is a single `DEV_FINAL_REPORT` containing MASTER_TASK,
STATUS, PREFLIGHT, DELEGATION, CONSULTATIONS, IMPLEMENTED, FILES_CHANGED,
DATABASE, CONFIG, TESTS, QA, SECURITY, REGRESSION, PRODUCTION, GIT_DIFF_REVIEW,
ACCEPTANCE_CRITERIA, DEVIATIONS_FROM_MASTER_TASK, BLOCKERS, RISKS,
TECHNICAL_DECISIONS, PROJECT_STATE_IMPACT, RECOMMENDED_NEXT_STEP,
CHANGES_MADE_BY_ARCHITECT_DIRECTLY, and FINAL_NOTE.

Allowed final statuses:

- `PASS`: all applicable criteria and mandatory checks completed, no blocker;
- `PARTIAL`: useful work completed, but scope or evidence remains incomplete;
- `BLOCKED`: external access, dependency, approval, or evidence prevents safe completion;
- `FAILED`: implementation attempt did not meet the criteria.

## Safety preserved by this protocol

This protocol supplements, and does not replace, [AGENTS.md](../AGENTS.md),
[QA_ROLE.md](../QA_ROLE.md), and [QA_PROCESS.md](../QA_PROCESS.md). In
particular it preserves landing auto-redirect, exact attribution before
`userDidSubscribe`, owner/channel-link isolation, no raw secret/PII logging,
single production MAX poller, gated production deploy, rollback on failed
smoke, and required real-device mobile evidence.

Advertising writes remain forbidden unless a future MASTER TASK contains
explicit owner authorization. The existence of an analytical recommendation
never authorizes a Direct or VK write.

## Release and Git evidence

`RELEASE_STATUS.md` remains the canonical release record. Once the Git baseline
exists, applicable release records must include `MASTER_TASK_ID`, `PRE_TASK_SHA`,
`CANDIDATE_SHA`, and `DIFF_REVIEW`. Until a confirmed baseline commit exists,
these values remain `UNVERIFIED`; an untracked workspace must not be reported as
a verified Git diff.

## Orchestration self-test

For each bootstrap or future orchestration validation, report:

```text
ORCHESTRATION_SELF_TEST
PRECHECK_DONE = YES / NO
PRIMARY_ROLE_SELECTED = YES / NO / MANUAL_HANDOFF_REQUIRED
LEGACY_ROLE_AVOIDED = YES / NO / NOT_APPLICABLE
QA_GATE_EXECUTED = YES / NO / MANUAL_HANDOFF_REQUIRED
DEV_INTEGRATION_REVIEW = PASS / FAIL / UNVERIFIED
```

If the Codex environment cannot invoke a specific task programmatically, state
`MANUAL_HANDOFF_REQUIRED`; never claim that a role was called when it was not.
