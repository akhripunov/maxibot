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

## Правило интерпретации Architect Sol → Architect Codex

Architect Sol определяет WHAT: product intent, business/system semantics,
invariants, acceptance criteria, forbidden changes и required evidence.
Architect Codex обязан до реализации сверить каждую задачу Sol с реальным
репозиторием, runtime, миграциями, конфигурацией и существующими абстракциями.

Architect Codex может менять HOW: точные файлы, модули, services/classes,
декомпозицию, порядок реализации, миграции, specialist assignment и deployment
mechanics. Но он не может молча менять WHAT: product behavior, business или
attribution semantics, conversion definitions, security invariants,
OWNER-only rules и frozen production-path behavior.

Упомянутые Sol имена файлов, модулей или классов не являются обязательными
targets, если Sol явно не отметил их как exact required targets. Нельзя создавать
новую абстракцию только ради соответствия формулировке, если существующая
абстракция репозитория уже реализует требуемую семантику.

Если реальный репозиторий требует изменения WHAT или конфликтует с product/system
semantics задачи, необходимо вернуть `ARCHITECTURE_BLOCKER` с evidence репозитория,
точным конфликтом и безопасными альтернативами. Нельзя угадывать или молча
переинтерпретировать требования. `UNKNOWN`, `UNVERIFIED` и `NOT_RUN` не равны
`PASS`.

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

## Политика автоматического одобрения CHANNEL

`CHANNEL_AUTOAPPROVE = DEFERRED`.
`NO_EXTRA_CLIENT_MAX_AUTH_FOR_AUTOAPPROVE = YES`.

Текущий доказанный программный механизм одобрения channel join request требует
аутентифицированной пользовательской MAX Web-сессии, связанной с человеческим
аккаунтом. Обычный MAX Bot API token не признан достаточным, а onboarding MaxiBot
не предоставляет такую сессию.

MaxiBot не должен требовать от клиента отдельную авторизацию личного MAX-аккаунта
для включения auto-approve. Запрещены login/SMS/QR, передача или загрузка
user-session/cookie, userbot authorization и хранение человеческой MAX-сессии для
этой цели. Внутренний MAX WebSocket остаётся исследовательским evidence и не
может быть превращён в customer production auto-approve.

`CHANNEL_AUTOAPPROVE_REQUIRING_EXTRA_CLIENT_AUTH = FORBIDDEN`.
`AUTOAPPROVE_IMPLEMENTATION = BLOCKED_BY_PRODUCT_POLICY`.
Ручное одобрение владельцем остаётся каноническим production-поведением.
Будущая реализация может быть пересмотрена только после доказательства
официального no-extra-auth bot/admin или partner API для произвольных клиентских
каналов. При отсутствии такого доказательства ARCHITECT CODEX обязан fail-close:
`PRODUCT_POLICY_BLOCKER = CHANNEL_AUTOAPPROVE_REQUIRES_UNAPPROVED_EXTRA_CLIENT_AUTH`.

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

## Action classes and approval friction policy

The following action classes govern execution scope:

- `CLASS_0_READ_ONLY`: repository/runtime inspection, reports, logs, and
  read-only production queries. No OWNER confirmation is required.
- `CLASS_1_DEVELOPMENT_ISOLATED_QA`: approved-scope source edits, tests, Git
  commits/exact-SHA creation, frozen-path guard execution, and disposable
  local/QA infrastructure or databases. ARCHITECT CODEX may perform these
  automatically after the task scope is accepted. They MUST NOT touch
  production data.
- `CLASS_2_NORMAL_PRODUCTION_RELEASE`: normal low-risk releases under the
  existing FAST_PATH and risk-based QA policy.
- `CLASS_3_HIGH_RISK_PRODUCTION_CHANGE`: attribution, paid conversion,
  payments, security, destructive/data-transforming migrations, or frozen
  BOT/CHANNEL behavior. It requires one OWNER approval at the final immutable
  SHA release boundary, after implementation, QA, migration validation, and
  guard evidence are complete.
- `CLASS_4_PAID_ADVERTISING_ACTION`: campaign, budget, bid, strategy,
  targeting, creative, or live-goal changes. These always require separate
  direct OWNER instruction for the exact advertising action.

Within an accepted task scope, do not request repeated OWNER confirmations for
CLASS 1 commit creation, exact-SHA QA, frozen guard execution, disposable QA
PostgreSQL/Docker, migrations, or processor tests. Frozen-path guard execution
itself is preauthorized; changing frozen behavior still requires the applicable
final OWNER approval. If a SHA changes after CLASS 3 approval, obtain approval
for the new SHA. Before reporting `BLOCKED`, classify the blocker as
`PRODUCT_DECISION`, `PRODUCTION_PERMISSION`, `PAID_AD_PERMISSION`,
`ENVIRONMENT_LIMITATION`, `REAL_TEST_FAILURE`, or `EXTERNAL_DEPENDENCY`.

