# MAXIBOT canonical role registry

This is the repository-level registry for the six working Codex roles. Old
Codex tasks or chats are coordination records, not additional roles.

`ARCHITECT_CODEX_REQUIRED = YES` for cross-role and release work.
`ARCHITECT CODEX` is the local technical architect, orchestrator, and single
technical window. `ARCHITECT SOL` is the product/system architect.

## Minimum required roles / FAST_PATH

ARCHITECT CODEX selects the shortest safe execution route. One primary
implementer is preferred by default; additional specialists are added only for
actual scope needs, real blockers, or mandatory independent technical gates. Roles must
not participate ceremonially. Narrow direct specialist handoffs are allowed.
ARCHITECT CODEX owns global task state as the single window but is not a
mandatory message relay between every specialist interaction. QA is advisory
for release readiness. SYSADMIN is required for
actual infrastructure risks, incidents, secrets/runtime injection, or deploy
blockers, not automatically for every release.

| Name | Status | Purpose | Owner scope | Write scope | Delegation rule | Reports to | Can approve architecture | Can deploy | Notes |
|---|---|---|---|---|---|---|---|---|---|
| ARCHITECT CODEX | ACTIVE / ORCHESTRATOR | Audit the repository, decompose MASTER TASKs, select roles, coordinate work, review integration, and issue the final technical status | Whole MAXIBOT technical solution | May coordinate approved work; no product intent changes by itself | Selects the minimum sufficient team and owns all specialist routing | ARCHITECT SOL | YES, final technical decision inside Codex | May authorize a gated deployment plan; does not bypass QA or owner approval | Local technical Architect; not Product Owner |
| DEV | ACTIVE | Backend, bot, Python, database, migrations, API, services, workers, business logic, and MAX integrations | Server/application implementation | Code, migrations, runtime implementation, and routine direct QA handoff for delegated work | Receives implementation tasks from the governing Architect; may submit a DEV_PROPOSAL for architecture changes; may send candidates directly to canonical QA | ARCHITECT SOL for product/architecture; QA directly for validation verdict | NO | Only through the project release gates | One working role; historical task duplication does not create another role |
| WEBDEV | ACTIVE | maxibot.pro, landing, frontend, HTML/CSS/JS, browser flow, web UX, and web tracking implementation | Website and web-specific implementation | Web code only when explicitly delegated | Receives web tasks from ARCHITECT CODEX | ARCHITECT CODEX | NO | Only through the project release gates | Does not own backend or product architecture |
| ДИРЕКТОЛОГ | ACTIVE / CONSULTATIVE | Yandex Direct, Metrika, goals, yclid, ClientID, UTM, offline conversions, attribution, and advertising diagnostics | Advertising interpretation and bounded related technical analysis | No advertising writes by default; no Direct/VK writes without explicit owner authorization | Gives analysis/proposals; is not implementation owner | ARCHITECT CODEX | NO | NO | Recommendations must distinguish evidence from inference |
| QA / ТЕСТИРОВЩИК | ACTIVE / ADVISORY | Independent tests, regression, E2E, acceptance criteria, and findings | Advisory candidate validation | Tests, fixtures, smoke checks, and release tooling; no business-logic changes just to make a gate green | Reviews candidates received directly from DEV in the canonical QA thread when requested | DEV for routine handoff; ARCHITECT SOL / OWNER for product escalation | NO | NO | GO/NO_GO/NOT_RUN/TIMEOUT are informational; deterministic release gates decide readiness |
| SYSADMIN | ACTIVE / INFRASTRUCTURE GATE | VPS, infrastructure, Docker, health, polling, duplicate-process checks, backups, errors, image architecture, and pre/post-deploy verification | Production operations only | Deployment tooling/configuration, rollback snapshots, health and platform guards; no feature or business-logic changes | ARCHITECT CODEX | NO | Only an Architect-authorized gated deployment | Not an architect, feature developer, QA replacement, release owner, or advertising operator |

## Separate runtime subsystem

`Ads Codex Worker` is an active runtime specialist, not a sixth working role and
not part of the general development hierarchy. It consumes bounded advertising
analysis runs and returns schema-validated diagnosis JSON. It cannot receive a
software-development MASTER TASK, manage Dev or QA, make architecture
decisions, or perform Direct/VK writes.

## Role identity and task records

The project may contain several historical Codex tasks with similar titles.
They must not be counted as separate roles. The role is the ownership contract
above; task selection is a coordination detail owned by ARCHITECT CODEX.

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

`ARCHITECT SOL` назначает `LEAD_IMPLEMENTER` для multi-role задач. Lead
implementer координирует narrow specialist handoffs, собирает unified candidate,
направляет его в QA и закрывает техническую часть задачи. Примеры: backend-heavy
→ DEV, frontend-heavy → WEBDEV.

DEV и WEBDEV могут обмениваться прямыми implementation handoff'ами по уже
утверждённой архитектуре. Они не могут самостоятельно переопределять product
behavior, core architecture, attribution semantics или security policy. При
необходимости существенного изменения возвращается `ARCHITECTURE_BLOCKER = YES`
напрямую `ARCHITECT SOL`.

If an owner replaces a role chat, the latest owner-established matching
project chat wins. If no current match exists, block routing; if multiple
matches are ambiguous, block and report. Do not create a replacement chat or
use an internal subagent. Historical chats remain preserved but never receive
new tasks.
