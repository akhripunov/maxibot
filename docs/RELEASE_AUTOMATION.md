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
создаётся. Без разрешения сохраняется exact-SHA approval path. Release infrastructure и
неподдержанные owner-only категории остаются за отдельным exact-SHA approval;
DB-миграция допускается только с конкретным риск-контрактом и всеми штатными
миграционными gates. Runner может штатно ждать в `Queued`; readiness check
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
в отдельные GHCR OCI packages. Каждый payload привязан к SHA/run/attempt, а
publisher сверяет публикацию повторным read-back по immutable digest. Docker
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

## Где смотреть

- GitHub run и первый упавший шаг: ссылки **MAXIBOT — Prepare** / **MAXIBOT — Deploy** выше.
- Текущий SHA и статус кандидата/preflight/production: [Архитектор — Handoff](https://linear.app/bestuzhev/document/arhitektor-handoff-bb75d67e4a95).
- История отдельных попыток: [Журнал релизов MAXIBOT](https://linear.app/bestuzhev/document/zhurnal-relizov-maxibot-3e615fe7e879).

Publisher обновляет только именованные MAXIBOT release-блоки в Handoff и добавляет идемпотентные события в журнал; архитектурный контекст владельца сохраняется. Публикуются SHA, run, статус и безопасные migration/readback факты. Секреты, production `.env`, SSH key и пользовательские сообщения в Linear не передаются. Repository secret `LINEAR_API_KEY` доступен только шагам публикации.

Повторные и неуспешные публикации сохраняются по `run_id`/`run_attempt`. Bookkeeping не коммитит и не отправляет `main`; publication recovery read-only и не вызывает runtime apply. Исторические GitHub Releases и Actions result artifacts остаются доступны только для старых попыток; новые images, receipts и evidence хранятся в GHCR по digest.

## Ограничения runner

Для MAXIBOT требуется отдельная repository-scoped регистрация runner с label `maxibot-production`. На этом Mac она может работать под тем же пользователем, что и GPoint. Использовать отдельную папку runner и work directory без пробелов, например `~/.local/share/maxibot-release-work`. Рабочие каталоги и runner не переиспользовать между регистрациями. MAXIBOT runner имеет доступ к `~/.ssh/maxibot_selectel_ed25519`; workflow назначает его только после проверки exact main SHA и успешного Prepare. Никогда не запускать на нём workflow из pull request/fork.

Регистрировать runner следует из Settings → Actions → Runners в репозитории MAXIBOT; temporary registration token не сохранять. Предварительный API опрос runner не требуется: GitHub `Queued` является штатным ожиданием и сам по себе не означает сбой. После старта job выполняется fail-closed readiness check runner identity/labels, macOS/ARM64, Docker Linux runtime, disk, cache/runtime paths, required tools/network и отсутствия конфликтующей production mutation до загрузки bundle или дорогих шагов. `Queued` не означает успешный preflight или выкладку.

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
