# Автоматический выпуск MAXIBOT

Процесс оборачивает существующий `scripts/release_prod.sh`; он остаётся единственной production-командой. Workflow не управляет Direct/VK кампаниями, бюджетом, ставками, объявлениями или `ADS_*` master flags.

## Порядок для владельца

1. Dev отправляет готовый commit в `origin/main`. GitHub запускает [MAXIBOT — Prepare](https://github.com/akhripunov/maxibot/actions/workflows/maxibot-prepare.yml): полную pytest suite, frozen-path guard и сборку/проверку exact `linux/amd64` image. Результат `CI_PASS` означает, что код и image прошли CI; он сам по себе ещё не разрешает выкладку.
2. Dev сообщает полный SHA и точный Alembic target. В [MAXIBOT — Deploy](https://github.com/akhripunov/maxibot/actions/workflows/maxibot-deploy.yml) владелец выбирает `main`, вставляет эти значения и запускает `preflight`. Runner проверяет последний успешный Prepare именно для текущего `main`, собирает кандидата из этого SHA для локального platform gate, затем вызывает canonical `scripts/release_prod.sh SHA --dry-run` с production SSH identity. Результат `READY` / `SAFE_TO_RELEASE=YES` оставляет production без изменений.
3. Владелец повторно запускает форму с тем же SHA и migration, выбирает `production`, отмечает **I authorize this exact candidate and Alembic target on MAXIBOT production** и нажимает **Run workflow**. Runner заново проверяет provenance и dry-run, затем передаёт `OWNER_APPROVED=1` только canonical `scripts/release_prod.sh SHA --apply`.
4. `COMPLETE` означает, что canonical скрипт проверил production SHA, Alembic, API/worker image parity, `linux/amd64`, health и polling topology. После выкладки выполните требуемую QA/regression и живой продуктовый smoke. Workflow не включает рекламу и не создаёт ad writes.

`FAILED_PRE_SWITCH` означает, что мутация production не началась. `RECOVERY_REQUIRED` означает, что после разрешения миграции apply не завершился доказанным `RELEASE_STATUS=PASS`: сначала выполнить read-only сверку и не повторять apply вслепую. При пропавшем Mac runner job останется в очереди, пока владелец не включит runner/Mac. Один и тот же SHA должен быть текущим `main` и иметь последний успешный Prepare; новый commit автоматически делает старую форму непригодной.

## Где смотреть

- GitHub run и первый упавший шаг: ссылки **MAXIBOT — Prepare** / **MAXIBOT — Deploy** выше.
- Текущий SHA и статус кандидата/preflight/production: [Архитектор — Handoff](https://linear.app/bestuzhev/document/arhitektor-handoff-bb75d67e4a95).
- История отдельных попыток: [Журнал релизов MAXIBOT](https://linear.app/bestuzhev/document/zhurnal-relizov-maxibot-3e615fe7e879).

Publisher обновляет только именованные MAXIBOT release-блоки в Handoff и добавляет идемпотентные события в журнал; архитектурный контекст владельца сохраняется. Публикуются SHA, run, статус и безопасные migration/readback факты. Секреты, production `.env`, SSH key и пользовательские сообщения в Linear не передаются. Repository secret `LINEAR_API_KEY` доступен только шагам публикации.

## Ограничения runner

Для MAXIBOT требуется отдельная repository-scoped регистрация runner с label `maxibot-production`. На этом Mac она может работать под тем же пользователем, что и GPoint. Использовать отдельную папку runner и work directory без пробелов, например `~/.local/share/maxibot-release-work`. Рабочие каталоги и runner не переиспользовать между регистрациями. MAXIBOT runner имеет доступ к `~/.ssh/maxibot_selectel_ed25519`; workflow назначает его только после проверки exact main SHA и успешного Prepare. Никогда не запускать на нём workflow из pull request/fork.

Регистрировать runner следует из Settings → Actions → Runners в репозитории MAXIBOT; temporary registration token не сохранять. На GitHub проверить `maxibot-owner-mac` / `maxibot-production` = `Idle`. Если пользовательский сервис не работает, production job остаётся `Queued` — это не успешная выкладка.

Проверки и runner используют Linux/macOS user environment с production MAX polling выключенным для тестов; единственный production poller остаётся на VPS `worker`. Production SSH key и `.env` остаются на Mac/VPS. Ветки PR, пользовательский Telegram smoke и реклама автоматикой не меняются.
