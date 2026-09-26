# MAXIBOT: обязательное правило пользовательского пути

Пользователь на лендинге ничего не нажимает.

После открытия лендинга система автоматически выполняет редирект в MAX по
нужному tracking/deep link. Кнопка «Перейти» на лендинге не является частью
пользовательского сценария и не должна требоваться для перехода.

Правильный путь:

```text
Яндекс Директ → лендинг → автоматический редирект → MAXIBOT/канал → вступление
```

Не добавлять промежуточные кнопки, подтверждения или действия пользователя на
лендинге, если это отдельно не запрошено архитектором.

# Роли и порядок работы

## Архитектор

Архитектор формулирует целевую схему, принимает архитектурные решения,
утверждает ограничения и выдаёт dev единое ТЗ. Архитектор не считает задачу
готовой по наличию кода — требуется проверяемый handoff.

## Dev

Dev изучает текущий код, реализует утверждённое ТЗ, не меняет согласованную
архитектуру самовольно, запускает тесты и проверяет результат на staging/VPS.
После завершения dev обязан передать архитектору handoff:

- что сделано;
- какие файлы и миграции изменены;
- команды запуска и переменные окружения;
- результаты тестов;
- результаты реального E2E/VPS-теста;
- статусы PASS/BLOCKED;
- точный оставшийся блокер, если он есть.

# Главная рабочая схема

```text
Яндекс Директ
  → landing
  → автоматический redirect без клика на landing
  → MAXIBOT/tracking deep link
  → нужный подключённый MAX-канал
  → вступление или заявка
  → ручное одобрение владельцем
  → подтверждённое членство
  → userDidSubscribe в Яндекс Метрику
```

## Получение ссылки подключённого канала

Для подключённого MAX-канала не просить владельца копировать invite URL.

```text
bot_added
  → получить chat_id
  → GET /chats/{chat_id} через официальный MAX Bot API
  → взять response.link
  → сохранить link как target/invite_url этого chat_id
```

Нельзя смешивать `chat_id`, владельцев или ссылки разных каналов. Если MAX не
вернул непустой валидный `link`, редирект блокируется и в handoff указывается
точный статус BLOCKED.

## Связка MAX-пользователя с yclid

Для точной атрибуции не считать прямой переход по общей ссылке канала
достаточным. Использовать tracking deep link бота:

```text
landing получает yclid/UTM
  → создаёт opaque click_id
  → автоматический redirect на MAXIBOT?start=payload
  → bot_started даёт MAX user_id и payload/click_id
  → MAXIBOT отправляет этому пользователю link нужного канала
  → user_added фиксирует user_id + channel chat_id
  → user_id → click_id → yclid
  → userDidSubscribe после подтверждённого членства
```

Пользователь не вводит ссылку и не нажимает кнопку на landing. Для закрытого
канала ручное одобрение владельцем остаётся обязательным, пока отдельно не
доказан production auto-approve.

Пользователь на landing ничего не нажимает. Любая кнопка на landing,
требующая действия для перехода, считается нарушением утверждённого UX.

# Структура проекта

- `app/` — основное приложение, API, bot runtime, интеграции и бизнес-логика.
- `app/bot/` — меню, webhook/event handling и пользовательские сценарии бота.
- `app/integrations/` — клиенты MAX, Яндекс и внешние интеграции.
- `miniapp/` — интерфейс мини-приложения и traffic/landing UI.
- `demo/` — демонстрационные страницы и локальный UI-прототип.
- `tests/` — unit, integration и contract-тесты.
- `alembic/` — миграции базы данных.
- `deploy/` и `docker-compose*.yml` — deployment и VPS-конфигурация.
- `research/` — отчёты исследований, доказательства и handoff-материалы.
- `App Design/` — согласованные визуальные макеты.

# Критические ограничения

- Не использовать user-level WebSocket и opcode 55/77 без отдельного
  утверждённого безопасного ТЗ.
- Не отправлять `userDidSubscribe`, если нет доказанной связи подписчика с
  рекламным визитом.
- Не подставлять неизвестный или чужой канал, если target/invite URL не
  получен.
- Не выводить токены, cookies, raw frames и персональные данные в логи.
- Не объявлять E2E/production готовыми только по unit-тестам.

## Production deployment invariants

- Release owner: `ARCHITECT CODEX`. Advertising campaign control is
  `OWNER_ONLY`; deploy tooling must not start, pause, resume, or mutate Direct,
  VK, budget, bids, strategy, targeting, or creatives.
