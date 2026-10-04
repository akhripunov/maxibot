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

## Статус миграции хранения

Переход новых MAXIBOT Prepare bundles и Deploy evidence с Actions Artifacts на
immutable GitHub Releases ещё не прошёл полный реальный Prepare и Deploy
preflight. До фактического PASS цепочки статус **NOT VERIFIED**; наличие кода
само по себе не доказывает, что Release становится immutable.

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
`Administration: write`; checksum и workflow token эту проверку не заменяют. До
подтверждения нового маршрута не считать production release readiness доказанной.

## Порядок для владельца

1. Dev отправляет commit в `origin/main`. GitHub запускает [MAXIBOT — Prepare](https://github.com/akhripunov/maxibot/actions/workflows/maxibot-prepare.yml): полную pytest suite, frozen-path guard и единственную сборку exact `linux/amd64` image. Prepare использует pinned Docker 29.1.3 с private containerd rehearsal и изолированной сетью; проверяет config checksum, platform, source labels и различие native image ID/config digest. Receipt связывает PASS с SHA, Prepare run ID/attempt и checksum архива. После CI gates Prepare создаёт уникальный tag `maxibot-prepare-<sha>-<run>-<attempt>` и публикует `bundle.zip` с image archive, manifest и receipt. Затем Release metadata read-back обязан подтвердить published state, `immutable=true` и GitHub asset digest. Новые большие Prepare bundles не хранятся в Actions Artifacts. `CI_PASS` появляется в Linear только после завершения публикации.
2. Dev сообщает полный SHA и Alembic target. Владелец запускает `preflight` в [MAXIBOT — Deploy](https://github.com/akhripunov/maxibot/actions/workflows/maxibot-deploy.yml). Runner требует последний успешный Prepare именно для текущего `main`, exact run/attempt и опубликованный immutable Release/tag/asset. Он сверяет release digest, bundle receipt, manifest/config digest, platform и source labels. Mac Docker daemon не загружает и не пересобирает image. Затем runner вызывает canonical `scripts/release_prod.sh SHA --dry-run` с production SSH identity и передаёт проверенный image archive. `READY` / `SAFE_TO_RELEASE=YES` не меняет production.
3. Владелец повторно запускает exact SHA/migration в режиме `production` с явным подтверждением. Runner заново валидирует Release metadata и те же image bytes, повторяет dry-run и вызывает `scripts/release_prod.sh SHA --apply`. Canonical script импортирует готовый archive; Deploy не пересобирает image на Mac/VPS.
4. Результат и allowlisted preflight/apply evidence публикуются отдельным immutable Release `maxibot-evidence-<sha>-<run>-<attempt>`; Prepare Release остаётся неизменным. Linear читает immutable evidence новых workflow runs и сохраняет Actions Artifact fallback для истории. Если apply прошёл, а evidence publication упала, не запускайте `production` повторно. `publication-recovery` сверяет точный исходный run/attempt и выполняет только read-only preflight; он может опубликовать recovery evidence лишь при доказанном совпадении production image, DB head, health, rollback metadata и выключенных ADS flags.

`FAILED_PRE_SWITCH` означает, что мутация production не началась. `RECOVERY_REQUIRED` означает, что после разрешения миграции apply не завершился доказанным `RELEASE_STATUS=PASS`: сначала выполнить read-only сверку и не повторять apply вслепую. При пропавшем Mac runner job останется в очереди, пока владелец не включит runner/Mac. Один и тот же SHA должен быть текущим `main` и иметь последний успешный Prepare; новый commit автоматически делает старую форму непригодной.

## Где смотреть

- GitHub run и первый упавший шаг: ссылки **MAXIBOT — Prepare** / **MAXIBOT — Deploy** выше.
- Текущий SHA и статус кандидата/preflight/production: [Архитектор — Handoff](https://linear.app/bestuzhev/document/arhitektor-handoff-bb75d67e4a95).
- История отдельных попыток: [Журнал релизов MAXIBOT](https://linear.app/bestuzhev/document/zhurnal-relizov-maxibot-3e615fe7e879).

Publisher обновляет только именованные MAXIBOT release-блоки в Handoff и добавляет идемпотентные события в журнал; архитектурный контекст владельца сохраняется. Публикуются SHA, run, статус и безопасные migration/readback факты. Секреты, production `.env`, SSH key и пользовательские сообщения в Linear не передаются. Repository secret `LINEAR_API_KEY` доступен только шагам публикации.

Повторные и неуспешные публикации сохраняются по `run_id`/`run_attempt`. Bookkeeping не коммитит и не отправляет `main`; публикационная сверка read-only и не вызывает runtime apply. Исторические Actions result artifacts остаются доступны для reconciliation; новые обязательные bundles/evidence хранятся в immutable Releases.

## Ограничения runner

Для MAXIBOT требуется отдельная repository-scoped регистрация runner с label `maxibot-production`. На этом Mac она может работать под тем же пользователем, что и GPoint. Использовать отдельную папку runner и work directory без пробелов, например `~/.local/share/maxibot-release-work`. Рабочие каталоги и runner не переиспользовать между регистрациями. MAXIBOT runner имеет доступ к `~/.ssh/maxibot_selectel_ed25519`; workflow назначает его только после проверки exact main SHA и успешного Prepare. Никогда не запускать на нём workflow из pull request/fork.

Регистрировать runner следует из Settings → Actions → Runners в репозитории MAXIBOT; temporary registration token не сохранять. На GitHub проверить `maxibot-owner-mac` / `maxibot-production` = `Idle`. Если пользовательский сервис не работает, production job остаётся `Queued` — это не успешная выкладка.

Проверки и runner используют Linux/macOS user environment с production MAX polling выключенным для тестов; единственный production poller остаётся на VPS `worker`. Production SSH key и `.env` остаются на Mac/VPS. Ветки PR, пользовательский Telegram smoke и реклама автоматикой не меняются.

## Release performance and finalization

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