- `NEXT_ARCHITECTURE_TASK = MAXIBOT_INTO_GENERIC_BOT_DOMAIN`: record only;
  do not implement as part of unrelated tasks. The target model keeps CHANNELS
  and BOTS separate, makes MaxiBot one generic BOT entity, and converges all
  BOT promotion UI/template and attribution/conversion semantics without
  changing the current MaxiBot landing/handoff/start/conversion flow. The
  future BOT promotion UI template must be a single source of truth for MaxiBot
  and customer bots, with the canonical buttons «Яндекс.Директ», «Аналитика»,
  and «Назад»; button changes apply to all BOT entities automatically.

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
- `SYSADMIN_MANDATORY_RELEASE_GATE = NO`.
- `SYSADMIN_DEFAULT_PARTICIPATION = NO`: SYSADMIN is optional and read-only by
  default. It participates only for an actual infrastructure/runtime anomaly,
  host/container/network/storage/platform topology change, an explicit OWNER
  request for an independent infrastructure audit, or when ARCHITECT CODEX
  determines infrastructure diagnosis is materially required. A production
  deploy alone does not invoke SYSADMIN.
- Normal deployment is allowed only after `RELEASE_READY = YES`, with exact-SHA
  QA GO where applicable, a passing frozen-path guard where applicable,
  canonical amd64/platform parity, rollback readiness, and API/worker health,
  polling, and parity checks. If an infrastructure anomaly appears, STOP and
  route read-only diagnosis to SYSADMIN.
- `SYSADMIN_DEFAULT_MODE = READ_ONLY`.
- `SYSADMIN_SAFE_DOCKER_MAINTENANCE = ALLOWED` is the narrow exception for the
  repository's fail-closed Docker maintenance scripts on the local Mac and
  production VPS `135.106.166.46`. It permits only explicitly enumerated
  cleanup of dangling images, unused builder cache, unreferenced old images,
  and eligible stopped non-production containers. It never permits volume,
  database, upload, backup, secret, source, active-container, active-image,
  protected-rollback, deployment, application-config, or advertising changes.
  Unknown ownership or unresolved current/rollback image state is skipped and
  reported as `MAINTENANCE_BLOCKER`; `docker system prune -a --volumes` is
  forbidden. Scheduled runs must use the repository scripts in dry-run mode
  for review and execute mode only after their guards pass.

## Эксплуатационное правило: MAX updates читаются в одном месте

- Production MAX updates читает только выделенный VPS `worker`.
- Единственный production host для polling: `135.106.166.46`.
- `LEGACY_VPS_RETIRED`: старый VPS выведен из эксплуатации и не участвует
  в проверках, polling или deployment. Проверять только production host.
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
  release gate, `SYSADMIN` — optional read-only infrastructure validator.
  Specialist roles не принимают финальные архитектурные решения.
- `Ads Codex Worker` — отдельный bounded runtime-контур рекламной аналитики,
  не общий development orchestrator.

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
указанные проблемы и повторно передаёт тот же workflow в QA. При `QA = GO` и отсутствии фактического infra blocker:

```text
QA → DEV canonical deploy/checks → DEV final report → ARCHITECT SOL / OWNER
```

SYSADMIN добавляется только при условиях, перечисленных в
`SYSADMIN_MANDATORY_RELEASE_GATE = NO`; его отсутствие само по себе не
блокирует релиз.

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

## Durable Release Flow V2 contract

- Before creating any release candidate, obtain and persist the current
  production baseline: `CURRENT_PRODUCTION_SHA`,
  `CURRENT_PRODUCTION_DB_HEAD`, current API image/runtime SHA, current worker
  image/runtime SHA, API/worker polling state, and current production
  Compose/runtime state. A candidate must never be built from an assumed or
  stale baseline.
- A candidate with schema changes must extend `CURRENT_PRODUCTION_DB_HEAD`.
  Prove the migration rehearsal before release approval:
  `CURRENT_PRODUCTION_DB_HEAD → candidate target migration`, rollback to the
  current production head, re-upgrade, and one canonical Alembic head. Never
  downgrade, rewrite, stamp, or force production migration history.
- `CANDIDATE_READY = YES` means the immutable product candidate passed product
  tests, relevant E2E, schema, and frozen-path gates. `RELEASE_READY = YES`
  additionally requires a freshly read production baseline, migration
  compatibility rehearsal, Docker build, effective Compose rendering, exact
  API/worker image propagation, validated backup and rollback path, production
  access preflight, worker-sync feasibility, and no known release blocker.
  OWNER must not be asked to deploy before `RELEASE_READY = YES`.