- The canonical production/build platform is `linux/amd64`.
- Before replacing a production image, enforce `VPS_ARCH == IMAGE_PLATFORM`.
  `arm64` on the VPS, an unknown platform, failed image inspection, or an
  unknown source SHA is fail-closed and blocks replacement.
- Native ARM Mac production builds are forbidden. An explicit
  `--platform linux/amd64` is allowed only when the resulting image platform
  is inspected and matches the canonical VPS.
- Do not improvise `docker build/save/load` deployment flows. Use the
  repository's canonical Compose/runbook and rollback snapshot.
- The deployment sequence is: deterministic gates on exact SHA → preflight
  and rollback snapshot → canonical amd64 build and platform/source guard → image
  replacement → SYSADMIN postdeploy health/polling/error/parity checks →
  channel regression → ARCHITECT CODEX final verdict.

- For prospective candidates whose commit tree contains
  `scripts/release_policy.py`, independent QA verdicts are advisory only.
  Readiness is determined by deterministic tests, exact-SHA/image/platform/
  migration checks, parity, dry-run, rehearsal, and post-deploy readback.
  Historical candidates without that file remain governed by their prior
  release policy.

## Эксплуатационное правило: MAX updates читаются в одном месте

- Production MAX updates читает только выделенный VPS `worker`.
- Единственный production host для polling: `135.106.166.46`.
- VPS `api` и локальный `docker-compose` обязаны иметь
  `MAX_RUN_POLLING=false`; единственный VPS worker явно имеет `true`.
- Нельзя запускать локальный MAXIBOT с production MAX token и polling.
- Перед диагностикой дублей меню сначала проверять наличие второго poller-а;
  не удалять пользовательские сообщения и не менять модели каналов как обход.
- Подробная процедура закреплена в
  `research/MAXIBOT_SINGLE_POLLER_RUNBOOK.md`.

## Зафиксированный production-контракт mobile MAX handoff

Рабочий iPhone-путь подтверждён двумя реальными записями владельца 2026-08-21:

```text
mobile landing
  → max://max.ru/<bot>?startapp=<same opaque token>
  → MAX Mini App
  → POST /api/traffic/resolve
  → iPhone: обычный HTTPS channel invite через programmatic anchor target=_blank
  → нужный MAX-канал
```

Этот контракт нельзя менять как косметический рефакторинг:

- iPhone/iPad Mini App не переводить на безусловный `WebApp.openMaxLink()` —
  реальный iOS-клиент молча оставался на экране «Открываем канал…»;
- Android сохраняет `window.location.replace(target_url)`;
- MAX Web/desktop может использовать `WebApp.openMaxLink(target_url)` только
  при исключённом Apple mobile user agent;
- один и тот же `startapp` token обязан сохраняться от landing до Mini App;
- нельзя добавлять новый visit/token, timer-navigation, iframe или обязательный
  пользовательский клик;
- любые изменения `app/main.py`, `app/traffic/deep_link.py` или
  `miniapp/traffic.html`, затрагивающие handoff, требуют прохождения
  `tests/regression/test_mobile_max_handoff_contract.py` и двух свежих ручных
  smoke на настоящем iPhone: (1) ссылка из MAX, (2) ссылка из Safari;
- пока оба real-device smoke не завершились переходом в целевой канал, статус
  релиза `BLOCKED`, а предыдущий production handoff должен быть восстановлен.

# Canonical Codex orchestration

- `ARCHITECT CODEX` — единственный общий технический orchestrator MAXIBOT.
- `ARCHITECT_CODEX_REQUIRED = YES` для cross-role и release-задач.
- `ARCHITECT_CODEX_ROLE = LOCAL TECHNICAL ARCHITECT + ORCHESTRATOR + SINGLE WINDOW`.
- `ARCHITECT_SOL_ROLE = PRODUCT / SYSTEM ARCHITECT`.
- `MINIMUM_REQUIRED_ROLES / FAST_PATH`: ARCHITECT CODEX выбирает кратчайший
  безопасный маршрут; один primary implementer является default, а новые роли
  подключаются только при фактической scope-потребности, blocker или
  обязательном независимом gate. Роли не участвуют церемониально.
- Узкие direct handoff'ы между специалистами разрешены. ARCHITECT CODEX остаётся
  single window и владельцем состояния, но не обязан быть relay для каждого
  сообщения. Независимый QA обязателен там, где это требует release policy.
