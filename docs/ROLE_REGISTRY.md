# MAXIBOT canonical role registry

`ARCHITECT / ARCHITECT SOL` is the external ChatGPT project architect. `ARCHITECT SOL` is a compatibility alias for `ARCHITECT`. `DEV` is the primary autonomous technical implementer and owns task state, repository preflight, routine technical decisions, implementation, tests, self-review, integration, and closeout for approved work. There is no required or active `ARCHITECT CODEX` role or chat. OWNER approval remains required for protected actions.

## Minimum required roles / FAST_PATH

DEV chooses the shortest safe execution route for the approved scope. One implementer is the default. Specialists and independent QA are added only when the actual domain scope or an applicable gate requires them. Narrow direct handoffs are allowed; roles do not participate ceremonially. DEV proceeds without a separate ARCHITECT CODEX chat. DEV must escalate only for a real product or architecture conflict, a material behavior/scope change, or a protected authorization. OWNER-only actions and deterministic release gates remain unchanged.

| Name | Status | Purpose | Owner scope | Write scope | Delegation rule | Reports to | Can approve architecture | Can deploy | Notes |
|---|---|---|---|---|---|---|---|---|---|
| ARCHITECT / ARCHITECT SOL | EXTERNAL / PRODUCT-SYSTEM ARCHITECT | Defines product intent, system architecture, approved task scope, constraints, and acceptance criteria | Product and system intent | Issues approved specifications; no repository edits by this role contract | Receives genuine product/architecture conflicts and completed technical handoff | OWNER | YES, product/system intent | NO | external ChatGPT project architect; ARCHITECT SOL is a compatibility alias |
| DEV | ACTIVE / PRIMARY IMPLEMENTER | Backend, bot, Python, database, migrations, API, services, workers, business logic, and MAX integrations | Approved technical task and implementation | Code and tests within accepted scope; routine technical decisions; candidate preparation | Owns preflight through verified closeout; routes specialists and canonical QA only when required | ARCHITECT / ARCHITECT SOL for genuine product or architecture decisions; OWNER for protected approvals | NO, implements approved architecture | Only through project release gates and explicit authorization | DEV is the primary autonomous technical implementer; no separate ARCHITECT CODEX chat prerequisite |
| WEBDEV | ACTIVE / OPTIONAL SPECIALIST | maxibot.pro, landing, frontend, HTML/CSS/JS, browser flow, web UX, and web tracking | Explicitly delegated web scope | Web code within delegated scope | Narrow direct handoff from DEV when web expertise is needed | DEV | NO | Only through project release gates | Does not own backend or product architecture |
| ДИРЕКТОЛОГ | ACTIVE / CONSULTATIVE | Yandex Direct, Metrika, goals, yclid, ClientID, UTM, offline conversions, attribution, and advertising diagnostics | Advertising interpretation and bounded related technical analysis | No advertising writes by default; no Direct/VK writes without explicit OWNER authorization | Consulted only when campaign or attribution expertise is in scope | DEV; ARCHITECT / ARCHITECT SOL for product decisions | NO | NO | Recommendations distinguish evidence from inference |
| QA / ТЕСТИРОВЩИК | ACTIVE / OPTIONAL INDEPENDENT GATE | Independent tests, regression, E2E, acceptance criteria, and findings | Candidate validation under applicable QA policy | Tests, fixtures, smoke checks, and QA tooling; no business-logic changes just to make a gate green | Existing canonical QA thread; DEV sends candidate and receives verdict directly when required | DEV for findings; ARCHITECT / ARCHITECT SOL or OWNER for product escalation | NO | NO | Required only when release/task policy requires; deterministic gates remain authoritative |
| SYSADMIN | ACTIVE / OPTIONAL INFRASTRUCTURE GATE | VPS, infrastructure, Docker, health, polling, duplicate-process checks, backups, errors, image architecture, and pre/post-deploy verification | Infrastructure and deployment checks | Deployment tooling/configuration, rollback snapshots, health and platform guards within authorization | DEV routes only for real infrastructure, secret/runtime, incident, or deployment blocker | DEV; OWNER for protected production authorization | NO | Only as explicitly authorized by canonical release procedure | Not an architect, feature developer, or advertising operator |

## Canonical Linear context by role

Load only the relevant canonical project context for the current task. A document link is not proof that its contents were loaded. If Linear is unavailable, record the exact access failure and mark required context `NOT_VERIFIED`; do not invent role-specific documents.

