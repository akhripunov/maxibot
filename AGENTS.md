# MAXIBOT project instructions

## Project identity and canonical sources

- This repository is `akhripunov/maxibot`. GitHub exact commits are the durable source for code; Linear records project context, task decisions, and release evidence.
- Read [the role registry](docs/ROLE_REGISTRY.md), [orchestration protocol](docs/ORCHESTRATION_PROTOCOL.md), and the relevant MAXIBOT Linear sections before specialized work. Do not load the whole historical archive by default.
- Product and operational context: [MAXIBOT Project Context / AGENTS](https://linear.app/bestuzhev/document/maxibot-project-context-agents-1a98f6000c28), [Architect Handoff](https://linear.app/bestuzhev/document/arhitektor-handoff-bb75d67e4a95), and [MAXIBOT Workers](https://linear.app/bestuzhev/document/maxibot-workers-97f568bf84b1).
- Release sources are MAXIBOT's [Linear release rules](https://linear.app/bestuzhev/document/pravila-reliza-maxibot-825471a52588), [release journal](https://linear.app/bestuzhev/document/zhurnal-relizov-maxibot-3e615fe7e879), `docs/RELEASE_AUTHORIZATION.md`, and `docs/RELEASE_AUTOMATION.md`.
- GPoint and MAXIBOT are independent projects with separate authorization, workflows, runners, migrations, artifacts, release ledgers, Linear documents, and production environments. Never apply GPoint release procedures or authorization to MAXIBOT, or MAXIBOT release procedures or authorization to GPoint. Resolve each project's rules independently for cross-project work.

## Critical product and safety invariants

- Landing-to-MAX is an automatic redirect; no required landing click. Use the bot tracking deep link; a generic channel link does not prove click attribution. Keep BOT/CHANNEL, `chat_id`, owner, and invite URL contexts separate; preserve proven `yclid` → click ID → MAX user → channel attribution. Never infer `userDidSubscribe`; require confirmed membership and a proven visit link. Closed-channel owner approval remains required until production auto-approval is proven. A missing valid channel invite blocks the handoff.
- Preserve the MAX mobile handoff contract in `docs/frozen_paths.yaml`, `QA_PROCESS.md`, `QA_ROLE.md`, and `tests/regression/test_mobile_max_handoff_contract.py`. Required real-device checks cannot be replaced by unit or synthetic tests; Android stays blocked until its real-device E2E is recorded.
- Advertising campaign control is `OWNER_ONLY`. Do not change budgets, bids, strategies, targeting, creatives, campaign state, provider writes, or `ADS_*` write flags without explicit action-specific OWNER authorization. Keep both master flags OFF for ordinary diagnostics. `BOT` and `CHANNEL` are separate behaviorally frozen paid-traffic paths: use `docs/frozen_paths.yaml`, `scripts/frozen_path_guard.sh`, and required `FROZEN_PATH_REGRESSION` evidence; no cleanup refactor or gate bypass.
- Only the VPS worker on `135.106.166.46` polls production MAX updates. API/local Compose polling stays `MAX_RUN_POLLING=false`; only the VPS worker has `MAX_RUN_POLLING=true`. Never start a second production poller or run local MAXIBOT with a production MAX token and polling; check topology before diagnosing duplicate messages, deleting messages, or changing channel models. The contract is covered by `docs/ARCHITECT_SOL_CONTEXT_PACK.md`, Compose files, and `tests/regression/test_single_poller_safety.py`.
- Keep tokens, cookies, raw frames, secrets, and personal data out of Git, logs, and Linear. Do not use user-level WebSocket/opcodes 55/77 without a separately approved safe specification. Unit tests alone do not prove E2E or production readiness.
- AI Promotion is one object-level BOT or CHANNEL view over canonical ADS Data Layer/Closed-Loop state. Analyze active linked campaigns with campaign isolation; suspended campaigns do not drive current recommendations. Derive stages from canonical state; show evidence-backed causes/actions only. `UNKNOWN != ZERO`; provider proxy is not a business outcome. CHANNEL uses `max_subscriber`, BOT uses `bot_started`; use existing analysis workers with cadence/deduplication and keep Direct read-only.

## Autonomous execution

- Start substantial work with the repository preflight in `docs/ORCHESTRATION_PROTOCOL.md`. Execute an approved task through acceptance. Resolve routine reversible technical choices independently; do not ask again about accepted decisions. Iterate implementation, tests, review, and fixes. Ask OWNER only for a decision or authorization that is actually required.
- Use one primary agent by default. Use subagents only for bounded technical work that materially helps; the main agent owns decisions, integration, and follow-through. A subagent does not replace a named MAXIBOT role or required independent QA.
- Do not infer authority from chat titles, prior chats, model choice, or another project's approval. Stop a protected operation when required evidence or authorization is missing; report the exact gate.

## Role selection and orchestration

- Initialize a new specialized chat with an explicit `ROLE=` from `docs/ROLE_REGISTRY.md`. A chat title does not select a role. Without a selected role, ordinary research and assistance are allowed; do not automatically start a specialist workflow. Keep one selected role for the chat; for a specialization change, recommend a new chat. Do not mix role histories.
- `ARCHITECT` is the external ChatGPT project architect; `ARCHITECT SOL` is its compatibility alias. `DEV` is the primary autonomous technical implementer and task-state owner for approved work. No separate `ARCHITECT CODEX` chat or role is required. Preserve `WEBDEV`, `ДИРЕКТОЛОГ`, `QA`, and `SYSADMIN` boundaries and the direct DEV↔canonical-QA handoff. Escalate to ARCHITECT only for a real product or architecture conflict, or scope change.
- `.codex/config.toml` sets Luna/Medium project defaults for the primary Codex and subagents. It is a default, not proof of the active runtime model; verify a new chat when that claim matters. Do not change global Codex settings or the external `ARCHITECT` product role.

## MAXIBOT release boundaries

- MAXIBOT Release Orchestrator V2 is `COMPLETE / PASS / FROZEN`. Do not change its implementation as part of instruction work.
- Follow only MAXIBOT's `EXACT_SHA` / `TASK_SCOPED_AUTO` rules. Exact-main Prepare automatically triggers the existing controller's read-only preflight; `scripts/release_prod.sh` is the sole production mutation path. Retain immutable provenance, `linux/amd64`, API/Worker image parity, exact Alembic target, MAXIBOT backup/migration, locks, rollback, health, worker and regression gates. `READY_FOR_OWNER` is read-only readiness, not production authorization. Technical PASS is not owner acceptance.
- A task to implement alone does not authorize production apply. For protected or ambiguous actions, use the MAXIBOT authorization record and approval gates; never borrow a GPoint approval. Do not perform a production deploy for changes to `AGENTS.md`.
- Release workflows are dispatched and monitored only through the authorized CLI/API or GitHub API/MCP workflow tools, never browser UI. Check for active runs before dispatch; do not duplicate, cancel, or rerun a release unnecessarily. The repository-scoped `maxibot-production` runner is for trusted release jobs only, never untrusted PR/fork jobs.

## GitHub instruction publication — OWNER decision 2026-10-08

- Once the OWNER has approved the task, DEV may publish scoped edits to `AGENTS.md`, Codex instructions, and role/bootstrap documents directly to exact current `origin/main` without a mandatory PR or separate approval per ordinary instruction commit, **only when current repository controls permit direct writes**. A PR is optional or used when technically required, not a general instruction-publication gate. Do not reset, switch, stash, overwrite, or update another active chat's branch/worktree.
- Read the exact current main file/blob SHA and applicable policies before editing. Preserve concurrent changes, run relevant syntax, policy, security, frozen-path and independent QA gates where applicable, and record the exact published commit SHA. Never bypass branch protection or required reviews/checks; on a blocked direct write, stop and report a compliant alternative.
- An explicit, scope-specific OWNER authorization is still required for material changes to agent authority/autonomy, safety, OWNER-only operations, release rules, protected paths, access controls and other protected policies. The decision to eliminate mandatory PRs is **not** blanket approval for unrelated protected changes or production apply. Preserve MAXIBOT-only exact-SHA/TASK_SCOPED_AUTO rules and all independent gates.
- After publishing, verify `origin/main` via fresh readback; synchronize the corresponding Linear mirror/policy and safely refresh authorized Mac instructions without disturbing existing Codex chats. Prove actual role/profile/subagent/model loading separately; static config is not runtime proof. A push may trigger Prepare/read-only preflight but never authorizes production Deploy. No production Deploy for instruction-only changes.

## Task completion

- Run relevant tests and the full applicable suite; run frozen-path and QA gates when affected. Distinguish code/test evidence from real device, production, and OWNER acceptance; report `NOT_VERIFIED` where evidence is missing.
- Leave the task worktree clean with committed/published changes or report remaining files, exact commit/optional PR and blocking gates. Never perform protected policy changes without their scope-specific OWNER authorization or deploy production for instruction changes.
