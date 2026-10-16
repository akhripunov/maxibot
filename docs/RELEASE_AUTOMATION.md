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

## Статус миграции хранения — подтверждён 07.10.2026

**PASS / VERIFIED для проверенной цепочки MAXIBOT:** на SHA
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
gates; эта запись не является бессрочным разрешением Deploy.

Публикатор использует штатный workflow `GITHUB_TOKEN` (`contents: write`) для
создания тега, черновика, загрузки bundle и публикации Release. После публикации
обязательный API read-back через тот же workflow token проверяет
`draft=false`, `immutable=true`, tag на точный SHA, загруженный asset и
совпадение GitHub SHA256 digest с локальными байтами. Если `immutable=true`
отсутствует или равно false, шаг публикации завершается ошибкой и весь Prepare
имеет FAILED; Linear не отмечает candidate как READY, а Deploy не может выбрать
bundle без успешного Prepare и повторного immutable read-back. Внутренний receipt
отражает только результаты CI/build/rehearsal и сам по себе не разрешает Deploy.
Это требует включить repository immutable Releases пользователю с
`Administration: write`; checksum и workflow token эту проверку не заменяют. Без успешных проверок нового конкретного кандидата не считать его production release readiness доказанной.

## Порядок для владельца

1. Dev отправляет commit в `origin/main`. GitHub запускает [MAXIBOT — Prepare](https://github.com/akhripunov/maxibot/actions/workflows/maxibot-prepare.yml): полную pytest suite, frozen-path guard и единственную сборку exact `linux/amd64` image. Prepare использует pinned Docker 29.1.3 с private containerd archive-import rehearsal; проверяет config checksum, platform, source labels и различие native image ID/config digest. Synthetic 256/320/512 MiB allocation больше не является blocking Prepare gate: обязательная memory-защита выполняется read-only на реальном Worker во время preflight/postdeploy и требует Docker и kernel cgroup лимит ровно 512 MiB, `OOMKilled=false`, неизменный restart count и advancing primary heartbeat за 35 секунд. Receipt связывает PASS с SHA, Prepare run ID/attempt и checksum архива. После CI gates Prepare создаёт уникальный tag `maxibot-prepare-<sha>-<run>-<attempt>` и публикует `bundle.zip` с image archive, manifest и receipt. Затем Release metadata read-back обязан подтвердить published state, `immutable=true` и GitHub asset digest. Новые большие Prepare bundles не хранятся в Actions Artifacts. `CI_PASS` появляется в Linear только после завершения публикации.
2. После успешного exact-main Prepare `workflow_run` controller на уже существующем MAXIBOT Mac runner проверяет SHA, immutable Release/tag/asset/digest и отсутствие активного Deploy. Если единственная валидная durable authorization покрывает весь diff и baseline, controller атомарно claim-ит её и вызывает существующий `MAXIBOT — Deploy` в `task-scoped-auto`. Без неё он оставляет обычный exact-SHA approval path и ничего не применяет. Новые hosted controller jobs и повторные tests/builds не создаются.
3. Deploy validator проверяет successful exact-SHA Prepare, immutable artifact/provenance, разрешение (если task-auto), baseline и защиту от повторного запуска. После runner readiness и artifact verification он выполняет canonical `scripts/release_prod.sh SHA --dry-run` как read-only preflight непосредственно перед Apply. При PASS критичные условия повторно проверяются под production lock, затем вызывается `scripts/release_prod.sh SHA --apply`. Image не пересобирается на Mac/VPS.
4. Ручной `preflight` режим остаётся диагностическим и при успехе публикует `READY_FOR_OWNER`; внутренний preflight production/task-auto публикует `PASS`, не READY. Результат и allowlisted preflight/apply evidence публикуются отдельным immutable Release `maxibot-evidence-<sha>-<run>-<attempt>`; Prepare Release остаётся неизменным. Если apply прошёл, а evidence publication упала, не запускайте `production` повторно. `publication-recovery` сверяет точный исходный run/attempt и выполняет только read-only сверку; повторное применение запрещено.

State machine: без предварительной auth — `PREPARING → READY_FOR_OWNER` после отдельного ручного диагностического preflight либо ожидание exact-SHA approval; с валидной task-auto — `PREPARING → DEPLOYING → VERIFYING → COMPLETE`, причём Deploy включает `PREFLIGHTING` до любого Apply. Ошибки переходят в `PREPARE_FAILED`, `PREFLIGHT_FAILED`, `FAILED_PRE_SWITCH`, `RECOVERY_REQUIRED` или `PUBLICATION_RECOVERY_REQUIRED`. Claim refs защищают от повторного Deploy, concurrency lock сериализует runtime apply, а неопределённый dispatch оставляет claim для reconciliation. `FAILED_PRE_SWITCH` означает, что production mutation не началась. `RECOVERY_REQUIRED` требует read-only сверки; apply вслепую не повторяется. При пропавшем Mac runner job останется в очереди.

Runner telemetry публикует hosted/self-hosted job count и измеренные wall-clock seconds (не billable minutes), `DUPLICATE_HOSTED_WORK_REMOVED` и `CACHE_PREVENTED_DOWNLOAD`. Pipeline controller использует существующий self-hosted runner; Prepare и image build выполняются один раз, Deploy использует persistent project cache и проверяет release metadata/hash при каждом чтении.

## Где смотреть

- GitHub run и первый упавший шаг: ссылки **MAXIBOT — Prepare** / **MAXIBOT — Deploy** выше.
- Текущий SHA и статус кандидата/preflight/production: [Архитектор — Handoff](https://linear.app/bestuzhev/document/arhitektor-handoff-bb75d67e4a95).
- История отдельных попыток: [Журнал релизов MAXIBOT](https://linear.app/bestuzhev/document/zhurnal-relizov-maxibot-3e615fe7e879).

Publisher обновляет только именованные MAXIBOT release-блоки в Handoff и добавляет идемпотентные события в журнал; архитектурный контекст владельца сохраняется. Публикуются SHA, run, статус и безопасные migration/readback факты. Секреты, production `.env`, SSH key и пользовательские сообщения в Linear не передаются. Repository secret `LINEAR_API_KEY` доступен только шагам публикации.

Повторные и неуспешные публикации сохраняются по `run_id`/`run_attempt`. Bookkeeping не коммитит и не отправляет `main`; публикационная сверка read-only и не вызывает runtime apply. Исторические Actions result artifacts остаются доступны для reconciliation; новые обязательные bundles/evidence хранятся в immutable Releases.

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

Prepare exports the single verified linux/amd64 image as gzip level 1; the
outer immutable Release bundle ZIP uses stored entries. Mac verification and
VPS checksum verification use the same image archive bytes. Deploy never
rebuilds an application image.

Release tags contain candidate SHA, Prepare run ID and attempt. Before using
`~/.cache/maxibot-release/artifacts`, Deploy checks fresh GitHub main/run/Release
metadata and the asset's published SHA-256. Missing/draft/mutable Releases,
changed attempts and mismatches fail closed. The cache retains at most three ZIPs / 1
GiB (a selected oversized ZIP is retained alone). Portable Python 3.12.10 uses
a separate persistent project tool cache. No caches dirty the candidate checkout.

VPS image reuse is allowed only when the saved archive checksum and loaded
config ID still match and image platform plus all three source labels pass.
Fresh candidates still use canonical upload/load. The release wrapper streams
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