| Role | Canonical Linear context |
|---|---|
| ARCHITECT / ARCHITECT SOL | [MAXIBOT Project Context / AGENTS](https://linear.app/bestuzhev/document/maxibot-project-context-agents-1a98f6000c28); [Architect Handoff](https://linear.app/bestuzhev/document/arhitektor-handoff-bb75d67e4a95); [PROSTOR canonical release rules](https://linear.app/bestuzhev/document/pravila-relizov-fac48f62832a) when release scope applies |
| DEV | [MAXIBOT Project Context / AGENTS](https://linear.app/bestuzhev/document/maxibot-project-context-agents-1a98f6000c28); [Architect Handoff](https://linear.app/bestuzhev/document/arhitektor-handoff-bb75d67e4a95); [MAXIBOT Workers](https://linear.app/bestuzhev/document/maxibot-workers-97f568bf84b1) for worker scope |
| WEBDEV | [MAXIBOT Project Context / AGENTS](https://linear.app/bestuzhev/document/maxibot-project-context-agents-1a98f6000c28); [Architect Handoff](https://linear.app/bestuzhev/document/arhitektor-handoff-bb75d67e4a95) |
| ДИРЕКТОЛОГ | [MAXIBOT Project Context / AGENTS](https://linear.app/bestuzhev/document/maxibot-project-context-agents-1a98f6000c28); [MAXIBOT Workers](https://linear.app/bestuzhev/document/maxibot-workers-97f568bf84b1) only for Ads Worker scope |
| QA | [MAXIBOT Project Context / AGENTS](https://linear.app/bestuzhev/document/maxibot-project-context-agents-1a98f6000c28); [QA](https://linear.app/bestuzhev/document/qa-7a34fcf9286c) |
| SYSADMIN | [MAXIBOT Workers](https://linear.app/bestuzhev/document/maxibot-workers-97f568bf84b1); [Architect Handoff](https://linear.app/bestuzhev/document/arhitektor-handoff-bb75d67e4a95); [PROSTOR canonical release rules](https://linear.app/bestuzhev/document/pravila-relizov-fac48f62832a) when deployment scope applies |

## Separate runtime subsystem

`Ads Codex Worker` is an active runtime specialist, not a sixth working role and
not part of the general development hierarchy. It consumes bounded advertising
analysis runs and returns schema-validated diagnosis JSON. It cannot receive a
software-development MASTER TASK, manage Dev or QA, make architecture
decisions, or perform Direct/VK writes.

## Role identity and task records

The project may contain several historical Codex tasks with similar titles.
They must not be counted as separate roles. The role is the ownership contract above; task selection is a coordination detail owned by DEV.

## Dynamic chat routing registry

## Frozen paid-traffic paths

`BOT` and `CHANNEL` are separate `PAID_TRAFFIC_CRITICAL`
`FROZEN_PRODUCTION_PATHS`. The freeze is behavioral, not only file-based;
`NO_REFACTOR_FOR_CLEANUP = YES`, and any frozen-path or shared-dependency
change requires `OWNER_APPROVAL_REQUIRED_FOR_FROZEN_CHANGE = YES` plus
`FROZEN_PATH_REGRESSION` evidence. Use `docs/frozen_paths.yaml` and
`scripts/frozen_path_guard.sh`; `BOT_AND_CHANNEL_SEPARATE = YES` and
`FAST_PATH_WHEN_UNCHANGED = YES`.

Chat IDs are cache values, not permanent role identity. Before each delegation,
resolve the current existing project chat using the project metadata and the
selection rule in `docs/ORCHESTRATION_PROTOCOL.md`.

```text
ROLE=DEV
RESOLUTION_MODE=DYNAMIC
LAST_KNOWN_CHAT=01a065b2-55eb-7ec0-8e79-4fc19cbf84f5
CURRENT_STATUS=ACTIVE

ROLE=WEBDEV
RESOLUTION_MODE=DYNAMIC
LAST_KNOWN_CHAT=01a053ab-77b0-7dd0-9690-c036899ab6c5
CURRENT_STATUS=NOT_LOADED

ROLE=DIRECTOLOG
RESOLUTION_MODE=DYNAMIC
LAST_KNOWN_CHAT=01a0349f-54bc-78c3-8a2f-c6cb70b3cc6f
CURRENT_STATUS=IDLE

ROLE=QA
RESOLUTION_MODE=DYNAMIC
LAST_KNOWN_CHAT=019ffb88-32db-76f1-bdc2-1373b29fca13
CURRENT_STATUS=NOT_LOADED

ROLE=SYSADMIN
RESOLUTION_MODE=MANUAL_HANDOFF_REQUIRED
LAST_KNOWN_CHAT=NONE
CURRENT_STATUS=ACTIVE
```

## Cross-role lead and escalation rules

DEV ведёт multi-role задачу. Внешний `ARCHITECT / ARCHITECT SOL` может назвать
`LEAD_IMPLEMENTER` только если это действительно нужно; иначе DEV остаётся lead.
Lead координирует узкие specialist handoff'ы, собирает candidate, направляет его
в QA и закрывает техническую часть задачи. Примеры: backend-heavy → DEV,
frontend-heavy → WEBDEV.

DEV и WEBDEV могут обмениваться прямыми implementation handoff'ами по уже
утверждённой архитектуре. Они не могут самостоятельно переопределять product
behavior, core architecture, attribution semantics или security policy. При
необходимости существенного изменения возвращается `ARCHITECTURE_BLOCKER = YES`
напрямую внешнему `ARCHITECT / ARCHITECT SOL`.

Before optional specialist delegation, resolve an existing project chat using current project metadata. If unavailable or ambiguous, report `MANUAL_HANDOFF_REQUIRED` and continue independent work; do not create a replacement role chat. DEV may use bounded subagents for technical tasks when available, but they do not replace a required independent QA gate. Historical chats remain preserved.