- SYSADMIN не является обязательным участником каждого deploy: для обычных
  релизов предпочтительны deterministic automated infra guards; SYSADMIN нужен
  при реальном infra risk, incident, secret/runtime или deployment blocker.
- Рабочие роли проекта: `DEV`, `WEBDEV`, `ДИРЕКТОЛОГ`, `QA / ТЕСТИРОВЩИК`,
  `SYSADMIN`.
  Старые Codex tasks/chats не считаются дополнительными ролями.
- `ARCHITECT CODEX` выбирает minimum sufficient team, принимает финальное
  техническое решение и после handoff проводит integration review.
- `DEV` отвечает за backend/bot/DB/API/workers, `WEBDEV` — за сайт и frontend,
  `ДИРЕКТОЛОГ` — consultative по Direct/Metrika/attribution, `QA` — независимый
  release gate, `SYSADMIN` — инфраструктурный pre/post-deploy gate.
  Specialist roles не принимают финальные архитектурные решения.
- `Ads Codex Worker` — отдельный bounded runtime-контур рекламной аналитики,
  не общий development orchestrator.

## Default execution profile and token economy

- `ARCHITECT SOL` ведёт один основной контекст: принимает owner intent,
  формирует единое ТЗ, выбирает кратчайший безопасный маршрут, проверяет
  candidate и владеет final technical verdict.
- `DEV LUNA` (`gpt-5.6-luna`) — default primary implementer для bounded
  backend, bot, API, UI и test-задач. Он получает одно полное ТЗ с scope,
  acceptance criteria, разрешёнными путями и проверками, а возвращает commit
  и проверяемый handoff.
- По умолчанию одна задача получает одного implementer. Нельзя создавать
  дополнительные роли или новые чаты только для повторной передачи того же ТЗ:
  это не повышает качество и расходует контекст.
- Более сильная модель или отдельный specialist подключается только при
  архитектурном решении, реальном blocker, security/production risk,
  обязательном независимом техническом gate или доказанной недостаточности primary implementer.
- Цепочка по умолчанию: `OWNER → ARCHITECT SOL → DEV LUNA → ARCHITECT SOL
  review → exact GitHub SHA → exact VPS SHA`. QA/SYSADMIN добавляются только
  по risk-based policy и release gates.

## Актуальный DEV ↔ QA routing override

Для release closeout workflow сохраняется узкий direct handoff: DEV имеет
право передавать кандидат в канонический QA thread:
`019ffb88-32db-76f1-bdc2-1373b29fca13`.
Это не удаляет ARCHITECT CODEX из orchestration: он остаётся single window,
владеет состоянием задачи, exact candidate SHA, integration review и release
readiness.

Текущая цепочка:

```text
ARCHITECT SOL → DEV → QA → DEV
```

При `QA = NO_GO` QA возвращает findings напрямую DEV; DEV исправляет только
указанные проблемы и повторно передаёт тот же workflow в QA. При `QA = GO`:

```text
QA → DEV → SYSADMIN predeploy → DEV deploy → SYSADMIN postdeploy
  → DEV final report → ARCHITECT SOL / OWNER
```

DEV не должен требовать `ARCHITECT CODEX` для routine QA handoff, получения QA
verdict или release orchestration. Новый QA chat автоматически не создаётся;
канонический thread меняется только по явному указанию OWNER.

## Cross-role implementation governance

`ARCHITECT CODEX` остаётся техническим владельцем и single window. Для задач с
несколькими implementation roles `ARCHITECT SOL` назначает `LEAD_IMPLEMENTER`
(`DEV`, `WEBDEV` или другого подходящего специалиста) и явно фиксирует scope.
Lead implementer координирует технические handoff'ы, собирает unified
candidate, передаёт его QA и отвечает за technical closeout.

DEV и WEBDEV могут делать narrow direct handoff друг другу в рамках уже
утверждённой архитектуры. Специалист не может самостоятельно менять product
behavior, core architecture, attribution semantics или security policy. При
материальном расхождении возвращается `ARCHITECTURE_BLOCKER = YES` напрямую
`ARCHITECT SOL`.
- Для подробного ROLE REGISTRY и routing использовать
  [`docs/ROLE_REGISTRY.md`](docs/ROLE_REGISTRY.md) и
  [`docs/ORCHESTRATION_PROTOCOL.md`](docs/ORCHESTRATION_PROTOCOL.md).
