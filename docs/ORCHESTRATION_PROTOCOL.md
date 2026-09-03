# MAXIBOT orchestration protocol V1

## Canonical hierarchy

```text
OWNER
  ↓
ARCHITECT SOL (external Product/System Architect)
  ↓
MASTER TASK
  ↓
ARCHITECT CODEX (local technical Architect + orchestrator)
  ├── DEV
  ├── WEBDEV
  ├── ДИРЕКТОЛОГ
  └── QA / ТЕСТИРОВЩИК
  ↓
FINAL ARCHITECT REPORT
  ↓
ARCHITECT SOL
```

ARCHITECT SOL defines product intent, desired behavior, business rules,
architectural and safety constraints, and acceptance criteria. ARCHITECT CODEX
defines implementation approach from the verified repository and remains the
final technical decision owner inside the Codex team.

## MASTER TASK contract

Treat a MASTER TASK as the source of truth for WHAT: desired behavior, business
logic, constraints, MUST/MUST NOT rules, safety, and acceptance criteria. Do
not treat filenames, classes, functions, tables, migrations, abstractions, or
other implementation assumptions as facts until the repository is checked.

## Mandatory repository preflight

Before any substantial task, ARCHITECT CODEX records:

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

## Reuse before create

Before creating a service, model, table, worker, handler, pipeline, tracking
mechanism, configuration, or test abstraction, search for and reuse or extend
the existing implementation. Do not create parallel architecture without an
explicit Architect decision and recorded reason.

## Minimum sufficient delegation and routing

| Task type | Role(s) | QA required | Deployment possible | Final owner |
|---|---|---:|---:|---|
| Architecture-only analysis | ARCHITECT CODEX, optional consultants | Usually no | No | ARCHITECT CODEX |
| Bot/backend/DB/API/worker | DEV | Yes | Gated | ARCHITECT CODEX |
| Site/landing/frontend/browser flow | WEBDEV | Yes when executable behavior changes | Gated | ARCHITECT CODEX |
| Direct/Metrika/attribution interpretation | ДИРЕКТОЛОГ | Depends on code impact | Advertising writes forbidden by default | ARCHITECT CODEX |
| Backend + web | DEV + WEBDEV | Yes | Gated | ARCHITECT CODEX |
| Backend + advertising/attribution | DEV + ДИРЕКТОЛОГ | Yes | Gated | ARCHITECT CODEX |
| Web + Metrika | WEBDEV + ДИРЕКТОЛОГ | Yes | Gated | ARCHITECT CODEX |
| Full attribution flow | DEV + WEBDEV + ДИРЕКТОЛОГ | Yes | Gated | ARCHITECT CODEX |
| Advertising runtime diagnosis | Existing Ads Codex Worker flow | Per existing runtime gate | No automatic advertising write | ARCHITECT CODEX for product decisions |

Do not invoke every role automatically. Specialist roles submit proposals and
structured handoffs; ARCHITECT CODEX accepts, modifies, or rejects them.

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

Before every specialist delegation, ARCHITECT CODEX must resolve the current
project chat for the requested role and compare it with the last-known ID. The
selection rule is: current MAXIBOT project membership, canonical role name or
marker, available/non-archived state, and the latest owner-established current
chat. If the selected ID changes, update the routing state before delegating.
If no match exists, delegation is `ROUTING_BLOCKED`; if multiple matches are
ambiguous, delegation is `ROUTING_AMBIGUOUS`. Never auto-create a replacement,
use an internal subagent as a role substitute, or select a historical task only
because its ID is documented. An explicit owner statement that a new chat
replaces a role is authoritative.

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
RECOMMENDATION_TO_ARCHITECT =
```

Consultative roles may replace `FILES_CHANGED` with `ANALYSIS` and return
`EVIDENCE` and `RECOMMENDATION`. A developer proposal to change architecture
must return to ARCHITECT CODEX before implementation.

## QA and integration review

The canonical QA contract is defined by [QA_ROLE.md](../QA_ROLE.md),
[QA_PROCESS.md](../QA_PROCESS.md), [qa/TEST_CASES.md](../qa/TEST_CASES.md),
[RELEASE_STATUS.md](../RELEASE_STATUS.md), and
[scripts/qa_check.sh](../scripts/qa_check.sh). Do not create a parallel QA
protocol, registry, release format, or regression framework.

After implementation, the required flow for applicable code/runtime work is:

```text
IMPLEMENTER SELF-TEST
  ↓
ARCHITECT CODEX PRELIMINARY INTEGRATION REVIEW
  ↓
QA → canonical QA process → QA_VERDICT = GO / NO_GO
  ↓
ARCHITECT CODEX FINAL INTEGRATION REVIEW
  ↓
FINAL_ARCHITECT_REPORT
```

The canonical full QA command is `./scripts/qa_check.sh`. Applicable staging
checks use the isolated local Docker process specified in `QA_PROCESS.md`,
including separate app, PostgreSQL, volumes, `.env.staging`, ports, static,
unit, integration, regression, build, health, and E2E checks.

Executable code, schema, runtime, API, bot, landing, tracking, ADS, worker, or
deployment changes require the independent QA gate. QA returns `GO` or
`NO_GO`; QA GO does not itself mean MASTER TASK or architectural PASS. `NOT_RUN`
and `BLOCKED` are never PASS. Bugs require regression coverage, and new
functionality requires new or updated canonical test cases. Preserve the
existing TC-ID, classification, priority, and critical-regression rules.

QA may repair QA tooling within the existing policy, but must not rewrite
application business logic to make a gate pass. If application changes are
needed, QA returns `NO_GO` or `BLOCKED` and ARCHITECT CODEX routes a separate
implementation task.

Production credentials are not used in staging by default. If a read-only check
cannot run without them, QA reports `PRODUCTION_CREDENTIAL_REQUIRED=YES` and
ARCHITECT CODEX chooses the safe method. Production writes from staging remain
forbidden without explicit owner authorization.

After handoffs, ARCHITECT CODEX reviews actual implementation, full diff when
Git is available, unexpected files, duplicate architecture, migrations,
schemas, tests, QA verdict, regression, secrets, auth, permissions,
destructive behavior, runtime configuration, production impact, rollback, and
all MASTER TASK criteria. Any unchecked mandatory item remains UNVERIFIED or
BLOCKED.

## Final report and status semantics

The final report is a single `FINAL_ARCHITECT_REPORT` containing MASTER_TASK,
STATUS, PREFLIGHT, DELEGATION, CONSULTATIONS, IMPLEMENTED, FILES_CHANGED,
DATABASE, CONFIG, TESTS, QA, SECURITY, REGRESSION, PRODUCTION, GIT_DIFF_REVIEW,
ACCEPTANCE_CRITERIA, DEVIATIONS_FROM_MASTER_TASK, BLOCKERS, RISKS,
ARCHITECT_DECISIONS, PROJECT_STATE_IMPACT, RECOMMENDED_NEXT_STEP,
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
ARCHITECT_INTEGRATION_REVIEW = PASS / FAIL / UNVERIFIED
```

If the Codex environment cannot invoke a specific task programmatically, state
`MANUAL_HANDOFF_REQUIRED`; never claim that a role was called when it was not.
