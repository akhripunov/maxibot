## CURRENT OWNER TASK AUTHORIZATION — 2026-10-20

MAXIBOT follows the current GPoint V5 full-cycle OWNER-task model in
`docs/RELEASE_AUTHORIZATION.md`. A direct task to release the named scope covers
PR/merge, Prepare, controller, Deploy and verification, including reviewed
replacement SHAs within the unchanged scope and production baseline. No repeated
human exact-SHA approval is required for that task. Release infrastructure is
eligible with its complete explicit risk contract. Linear publishes evidence;
the committed direct OWNER task record supplies authorization. Older statements
below about mandatory separate infrastructure approval are historical.

Keep one immutable task activation claim and per-Prepare dispatch guards.
Only confirmed safe failure permits continuation; uncertain dispatch or runtime
requires read-only reconciliation. All security, DB, Worker, provenance,
backup, rollback, lock and health gates remain mandatory.

# Автоматический выпуск MAXIBOT

## Два режима разрешения выпуска

Актуальные условия: [Release authorization modes](RELEASE_AUTHORIZATION.md).
По умолчанию владелец подтверждает подготовленный exact SHA. Конкретное
поручение владельца «исправь [задачу] и выкатывай» разрешает один
`TASK_SCOPED_AUTO` release без повторного подтверждения. До реализации
фиксируется schema-v3 durable authorization record. После exact-SHA Prepare
existing controller перепроверяет baseline/scope, атомарно claim-ит разрешение
и вызывает существующий Deploy workflow. Этот Deploy выполняет read-only
preflight непосредственно перед Apply; отдельный обязательный preflight-run не
создаётся. Без валидной OWNER release task сохраняется exact-SHA approval fallback.
Release infrastructure допускается по полному явно разрешённому риск-контракту
в durable OWNER task без повторного подтверждения SHA. Неподдержанные owner-only
категории блокируются; DB-миграция допускается только с конкретным риск-контрактом
и всеми штатными миграционными gates. Runner может штатно ждать в `Queued`; readiness check
остаётся fail-closed.
Подробности: [Release authorization modes](RELEASE_AUTHORIZATION.md).

Процесс оборачивает существующий `scripts/release_prod.sh`; он остаётся единственной production-командой. Workflow не управляет Direct/VK кампаниями, бюджетом, ставками, объявлениями или `ADS_*` master flags.

## Историческая проверка GitHub Releases — 07.10.2026

