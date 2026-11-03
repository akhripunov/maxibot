# MAXIBOT Native First V3 + Kamal 2 release policy

A push to protected `main` starts the sole ordinary release workflow:
`.github/workflows/maxibot-native-release.yml` — Tests → Build/GHCR → Kamal Deploy → Runtime Verify.
A pull request runs checks only. The production job uses the GitHub `production` Environment and respects all current repository and Environment protections. Never bypass or change those protections as part of a release.

The workflow's `workflow_dispatch` interface is limited to the explicitly scoped application rollback. It is not a routine deployment path. No Compose deployment controller, release state machine, or Mac-side post-push stage is supported.

Before publishing, obtain OWNER authorization for the exact task scope and verify the complete candidate diff from the current production SHA. A main push can deploy production; do not publish unrelated work under a release authorization.

Keep exact SHA, immutable GHCR digest, linux/amd64, SBOM/Provenance, frozen-path, database/Redis, volume, backup/restore, single-Poller, Worker memory, rollback-compatibility, and Runtime Verify gates. A migration may be applied only when the current task contract authorizes the exact pending Alembic set and the live DB baseline matches. No automatic database downgrade or blind retry after uncertain Apply.

Historical controller designs are not supported. GitHub Actions and GitHub Deployments are the release history; Linear records task authorization and factual evidence.

---

# MAXIBOT project instructions

## Project identity and canonical sources

- This repository is `akhripunov/maxibot`. GitHub exact commits are the durable source for code; Linear records project context, task decisions, and release evidence.
- Read [the role registry](docs/ROLE_REGISTRY.md), [orchestration protocol](docs/ORCHESTRATION_PROTOCOL.md), and the relevant MAXIBOT Linear sections before specialized work. Do not load the whole historical archive by default.
- Product and operational context: [MAXIBOT Project Context / AGENTS](https://linear.app/bestuzhev/document/maxibot-project-context-agents-1a98f6000c28), [Architect Handoff](https://linear.app/bestuzhev/document/arhitektor-handoff-bb75d67e4a95), and [MAXIBOT Workers](https://linear.app/bestuzhev/document/maxibot-workers-97f568bf84b1).
- Release sources are MAXIBOT's [Linear release rules](https://linear.app/bestuzhev/document/pravila-reliza-maxibot-825471a52588), [release journal](https://linear.app/bestuzhev/document/zhurnal-relizov-maxibot-3e615fe7e879), `docs/RELEASE_AUTHORIZATION.md`, and `docs/RELEASE_AUTOMATION.md`.
- GPoint and MAXIBOT are independent projects with separate authorization, workflows, runners, migrations, artifacts, release ledgers, Linear documents, and production environments. Never apply GPoint release procedures or authorization to MAXIBOT, or MAXIBOT release procedures or authorization to GPoint. Resolve each project's rules independently for cross-project work.

## GitHub/GHCR access diagnostics — MAXIBOT

- The current [Linear «GitHub доступ»](https://linear.app/bestuzhev/document/github-dostup-7970682d1b80) is a diagnostic runbook, not a prerequisite for ordinary MAXIBOT work or successful GitHub/GHCR operations. Do not read it for a healthy `git fetch`, push, GitHub API, or GHCR request, and do not run prophylactic access checks.
- When an actual DNS, TLS, HTTPS/transport, GitHub/GHCR authentication, authorization, or permission error occurs, retrieve the current document and apply the relevant safe steps for this repository and host only. It does not transfer PROSTOR/GPoint credentials, runners, or authority to MAXIBOT.
- Classify the failure before acting. A failed `gh auth status` during unhealthy transport does not prove credential revocation. Preserve existing credentials; never create/rotate credentials or change VPN, DNS, SSH/TLS policy without applicable OWNER authorization.
- Read the runbook once for the current incident. For the same unchanged failure, use its bounded diagnostics and retries without repeatedly reopening Linear. Re-read only for a materially different failure class or when the current procedure is known to have changed.
- A timed-out push/dispatch may have succeeded: read back the remote exact SHA and existing Actions runs before retrying. Never force-push or repeat an ambiguous write blindly.
- Before reporting `GITHUB_ACCESS_BLOCKED`, report whether the runbook was read and which applicable checks passed, failed, or could not be run. If the runbook cannot be retrieved, report `GITHUB_ACCESS_RUNBOOK_NOT_VERIFIED`; do not invent its steps or bypass a required protection. This policy does not authorize production Apply or broaden release permissions.

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

- `.github/workflows/maxibot-native-release.yml` is the only application and database release workflow. It has Tests, Build, and Deploy jobs; a protected `main` push runs them in order.
- PR checks do not deploy. The production job uses the `production` Environment and all existing protections. Dispatch is reserved for exact, authorized application rollback.
- Build once with BuildKit and publish to GHCR. Deploy verifies exact candidate SHA, immutable digest, platform and source labels, then uses the pinned stock Kamal CLI with `--skip-push`.
- Apply Alembic only for the exact pending migration set authorized by the current task contract. Verify the live DB head and schema, backup and isolated restore before Apply. Never downgrade or blindly repeat an uncertain migration.
- Preserve PostgreSQL, Redis and uploads volumes; verify database and Redis health, API/Worker SHA parity, Worker 512 MiB, one active MAX Poller, health endpoints and Worker heartbeat.
- Do not use Compose Apply, legacy release controllers, or local Mac/VPS deployment scripts. Keep GitHub Actions and Deployments as release execution/history; use Linear for owner decisions and task evidence.
- OWNER authorization is scoped to the named task and exact changes. A technical PASS is not a substitute for OWNER authorization or product acceptance.

## GitHub instruction publication — OWNER decision 2026-10-08

- Once the OWNER has approved the task, DEV may publish scoped edits to `AGENTS.md`, Codex instructions, and role/bootstrap documents directly to exact current `origin/main` without a mandatory PR or separate approval per ordinary instruction commit, **only when current repository controls permit direct writes**. A PR is optional or used when technically required, not a general instruction-publication gate. Do not reset, switch, stash, overwrite, or update another active chat's branch/worktree.
- Read the exact current main file/blob SHA and applicable policies before editing. Preserve concurrent changes, run relevant syntax, policy, security, frozen-path and independent QA gates where applicable, and record the exact published commit SHA. Never bypass branch protection or required reviews/checks; on a blocked direct write, stop and report a compliant alternative.
- An explicit, scope-specific OWNER authorization is still required for material changes to agent authority/autonomy, safety, OWNER-only operations, release rules, protected paths, access controls and other protected policies. The decision to eliminate mandatory PRs is **not** blanket approval for unrelated protected changes or production apply. Preserve MAXIBOT exact-SHA/digest verification, GitHub Environment protection, migration scope checks, and runtime gates.
- Before an OWNER-authorized `main` push, acknowledge that GitHub will attempt automatic production Deploy even for instruction-only changes. Verify the exact remote SHA and active-run state; after publication verify the new exact SHA and hosted workflow result, then synchronize Linear. Otherwise publish a branch/PR only; never represent this as production COMPLETE. Do not disturb existing Codex worktrees or credentials.

## Task completion

- Run relevant tests and the full applicable suite; run frozen-path and QA gates when affected. Distinguish code/test evidence from real device, production, and OWNER acceptance; report `NOT_VERIFIED` where evidence is missing.
- Leave the task worktree clean with committed/published changes or report remaining files, exact commit/PR and blocking gates. Do not bypass protected policy gates. A `main` push is production-triggering; branch-only work is not production SUCCESS.
