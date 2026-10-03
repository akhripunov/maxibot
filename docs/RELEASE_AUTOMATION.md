# Автоматический выпуск MAXIBOT

## Два режима разрешения выпуска

Актуальные условия: [Release authorization modes](RELEASE_AUTHORIZATION.md).
По умолчанию владелец подтверждает подготовленный exact SHA. Для небольшой
конкретной задачи «Исправь [задачу] и выкатывай» разрешает один Task-scoped Auto
Release без повторного запроса SHA. Dev фиксирует scope, полный unreleased diff,
разрешение, exact SHA и проверки в Linear до apply и передаёт существующий
production approval input по этому разрешению. Рискованные изменения, миграции,
провал gates или расширение scope останавливают этот режим. Инструкции ручного
согласования ниже описывают обычный Exact-SHA режим. Все технические проверки
одинаковы в обоих режимах; это не автоматический deploy каждого push.

Процесс оборачивает существующий `scripts/release_prod.sh`; он остаётся единственной production-командой. Workflow не управляет Direct/VK кампаниями, бюджетом, ставками, объявлениями или `ADS_*` master flags.

## Порядок для владельца

1. Dev отправляет готовый commit в `origin/main`. GitHub запускает [MAXIBOT — Prepare](https://github.com/akhripunov/maxibot/actions/workflows/maxibot-prepare.yml): полную pytest suite, frozen-path guard и единственную сборку exact `linux/amd64` image. Prepare использует pinned Docker 29.1.3 с containerd snapshotter, импортирует созданный archive в отдельный пустой Docker store с private containerd и изолированной сетью; проверяет manifest/config checksum, platform, source labels, rootfs diff IDs и native container `.Image`, а прежнее сравнение native ID с config digest обязано завершиться отказом. Обязательный receipt связывает PASS с SHA, Prepare run ID и checksum архива; старый receipt не допускается к Deploy. Prepare публикует image tar и manifest отдельным artifact, названным по SHA, run ID и attempt. Manifest содержит SHA-256 архива, image config digest, platform и source labels. Результат `CI_PASS` означает, что код и image прошли CI; он сам по себе ещё не разрешает выкладку.
2. Dev сообщает полный SHA и точный Alembic target. В [MAXIBOT — Deploy](https://github.com/akhripunov/maxibot/actions/workflows/maxibot-deploy.yml) владелец выбирает `main`, вставляет эти значения и запускает `preflight`. Runner требует artifact именно от последнего успешного Prepare для текущего `main`, скачивает и проверяет SHA-256, config digest, platform и source labels прямо внутри immutable Docker archive. Mac Docker daemon не загружает и не пересобирает image. Затем runner вызывает canonical `scripts/release_prod.sh SHA --dry-run` с production SSH identity, передавая ему проверенный archive. Результат `READY` / `SAFE_TO_RELEASE=YES` оставляет production без изменений.
3. Владелец повторно запускает форму с тем же SHA и migration, выбирает `production`, отмечает **I authorize this exact candidate and Alembic target on MAXIBOT production** и нажимает **Run workflow**. Runner заново скачивает и проверяет тот же immutable image из Prepare run, повторяет provenance и dry-run, передаёт verified image archive в `scripts/release_prod.sh SHA --apply`. Canonical script импортирует archive только после этих проверок; на VPS он сверяет platform/source labels и API/worker parity, а native archive rehearsal выполняется в Prepare до production. Он не собирает image из исходников.
4. `COMPLETE` означает, что canonical скрипт проверил production SHA, Alembic, API/worker image parity, `linux/amd64`, health и polling topology. После выкладки выполните требуемую QA/regression и живой продуктовый smoke. Workflow не включает рекламу и не создаёт ad writes.

`FAILED_PRE_SWITCH` означает, что мутация production не началась. `RECOVERY_REQUIRED` означает, что после разрешения миграции apply не завершился доказанным `RELEASE_STATUS=PASS`: сначала выполнить read-only сверку и не повторять apply вслепую. При пропавшем Mac runner job останется в очереди, пока владелец не включит runner/Mac. Один и тот же SHA должен быть текущим `main` и иметь последний успешный Prepare; новый commit автоматически делает старую форму непригодной.

## Где смотреть

- GitHub run и первый упавший шаг: ссылки **MAXIBOT — Prepare** / **MAXIBOT — Deploy** выше.
- Текущий SHA и статус кандидата/preflight/production: [Архитектор — Handoff](https://linear.app/bestuzhev/document/arhitektor-handoff-bb75d67e4a95).
- История отдельных попыток: [Журнал релизов MAXIBOT](https://linear.app/bestuzhev/document/zhurnal-relizov-maxibot-3e615fe7e879).

Publisher обновляет только именованные MAXIBOT release-блоки в Handoff и добавляет идемпотентные события в журнал; архитектурный контекст владельца сохраняется. Публикуются SHA, run, статус и безопасные migration/readback факты. Секреты, production `.env`, SSH key и пользовательские сообщения в Linear не передаются. Repository secret `LINEAR_API_KEY` доступен только шагам публикации.

Повторные и неуспешные попытки сохраняются как отдельные GitHub artifacts и Linear journal events по `run_id`/`run_attempt`. Bookkeeping не коммитит и не отправляет `main`, а retry должен повторно использовать тот же exact candidate SHA и provenance artifact; новая сборка для retry не требуется.

## Ограничения runner

Для MAXIBOT требуется отдельная repository-scoped регистрация runner с label `maxibot-production`. На этом Mac она может работать под тем же пользователем, что и GPoint. Использовать отдельную папку runner и work directory без пробелов, например `~/.local/share/maxibot-release-work`. Рабочие каталоги и runner не переиспользовать между регистрациями. MAXIBOT runner имеет доступ к `~/.ssh/maxibot_selectel_ed25519`; workflow назначает его только после проверки exact main SHA и успешного Prepare. Никогда не запускать на нём workflow из pull request/fork.

Регистрировать runner следует из Settings → Actions → Runners в репозитории MAXIBOT; temporary registration token не сохранять. На GitHub проверить `maxibot-owner-mac` / `maxibot-production` = `Idle`. Если пользовательский сервис не работает, production job остаётся `Queued` — это не успешная выкладка.

Проверки и runner используют Linux/macOS user environment с production MAX polling выключенным для тестов; единственный production poller остаётся на VPS `worker`. Production SSH key и `.env` остаются на Mac/VPS. Ветки PR, пользовательский Telegram smoke и реклама автоматикой не меняются.

## Release performance and finalization

Prepare exports the single verified linux/amd64 image as gzip level 1; the
outer GitHub artifact ZIP uses compression level 0. Both Mac verification and
VPS checksum verification use the same compressed bytes. Application images
are never rebuilt by Deploy.

Artifact names contain candidate SHA, Prepare run ID and attempt. Before using
`~/.cache/maxibot-release/artifacts`, Deploy checks fresh GitHub main/run/artifact
metadata and the ZIP's published SHA-256. Missing/expired artifacts, changed
attempts and mismatches fail closed. The cache retains at most three ZIPs / 1
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

Deploy preserves runtime evidence before the separate Ubuntu Linear job.
Publication failures remain visible failed workflows; they do not replace the
runtime result. If runtime is already complete, retry the publication job
instead of switching production again. All events and result artifacts include
the GitHub attempt, preserving earlier history.

Проверка loaded image выполняется общим Python helper в Prepare и на VPS: читаются только manifest/config content blobs через ctr. Полный повторный docker save не выполняется; приложение в identity rehearsal не запускается. Измеренная длительность записывается в receipt.