Историческое подтверждение прежней схемы GitHub Releases на SHA
`44c19ab59571ccb663ebf5c6d4de49fa5a866239` завершены Prepare
[#37631234823](https://github.com/akhripunov/maxibot/actions/runs/37631234823),
automatic controller
[#37632023233](https://github.com/akhripunov/maxibot/actions/runs/37632023233)
и read-only preflight
[#37632079388](https://github.com/akhripunov/maxibot/actions/runs/37632079388)
со статусом `READY_FOR_OWNER`. Публичное состояние проверенных GitHub Releases:
`draft=false`, `immutable=true`, published bundle/evidence asset с GitHub SHA-256
digest. Подробности и ограничения proof — в текущем каноническом разделе
[Правила релиза MAXIBOT](https://linear.app/bestuzhev/document/pravila-reliza-maxibot-825471a52588).
Production apply для указанного SHA не выполнялся и не требуется только ради proof.
Каждый **следующий** candidate независимо проходит immutable/identity/approval
gates. Эта запись не описывает актуальный V5 кандидат и не разрешает будущий Deploy.

## Целевая V5 схема

Локальная реализация переводит MAXIBOT image, Prepare receipt и Deploy evidence
в уже связанный с MAXIBOT repository GHCR package. OCI image, receipt и evidence
различаются по типам манифеста и уникальным SHA/run/attempt tags; workflow token
имеет write-доступ через существующую связь package с repository. Каждый payload
привязан к SHA/run/attempt, а publisher сверяет публикацию повторным read-back
по immutable digest. Docker
archive остаётся только для private containerd rehearsal.

После успешного read-only production preflight Deploy создаёт GitHub Deployment
для `maxibot-production` с exact SHA, migration, mode, run/attempt и Prepare
 receipt digest. Только затем запускает canonical Apply. Непосредственно перед
 pull runner обменивает `GITHUB_TOKEN` на краткоживущий GHCR Bearer token,
 ограниченный `pull`-доступом к image package, и передаёт его VPS через stdin
 SSH. VPS тянет `repository@sha256:...`, сверяет config digest, platform и
 source labels, затем удаляет временный root-only Docker config. Полный workflow
 token остаётся на runner; registry token не попадает в аргументы, результат
 workflow или Linear. Deployment status и GHCR evidence независимо проверяются
 перед обновлением Linear.

Если публикация evidence/status не завершилась после Apply, повторный Apply
запрещён. `publication-recovery` проверяет точный runtime и БД read-only, затем
исправляет status существующего Deployment без production mutation. До отдельной
разрешённой публикации реализации production использует прежнюю схему; эта
локальная миграция сама по себе не меняет production.

## Порядок для владельца

1. Только в отдельной разрешённой подготовке Dev публикует commit в `origin/main`. GitHub запускает [MAXIBOT — Prepare](https://github.com/akhripunov/maxibot/actions/workflows/maxibot-prepare.yml): обязательную suite, frozen-path guard, exact linux/amd64 build, private Docker rehearsal и GHCR publication/read-back. Успешный V5 Prepare фиксируется OCI digest receipt; `CI_PASS` появляется в Linear только после публикации.
2. После успешного exact-main Prepare `workflow_run` controller на MAXIBOT Mac runner проверяет SHA, GHCR OCI receipt/image digest, image labels и отсутствие активного Deploy. Если единственная валидная durable authorization покрывает eligible scope и baseline, controller атомарно claim-ит её и вызывает существующий `MAXIBOT — Deploy` в `task-scoped-auto`. Без неё сохраняется exact-SHA approval path.
3. Deploy validator проверяет successful exact-SHA Prepare, OCI receipt/provenance, разрешение (если task-auto), baseline и защиту от повторного запуска. После runner readiness и image digest verification он выполняет canonical `scripts/release_prod.sh SHA --dry-run` как read-only preflight непосредственно перед Apply. При PASS создаётся GitHub Deployment, затем вызывается `scripts/release_prod.sh SHA --apply`. Image не пересобирается на Mac/VPS.
4. Ручной `preflight` остаётся диагностическим и при успехе публикует `READY_FOR_OWNER`; внутренний preflight production/task-auto публикует `PASS`, не READY. Результат публикуется как отдельный immutable GHCR OCI evidence digest. Если Apply прошёл, а evidence/status publication упала, не запускайте production повторно. `publication-recovery` сверяет exact исходный run/attempt и исправляет metadata только после read-only runtime reconciliation.

State machine: без предварительной auth — `PREPARING → READY_FOR_OWNER` после отдельного ручного диагностического preflight либо ожидание exact-SHA approval; с валидной task-auto — `PREPARING → DEPLOYING → VERIFYING → COMPLETE`, причём Deploy включает `PREFLIGHTING` до любого Apply. Ошибки переходят в `PREPARE_FAILED`, `PREFLIGHT_FAILED`, `FAILED_PRE_SWITCH`, `RECOVERY_REQUIRED` или `PUBLICATION_RECOVERY_REQUIRED`. Claim refs защищают от повторного Deploy, concurrency lock сериализует runtime apply, а неопределённый dispatch оставляет claim для reconciliation. `FAILED_PRE_SWITCH` означает, что production mutation не началась. `RECOVERY_REQUIRED` требует read-only сверки; apply вслепую не повторяется. При пропавшем Mac runner job останется в очереди.

Runner telemetry публикует hosted/self-hosted job count и измеренные wall-clock seconds (не billable minutes), `DUPLICATE_HOSTED_WORK_REMOVED` и `CACHE_PREVENTED_DOWNLOAD`. Pipeline controller использует существующий self-hosted runner; Prepare и image build выполняются один раз, Deploy использует persistent project cache и проверяет release metadata/hash при каждом чтении.

## Bounded network retry

All retries are for idempotent reads only and share the existing 30-minute
workflow budget through `NETWORK_RETRY_BUDGET_STATE`. Backoff is exponential,
jittered, and capped at 60 seconds. A fresh response is rechecked against the
current main SHA, exact Prepare run/attempt, immutable GHCR digest, and existing
authorization/conflict gates after each successful retry.

| Phase / operation | Endpoint and method | Idempotent | Retry / budget | Failure handling |
|---|---|---:|---|---|
| Runner bootstrap (before project scripts) | GitHub Actions service, action archives, `codeload.github.com`, pinned ORAS/action downloads; runner/platform GETs | Read-only | No project retry code is loaded yet; only the runner/action's own bounded behavior applies. Not charged to `NETWORK_RETRY_BUDGET_STATE`. | Bootstrap failure ends the job. Keep actions pinned and TLS verification enabled; do not claim Python retries can repair it. |
| Prepare coalescing | `GET api.github.com/repos/.../git/ref/heads/main` before and after quiet window | Yes | Retry classified transient network/TLS failures through the same shared 30-minute workflow budget. | Do not proceed with stale/unknown `main`; auth, non-transient HTTP and malformed responses fail closed. |
| Candidate / authorization / Prepare provenance | GitHub API `GET` for main ref, authorization refs, workflow runs, exact run metadata | Yes | Retry classified transient network/TLS EOF and HTTP 408/425/429/5xx through the shared 30-minute workflow budget. | 401/403, TLS verification failure, malformed response, changed SHA/run/attempt, invalid provenance or authorization mismatch fail closed. |
| Runner readiness | GitHub API `GET` plus fixed production host SSH readiness `printf` probe | Yes | Retry selected temporary network errors through the same budget; SSH attempts remain bounded and use one connection attempt each. | Auth, host-key and non-transient SSH failures fail closed. No production command is sent by the readiness probe. |
| GHCR artifact and image verification | Registry token, OCI manifest/blob GETs, and Docker pull by exact image digest | Yes | Retry classified transient reads and digest-pinned Docker pull through the shared budget and absolute deadline. Docker stderr is captured and never emitted. | Never relax repository scope, SHA/run/attempt identity, descriptor/layer/image digest or `linux/amd64` checks; auth and verification failures fail closed. |
| Cached GitHub artifact ZIP | GitHub Actions artifact API GET, signed HTTPS object-storage GET, parallel byte-range GETs | Yes | Retry reads through one shared budget and the download's absolute deadline. Parallel ranges charge the union of overlapping retry intervals, not the sum of worker durations. Obtain a fresh signed redirect on a new API attempt; never send the GitHub bearer token to storage. | Enforce byte/range bounds and exact artifact identity; expired or malformed redirects fail closed. |
| Legacy GitHub Release metadata / evidence reads | `gh api` / `gh release` readback and GitHub Release asset GET | Yes | No common retry wrapper in these legacy paths. | Fail closed on unavailable or inconsistent metadata; no retry claim is made for these paths. |
| GHCR / GitHub publication | ORAS login/push; Git tag and Release create/upload/edit; GitHub ref create/claim/consume; Deployment create/status | No (writes) | No blind retry and no shared read budget. | On uncertain outcome, read back exact tag/ref/run/deployment identity and reconcile. Never repeat a publication or claim based only on a timeout. |
| Production mutation | Canonical `release_prod.sh` over fixed-host SSH, Apply and Alembic migration | No (mutation) | No automatic network retry. | Preserve backup, lock, rollback and health gates. An uncertain result requires read-only reconciliation; never automatically repeat Apply or migration. |
| Linear / release bookkeeping | Release status and history publication | Write | No retry wrapper in the release client. | Report publication failure and reconcile the existing run/evidence; do not rerun Deploy. |

`NETWORK_UNAVAILABLE` means the bounded window expired. Budget accounting is
shared across concurrent reads: overlapping retry intervals consume only their
union of wall-clock time. HTTP authorization
failures and verification failures keep their safe, specific categories and do
not consume repeated attempts.

## Где смотреть

- GitHub run и первый упавший шаг: ссылки **MAXIBOT — Prepare** / **MAXIBOT — Deploy** выше.
- Текущий SHA и статус кандидата/preflight/production: [Архитектор — Handoff](https://linear.app/bestuzhev/document/arhitektor-handoff-bb75d67e4a95).
- История отдельных попыток: [Журнал релизов MAXIBOT](https://linear.app/bestuzhev/document/zhurnal-relizov-maxibot-3e615fe7e879).

Publisher обновляет только именованные MAXIBOT release-блоки в Handoff и добавляет идемпотентные события в журнал; архитектурный контекст владельца сохраняется. Публикуются SHA, run, статус и безопасные migration/readback факты. Секреты, production `.env`, SSH key и пользовательские сообщения в Linear не передаются. Repository secret `LINEAR_API_KEY` доступен только шагам публикации.

Повторные и неуспешные публикации сохраняются по `run_id`/`run_attempt`. Bookkeeping не коммитит и не отправляет `main`; publication recovery read-only и не вызывает runtime apply. Исторические GitHub Releases и Actions result artifacts остаются доступны только для старых попыток; новые images, receipts и evidence хранятся в GHCR по digest.

## Ограничения runner

Для MAXIBOT требуется отдельная repository-scoped регистрация runner с label `maxibot-production`. На этом Mac она может работать под тем же пользователем, что и GPoint. Использовать отдельную папку runner и work directory без пробелов, например `~/.local/share/maxibot-release-work`. Рабочие каталоги и runner не переиспользовать между регистрациями. MAXIBOT runner имеет доступ к `~/.ssh/maxibot_selectel_ed25519`; workflow назначает его только после проверки exact main SHA и успешного Prepare. Никогда не запускать на нём workflow из pull request/fork.

Регистрировать runner следует из Settings → Actions → Runners в репозитории MAXIBOT; temporary registration token не сохранять. Предварительный API опрос runner не требуется: GitHub `Queued` является штатным ожиданием и сам по себе не означает сбой. После старта job выполняется fail-closed readiness check runner identity/labels, macOS/ARM64, disk, cache/runtime paths, required tools/network и отсутствия конфликтующей production mutation до загрузки bundle или дорогих шагов. Docker CLI/daemon на Mac не нужен: контейнерные операции выполняются на VPS через SSH после разрешённого preflight. `Queued` не означает успешный preflight или выкладку.

Проверки и runner используют Linux/macOS user environment с production MAX polling выключенным для тестов; единственный production poller остаётся на VPS `worker`. Production SSH key и `.env` остаются на Mac/VPS. Ветки PR, пользовательский Telegram smoke и реклама автоматикой не меняются.

## Release performance and finalization

For a clean release benchmark, use comparable candidates and measure
commit/push, Prepare, automatic controller, Deploy preflight/apply/verify, and
Linear publication timestamps separately. Count workflow jobs and measured
runner seconds from the GitHub API; record cache hit/miss and repeated work.
An exact-SHA-only preparation run may include a manual diagnostic preflight;
an authorized task-auto production run must contain one in-Deploy preflight
before Apply and no separate mandatory preflight run.

Prepare rehearses the single verified linux/amd64 image through its private
archive import, then publishes the image to GHCR and verifies the immutable
manifest digest. Deploy pulls that exact digest and compares config digest,
platform and source labels. Deploy never rebuilds an application image.

GHCR tags contain candidate SHA, Prepare run ID and attempt. Before using the
receipt, Deploy checks fresh GitHub main/run metadata and verifies the selected
OCI digest read-back. Missing packages, changed attempts and manifest/payload
mismatches fail closed. Portable Python 3.12.10 uses a separate persistent
project tool cache. No caches dirty the candidate checkout.

VPS image reuse is allowed only when the saved OCI manifest digest and loaded
config ID still match and image platform plus all three source labels pass.
Fresh candidates use canonical GHCR pull-by-digest. The release wrapper streams
output immediately; upload, load, migration, runtime switch, health and ref
finalization emit timings, which are also saved in the workflow result.

Finalization supports a clean detached HEAD by checking the exact candidate
commit. The immutable release ref is pushed with a compare-and-swap lease;
a pre-existing ref for another SHA blocks finalization. Branch names are never
used as production identity. Backups and all safety gates remain mandatory.

Deploy publishes immutable runtime evidence before the separate Ubuntu Linear
job. Publication failures remain visible failed workflows. `publication-recovery`
rechecks live production read-only and cannot switch runtime. Events and evidence
include the GitHub attempt, preserving earlier history.

Проверка loaded image выполняется общим Python helper в Prepare и на VPS: читаются только manifest/config content blobs через ctr. Полный повторный docker save не выполняется; приложение в identity rehearsal не запускается. Измеренная длительность записывается в receipt.
