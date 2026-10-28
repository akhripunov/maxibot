# CURRENT MAXIBOT RELEASE / PRE-PUSH POLICY (2026-10-28)

For ordinary, OWNER-authorized production releases the executable source of truth is `.github/workflows/maxibot-native-release.yml`: a `push` to `main` runs hosted **Tests → Build/GHCR → Kamal Deploy → Runtime Verify** in the same run. GitHub `production` no longer requires manual reviewer approval. Codex and Mac are NOT stages after push. A manual `workflow_dispatch` is reserved for the explicitly authorized rollback operation, never routine deploy.

**Important:** every `main` push, including docs and `AGENTS.md`, can trigger a real production deployment. Do not push implementation-only or instruction-only changes to `main` without OWNER release authorization for that scope. A branch/PR is safe for review and must not be mistaken for a production release. GitHub CI and protected production safety gates remain mandatory; no bypass to save time. Historical ME-51, V2/V5, Compose and Mac-runner instructions below are archive, NOT the ordinary release procedure.

**Pre-push speed:** use the scope-based FAST/FULL decision in `docs/ORCHESTRATION_PROTOCOL.md` and the timing checklist in `docs/RELEASE_AUTOMATION.md`. Do not load old release archives, relaunch completed checks, or invoke ceremonial subagents for an unaffected path. Always read the current required GitHub access runbook before GitHub operations, check the exact remote SHA, preserve existing worktrees, and apply mandatory gates for changed paths.

---

## Historical exception — ME-51 first Compose → Kamal cutover (completed; not active)

For ME-51 only, the OWNER-authorized first Compose → Kamal cutover uses `.github/workflows/maxibot-first-kamal-cutover.yml` instead of the legacy `scripts/release_prod.sh` path. This exception supersedes conflicting V5/sole-mutation-path requirements only for that one transition and its documented recovery. The existing DevOps authority has been transferred by OWNER to Codex chat `01a1778b-db7d-7490-900e-90a71ee73f40` for ME-51.

Publish the scoped, clean, tested candidate only within OWNER's ME-51 authorization; require successful exact-SHA hosted Tests/Build, immutable GHCR digest/platform/source/SBOM/provenance verification, protected `production` Environment approval, and a successful fresh preflight before Apply. Preserve backup/isolated restore, single Poller, Worker 512 MiB, unchanged PostgreSQL/Redis/volumes, strict SSH, frozen-path/security gates and the documented Compose recovery baseline. `OWNER_ACCEPTED_UNROTATED` records accepted risk, never completed rotation. No direct ad/provider writes, GPoint changes, credential/network-policy changes, or gate bypass are authorized.

After verified first cutover, ordinary ME-51 release validation uses the existing `.github/workflows/maxibot-native-release.yml` protected stock Kamal `--skip-push` path with the same identity and runtime safety gates, following Linear «Правила релизов NEW». Legacy V5/Compose application mutation entrypoints must not operate concurrently on the same application containers; recovery material remains until OWNER confirms migration. This exception grants no permission for unrelated future releases and does not remove mandatory GitHub approval.


---

# MAXIBOT project instructions

## Project identity and canonical sources