- Перед существенной задачей обязателен repository preflight; `UNKNOWN` и
  `UNVERIFIED` нельзя выдавать за `PASS`.

## Frozen paid-traffic production paths

`BOT` и `CHANNEL` являются отдельными `PAID_TRAFFIC_CRITICAL`
`FROZEN_PRODUCTION_PATHS`. Freeze is behavioral, not only file-based:
refactoring, cleanup, or unification affecting either paid path or a shared
dependency requires explicit OWNER approval. The canonical stable baseline,
protected areas, and regression contracts are in
[`docs/frozen_paths.yaml`](docs/frozen_paths.yaml); the executable gate is
`scripts/frozen_path_guard.sh` and its result is `FROZEN_PATH_GUARD`.

`OWNER_APPROVAL_REQUIRED_FOR_FROZEN_CHANGE = YES`.
`BOT_AND_CHANNEL_SEPARATE = YES`.
`NO_REFACTOR_FOR_CLEANUP = YES`.
`FAST_PATH_WHEN_UNCHANGED = YES`.
An unchanged frozen path does not require duplicate role work. A changed path
requires `FROZEN_PATH_REGRESSION` evidence. These rules are authoritative and
lower-level documents may not weaken them.

`ARCHITECT CODEX` remains the single technical/orchestration window.
`MINIMUM_REQUIRED_ROLES / FAST_PATH` applies; SYSADMIN is an optional
read-only infrastructure validator unless a real infrastructure, secret,
runtime, or deployment blocker requires escalation. QA uses the risk-based
policy; QA artifact ingest is non-blocking for otherwise-green product QA,
while product and frozen-path failures remain fail-closed. Advertising
campaign control is `OWNER_ONLY`.

## OWNER intent и защита от формальных блокировок

- Явное поручение OWNER реализовать изменение считается разрешением на
  минимально необходимые изменения файлов, которых это поручение неизбежно
  касается, включая frozen path. От OWNER не требуется повторять специальную
  формулу `OWNER_APPROVAL_FOR_FROZEN_CHANGE = YES`.
- Такое разрешение не включает deploy, изменение рекламных кампаний или
  расширение scope. Frozen regression и остальные release gates сохраняются.
- После изменения `HEAD`, ветки, worktree или восстановления canonical
  baseline все прежние repository/environment blockers считаются устаревшими
  до повторной проверки. Нельзя повторять старый blocker без свежего
  воспроизведения на текущем состоянии.
- Новый прямой запрос OWNER заменяет старый незавершённый goal, если запросы
  относятся к разным задачам. Старый goal не должен запускать повторные
  остановки или автоматические продолжения.
- Writable OWNER worktree разрешено использовать для утверждённой реализации.
  Невозможность создать дополнительный isolated worktree не является
  blocker, если текущий worktree writable и можно сохранить чужие изменения.
- При обнаружении нескольких Git-состояний сначала фиксируется один current
  source of truth. Старые ветки можно использовать только как read-only
  reference; найденный код переносится выборочно и проверяется на current
  source of truth.
- Архитектор завершает всю уже разрешённую обратимую работу до запроса нового
  решения OWNER. Routine preflight, тесты, исправление ошибок реализации и
  подготовка candidate не требуют отдельного подтверждения.

## AI Promotion continuous control screen

- `AI Продвижение` — единый object-level экран для CHANNEL или BOT поверх
  canonical ADS Data Layer и Closed-Loop state.
- Все ACTIVE `ads_promotion_campaign_links` объекта анализируются
  автоматически; OWNER не запускает проверку отдельно для каждой кампании.
- Suspended/disabled campaigns сохраняются в истории, но не участвуют в
  current recommendation. Campaign isolation обязательна.
- Главный экран показывает одну доказанную primary root cause и одно next best
  action. При недостаточных данных причина не придумывается.
- Stage `Анализ → Рекомендация → Проверка → Результат → Масштабирование`
  вычисляется из canonical Closed-Loop state, а не хранится как отдельный UI
  статус.
- Provider proxy не является подтверждённым business outcome. CHANNEL использует
  `max_subscriber`, BOT использует `bot_started`; `UNKNOWN != ZERO`.
- Continuous analysis использует существующий collector/poller/worker,
  configurable cadence и dedupe текущих AI runs. Direct остаётся read-only.