- Before any production migration or runtime switch, render the effective
  production Compose configuration and prove that the expected candidate SHA
  is the image SHA for both API and worker. Any mismatch is a fail-closed stop
  before migration or apply.
- One OWNER deploy approval is bound to one exact immutable product candidate.
  After `OWNER: «Выпускай»`, infrastructure, session, SSH, Docker, network, or
  worker-sync blockers use `RESUME SAME RELEASE` while the product candidate
  SHA is unchanged. Reuse completed valid stages idempotently and preserve the
  exact candidate, migration target, image identity, backup, rollback metadata,
  and validation state. Do not request a second approval for the same SHA.
- If product code, migration, immutable candidate SHA, or release contents
  materially change, this is a new candidate: repeat validation, obtain
  `RELEASE_READY = YES`, and obtain new OWNER approval. Approval never silently
  transfers to a different SHA.
- Any `UNKNOWN`, `NOT_RUN`, or `UNVERIFIED` mandatory invariant is not PASS.
  Release completion requires `PRODUCTION_SHA`, `API_READY_SHA`, and
  `WORKER_RUNTIME_SHA` to equal the approved candidate SHA, plus
  `API_PARITY = PASS`, `WORKER_PARITY = PASS`,
  `CODEX_ADS_WORKER_SYNC = PASS`, `MAC_PARITY = PASS`,
  `PRODUCTION_SMOKE = PASS`, and `ROLLBACK_READY = YES`.
  Only then may `RELEASE_COMPLETE = YES` be reported.
- Release tooling does not grant permission for Yandex Direct writes:
  `DIRECT_CHANGED = NO` by default. Campaign, budget, bid, strategy,
  targeting, creative, and placement changes require separate explicit OWNER
  authorization.
- Frozen-path guard remains mandatory. Approval for one shared or frozen
  dependency is scoped only to that exact dependency and never authorizes
  unrelated BOT, CHANNEL, or other frozen-path changes.
- The canonical OWNER UX is:
  `ARCHITECT CODEX: RELEASE_READY = YES` → `OWNER: «Выпускай»` →
  automatic release/resume until `RELEASE_COMPLETE = YES` or an exact external
  blocker that cannot be resolved automatically. OWNER should not manually
  shuttle SHAs, image tags, migration, SSH, rollback, or worker-sync commands
  unless automated execution is genuinely impossible because of an external
  environment restriction.

## Canonical production release tooling

- `PRODUCTION_RELEASE_ENTRYPOINT = scripts/release_prod.sh` is the only
  canonical production release entrypoint.
- `PRODUCTION_RELEASE_BYPASS = FORBIDDEN` unless OWNER explicitly authorizes
  release-tooling work.
- `MIGRATION_BEFORE_RUNTIME_SWITCH_READY = FORBIDDEN`.
- `AD_HOC_ENV_WIRING_DURING_PRODUCT_RELEASE = FORBIDDEN`.
- `AD_HOC_RELEASE_SYMLINKS = FORBIDDEN`.
- The sole production secret source is `/opt/maxibot/.env`; release archives
  must not contain secret copies.
- High-risk releases require `scripts/release_prod.sh <FULL_SHA> --dry-run`
  before `--apply`.
- The release gate must prove exact SHA provenance, immutable release layout,
  canonical `linux/amd64` image, Compose resolution, rollback metadata, and
  runtime-switch readiness before it permits a production migration.

## AI Promotion continuous object control

- `AI Promotion` is one continuous object-level control screen for each
  CHANNEL or BOT, not separate primary screens for checking, recommendations,
  results, or scaling. Its stage is derived from the canonical Closed-Loop
  state.
- The screen shows one evidence-backed primary root cause and one next best
  action. Provider metrics remain proxy signals; `max_subscriber` is CHANNEL
  business truth and `bot_started` is BOT business truth. `UNKNOWN` is never
  rendered as zero, and business CPA is not computable without confirmed
  outcomes.
- Continuous analysis considers all ACTIVE linked campaigns for the selected
  object while preserving campaign isolation. Suspended or disabled campaigns
  remain historical but cannot drive a new current recommendation.
- Automatic analysis reuses the existing collection, snapshot, outcome,
  worker, and memory pipeline. Triggers are bounded by cadence and must reuse
  pending runs; they must not start an AI run on every collector tick.
- Verified learning contains only conclusive Closed-Loop SUCCESS/FAIL evidence;
  recommendations and INCONCLUSIVE outcomes never become verified memory.
  Direct remains read-only and the main screen stays advisory.