- This repository is `akhripunov/maxibot`. GitHub exact commits are the durable source for code; Linear records project context, task decisions, and release evidence.
- Read [the role registry](docs/ROLE_REGISTRY.md), [orchestration protocol](docs/ORCHESTRATION_PROTOCOL.md), and the relevant MAXIBOT Linear sections before specialized work. Do not load the whole historical archive by default.
- Product and operational context: [MAXIBOT Project Context / AGENTS](https://linear.app/bestuzhev/document/maxibot-project-context-agents-1a98f6000c28), [Architect Handoff](https://linear.app/bestuzhev/document/arhitektor-handoff-bb75d67e4a95), and [MAXIBOT Workers](https://linear.app/bestuzhev/document/maxibot-workers-97f568bf84b1).
- Release sources are MAXIBOT's [Linear release rules](https://linear.app/bestuzhev/document/pravila-reliza-maxibot-825471a52588), [release journal](https://linear.app/bestuzhev/document/zhurnal-relizov-maxibot-3e615fe7e879), `docs/RELEASE_AUTHORIZATION.md`, and `docs/RELEASE_AUTOMATION.md`.
- GPoint and MAXIBOT are independent projects with separate authorization, workflows, runners, migrations, artifacts, release ledgers, Linear documents, and production environments. Never apply GPoint release procedures or authorization to MAXIBOT, or MAXIBOT release procedures or authorization to GPoint. Resolve each project's rules independently for cross-project work.

## Mandatory GitHub/GHCR access recovery — MAXIBOT

- For any MAXIBOT task involving GitHub, GHCR, a Git remote, GitHub Actions, push, workflow dispatch, or repository authentication, **read the current [Linear «GitHub доступ»](https://linear.app/bestuzhev/document/github-dostup-7970682d1b80) before the first relevant access operation**. It is a mandatory operating procedure, not optional background context. Use its safe steps for this repository and this host only; it does not transfer PROSTOR/GPoint credentials, runners, or authority to MAXIBOT.
- After a context compaction, on a newly encountered DNS/TLS/HTTPS/auth failure, or whenever the procedure is no longer reliably in context, **retrieve the current document again**. A remembered summary, an old quote, and a link without a successful read are not evidence that the procedure was followed.
- Before declaring `GITHUB_ACCESS_BLOCKED`, distinguish DNS/proxy/VPN/transport failure from genuine rejected credentials; `gh auth status` during a network failure is not sufficient proof of revocation. Apply the document's existing-credential recovery and bounded retry/readback steps without printing secrets. Report the document read status, checks attempted, and exact remaining failure. If Linear is unreachable, report `GITHUB_ACCESS_RUNBOOK_NOT_VERIFIED`; do not invent its steps.
- A timed-out push/dispatch may have succeeded: read back the remote exact SHA and existing Actions runs **before** retrying. Never force push, rotate/create credentials, relax host-key/TLS verification, or alter VPN/DNS/network settings without the applicable OWNER authorization. This instruction does not authorize production Apply or broaden release permissions.

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

- V2/V5 Release Orchestrator, Compose, `scripts/release_prod.sh` and Mac/controller are historical or recovery-specific; they are not the ordinary production mutation path. Keep historical implementation frozen unless separately authorized.
- The ordinary release is the single push-triggered hosted Tests → Build → Kamal Deploy → Runtime Verify workflow. Preserve exact SHA/digest, linux/amd64, provenance, frozen paths, single Poller, Worker 512 MiB, PostgreSQL/Redis/volumes, Alembic, backup/rollback and runtime safety gates. Do not infer owner/product acceptance from technical PASS.
- A development-only task does not authorize a `main` push because it would automatically deploy. OWNER must authorize publication and resulting release for the named scope. Never borrow GPoint authorization. Do not directly publish instruction-only changes to `main` without that authorization; use a PR instead.
- Do not manually dispatch routine Deploy, rerun an uncertain/partial Apply, or use an old Mac controller. The manually dispatched rollback operation remains owner-scoped and safety-gated. Check active runs and remote SHA before writes; browser UI is not a release execution tool. Untrusted PR/fork code must never have production secrets.

## GitHub instruction publication — OWNER decision 2026-10-08

- Once the OWNER has approved the task, DEV may publish scoped edits to `AGENTS.md`, Codex instructions, and role/bootstrap documents directly to exact current `origin/main` without a mandatory PR or separate approval per ordinary instruction commit, **only when current repository controls permit direct writes**. A PR is optional or used when technically required, not a general instruction-publication gate. Do not reset, switch, stash, overwrite, or update another active chat's branch/worktree.
- Read the exact current main file/blob SHA and applicable policies before editing. Preserve concurrent changes, run relevant syntax, policy, security, frozen-path and independent QA gates where applicable, and record the exact published commit SHA. Never bypass branch protection or required reviews/checks; on a blocked direct write, stop and report a compliant alternative.
- An explicit, scope-specific OWNER authorization is still required for material changes to agent authority/autonomy, safety, OWNER-only operations, release rules, protected paths, access controls and other protected policies. The decision to eliminate mandatory PRs is **not** blanket approval for unrelated protected changes or production apply. Preserve MAXIBOT-only exact-SHA/TASK_SCOPED_AUTO rules and all independent gates.
- Before an OWNER-authorized `main` push, acknowledge that GitHub will attempt automatic production Deploy even for instruction-only changes. Verify the exact remote SHA and active-run state; after publication verify the new exact SHA and hosted workflow result, then synchronize Linear. Otherwise publish a branch/PR only; never represent this as production COMPLETE. Do not disturb existing Codex worktrees or credentials.

## Task completion

- Run relevant tests and the full applicable suite; run frozen-path and QA gates when affected. Distinguish code/test evidence from real device, production, and OWNER acceptance; report `NOT_VERIFIED` where evidence is missing.
- Leave the task worktree clean with committed/published changes or report remaining files, exact commit/PR and blocking gates. Do not bypass protected policy gates. A `main` push is production-triggering; branch-only work is not production SUCCESS.
