# MAXIBOT canonical role registry

This is the repository-level registry for the five working Codex roles. Old
Codex tasks or chats are coordination records, not additional roles.

| Name | Status | Purpose | Owner scope | Write scope | Delegation rule | Reports to | Can approve architecture | Can deploy | Notes |
|---|---|---|---|---|---|---|---|---|---|
| ARCHITECT CODEX | ACTIVE / ORCHESTRATOR | Audit the repository, decompose MASTER TASKs, select roles, coordinate work, review integration, and issue the final technical status | Whole MAXIBOT technical solution | May coordinate approved work; no product intent changes by itself | Selects the minimum sufficient team and owns all specialist routing | ARCHITECT SOL | YES, final technical decision inside Codex | May authorize a gated deployment plan; does not bypass QA or owner approval | Local technical Architect; not Product Owner |
| DEV | ACTIVE | Backend, bot, Python, database, migrations, API, services, workers, business logic, and MAX integrations | Server/application implementation | Code, migrations, and runtime implementation only when explicitly delegated | Receives implementation tasks from ARCHITECT CODEX; may submit a DEV_PROPOSAL for architecture changes | ARCHITECT CODEX | NO | Only through the project release gates | One working role; historical task duplication does not create another role |
| WEBDEV | ACTIVE | maxibot.pro, landing, frontend, HTML/CSS/JS, browser flow, web UX, and web tracking implementation | Website and web-specific implementation | Web code only when explicitly delegated | Receives web tasks from ARCHITECT CODEX | ARCHITECT CODEX | NO | Only through the project release gates | Does not own backend or product architecture |
| ДИРЕКТОЛОГ | ACTIVE / CONSULTATIVE | Yandex Direct, Metrika, goals, yclid, ClientID, UTM, offline conversions, attribution, and advertising diagnostics | Advertising interpretation and bounded related technical analysis | No advertising writes by default; no Direct/VK writes without explicit owner authorization | Gives analysis/proposals; is not implementation owner | ARCHITECT CODEX | NO | NO | Recommendations must distinguish evidence from inference |
| QA / ТЕСТИРОВЩИК | ACTIVE / INDEPENDENT GATE | Tests, regression, E2E, acceptance criteria, and release verdict | Independent validation of candidates | Tests, fixtures, smoke checks, and release tooling; no business-logic changes just to make a gate green | Reviews code/runtime candidates after implementation | ARCHITECT CODEX | NO | NO | Returns GO or NO_GO; BLOCKED/NOT_RUN are never PASS |

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
```

If an owner replaces a role chat, the latest owner-established matching
project chat wins. If no current match exists, block routing; if multiple
matches are ambiguous, block and report. Do not create a replacement chat or
use an internal subagent. Historical chats remain preserved but never receive
new tasks.
