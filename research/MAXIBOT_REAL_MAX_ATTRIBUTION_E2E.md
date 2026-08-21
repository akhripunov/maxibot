# MAXIBOT REAL MAX attribution E2E

## Native MAX attempt without desktop auto-fallback: 2026-08-17

После принятия competitor research production handoff изменён:

```text
landing → один hidden max://...?...startapp=<same-token> attempt
        → landing остаётся видимым
        → CTA «Перейти в канал» с тем же max://...startapp
```

HTTPS `max.ru` auto-redirect и публичная карточка MAXIBOT из landing удалены.
Visit/startapp/attribution backend не менялись.

Production desktop smoke по `FZ3kpwshUOqIvA`:

```text
LANDING_REMAINS_VISIBLE = YES
AUTO_MAX_SCHEME_ATTEMPT = YES
BOT_PROFILE_AUTO_OPEN = NO
HTTPS_PROFILE_FALLBACK = NO
CTA_PRESENT = YES
STARTAPP_PRESERVED = YES
EXTRA_VISIT_CREATED = NO (один GET handoff, без нового token/visit внутри HTML)
API_HEALTH_READY = PASS
```

Mobile native behavior намеренно не объявляется PASS до отдельного smoke на
iOS/Android с установленным MAX.

## Device-aware promotion landing: 2026-08-17

По отдельному ТЗ desktop больше не переводится автоматически на публичную
карточку MAXIBOT. Backend attribution не менялся: visit/startapp создаются
прежним способом, меняется только HTML handoff.

```text
desktop → traffic landing остаётся открытым → CTA «Открыть MAX»
mobile  → тот же официальный https://max.ru/<bot>?startapp=<same-token>
          с автоматическим window.location.replace
```

Production smoke с VPS по `FZ3kpwshUOqIvA`:

```text
DESKTOP_LANDING_FLASH = NO
DESKTOP_AUTO_BOT_PROFILE = NO
DESKTOP_LANDING_USABLE = YES
DESKTOP_CTA = YES
MOBILE_FLOW_UNCHANGED = YES
MOBILE_AUTO_REDIRECT = YES
MOBILE_STARTAPP_PRESENT = YES
API_HEALTH_READY = PASS
```

Это проверка server-side device behavior по User-Agent. Отдельный native iOS /
Android smoke по-прежнему требует ручного устройства с установленным MAX;
этот отчёт не объявляет его PASS.

Дата: 2026-08-17
Тестовый маркер: `REAL_MAX_E2E_001`

## Последняя попытка создать отдельный MAX test contour

```text
TEST_CHANNEL_CREATED = BLOCKED
TEST_CHANNEL_PRIVATE = BLOCKED
TEST_CHANNEL_CHAT_ID = NOT_CREATED
TEST_CHANNEL_INVITE_OR_URL = NOT_CREATED
TEST_CHANNEL_SAFE_FOR_E2E = NO

TEST_BOT_CREATED = NOT_STARTED
TEST_MINI_APP_CREATED = NOT_STARTED
TEST_MINI_APP_BOUND_TO_TEST_BOT = NOT_STARTED
```

В доступном MAX Web developer-чате `Каналы в MAX для бизнеса` после
верификации владельца доступен сценарий создания/публикации публичных каналов.
Сценария создания отдельного закрытого канала через текущую browser session не
обнаружено. Раздел «МОИ» показывает существующие каналы, но не содержит
действия создания приватного канала. Рабочие «Москва 365» и «Твой Петербург» не
использовались.

Это единственный blocker текущего шага: без безопасного private test channel
нельзя продолжать созданием test bot/Mini App flow и нажатием «Вступить».

## Итоговый статус

```text
STAGING_STARTED = YES
PUBLIC_STAGING_HTTPS = YES (temporary Quick Tunnel; stopped after check)
TEST_CHANNEL_IDENTIFIED = NO
STAGING_MINI_APP_READY = NO

REAL_MAX_USER_ID_CAPTURED = BLOCKED
START_TOKEN_RESOLVED = BLOCKED
MAX_USER_BOUND_TO_VISITOR = BLOCKED

CHANNEL_OPENED = BLOCKED
EXTRA_USER_CLICK_REQUIRED = NOT_MEASURED
MINI_APP_VISIBLE_DURATION_MS = NOT_MEASURED

REAL_JOIN_REQUEST_OBSERVED = BLOCKED
JOIN_USER_EQUALS_BOUND_USER = BLOCKED
JOIN_TO_YCLID_RESOLUTION = BLOCKED

MEMBER_CONFIRMED = NOT_PERFORMED

REAL_E2E_ATTRIBUTION = BLOCKED
PRODUCTION_CHANGED = NO
YANDEX_CONVERSION_SEND = DISABLED
```

## Staging

Использован существующий изолированный compose `docker-compose.qa.yml` с
`.env.staging`:

- API: `127.0.0.1:18000`;
- PostgreSQL: отдельный volume `maxibot_staging_postgres_data`;
- Redis: отдельный volume `maxibot_staging_redis_data`;
- `MAX_CLIENT_MODE=stub`;
- `MAX_RUN_POLLING=false`;
- отдельная сеть `maxibot_staging_network`.

`GET /health/ready` вернул `status=ready`, `max_mode=stub`. Static Mini App
endpoints `/mini` и `/traffic-test/app` вернули HTTP 200.

Существующий production Dockerfile не изменялся. Для исправления staging build
добавлен отдельный `Dockerfile.staging`: production image packaging step падает
на flat-layout с несколькими top-level директориями, поэтому staging ставит
зависимости напрямую и не устанавливает проект как wheel.

Внешняя проверка через временный Cloudflare Quick Tunnel с VPS прошла:

```text
GET https://<temporary-trycloudflare-host>/health/ready → 200
{"status":"ready","service":"maxibot","max_mode":"stub"}
```

Tunnel, SSH reverse-forward и ephemeral tunnel container после проверки
остановлены. Chrome browser extension заблокировал открытие trycloudflare URL
(`ERR_BLOCKED_BY_CLIENT`), поэтому browser-specific health check не считается
отдельным PASS; внешний HTTPS curl был PASS.

## Тестовый MAX-канал

В доступной MAX Web-сессии видна кнопка `Твой Петербург` в меню подключённых
каналов MAXIBOT. Это подтверждает только наличие подключённого рабочего канала.
Не доказано, что он выделен как staging/test, закрыт именно для безопасного
E2E и не содержит реальных пользователей. «Москва 365» явно не использовался.

По правилу ТЗ безопасный тестовый канал не идентифицирован, поэтому тест
остановлен до любого перехода, нажатия «Вступить» или создания JOIN_REQUEST.

```text
TEST_CHANNEL_PRIVATE = UNKNOWN
TEST_CHANNEL_SAFE_FOR_E2E = NO
```

## Почему не выполнен Mini App E2E

В staging нет тестового MAX bot token/username, зарегистрированного на staging
Mini App. Production bot/Mini App использовать запрещено. При `MAX_CLIENT_MODE=stub`
нельзя получить настоящий MAX WebApp initData и настоящий MAX user.id.

Поэтому не выполнялись:

- production или неизвестный канал;
- подстановка MAX user_id вручную;
- synthetic JOIN_REQUEST вместо реального события;
- approve заявки;
- отправка Яндекс conversion.

## Проверки

```text
docker compose ... up -d --build = PASS
staging /health/ready = PASS
staging /mini = PASS
staging /traffic-test/app = PASS
staging pytest (identity/deep-link/UI contract) = 21 passed
external HTTPS health via VPS curl = PASS
browser open of temporary tunnel = BLOCKED by browser client policy
```

## Единственный блокер для REAL E2E

Нужен отдельный тестовый MAX-контур: зарегистрированный тестовый bot/Mini App,
его credentials в staging env и закрытый тестовый канал, подтверждённый как
неproduction и безопасный для создания JOIN_REQUEST. До этого
`REAL_E2E_ATTRIBUTION` остаётся `BLOCKED`.

## Актуальная попытка: production Mini App + TEST records

Дата: 2026-08-17, production code/config не изменялись.

Устаревшие блокеры «нет test channel» и «нужен новый bot/Mini App» сняты.
В production DB добавлены только новые TEST-записи для существующего MAX
канала `MAXIBOT E2E TEST` (`chat_id=-77886885970047`):

```text
channel_id = 0e96a0cd-f7e6-4f69-b141-20fa58e89071
traffic_profile_id = 5659a72c-cac8-40b5-afc2-fd3b7ccadf68
landing_page_id = 9c1d72e8-ff05-4a8e-b309-c987b2c25d82
public_id = e2e-test-20260817
marker = REAL_MAX_E2E_001
```

Invite URL сохранён только в TEST `TrafficProfile`; в отчёт не выводится.
`yandex_enabled=false`, conversion queue для тестового visit пуст.

### Фактические результаты

```text
TEST_CHANNEL_REGISTERED_IN_MAXIBOT = YES
TEST_TRAFFIC_PROFILE_READY = YES
TEST_LANDING_READY = YES

LANDING_VISITOR_CREATED = YES
VISIT_CREATED = YES
YCLID_BOUND_TO_VISIT = YES
START_TOKEN_CREATED = YES
TOKEN_TO_VISIT = PROVEN
START_TOKEN_HASH = 6e7d60b3d7a841e84019c6269b41db0e
VISIT_ID = b8f3e1ce-1556-4584-a6d9-0bc3467a790c
VISITOR_TOKEN_HASH = c28e8a98d0e8b68e9cfac1225875c174

START_PARAM_RECEIVED = YES
START_PARAM_EQUALS_SENT_TOKEN = YES
REAL_MAX_USER_ID_RECEIVED = YES
REAL_MAX_USER_ID_HASH = af8adb57889535acac46a052d0f49408
INIT_DATA_VALIDATED = YES
MAX_USER_BOUND_TO_VISIT = YES

TEST_CHANNEL_TARGET_RESOLVED = YES
CHANNEL_OPENED = YES (backend channel_open_clicked event)
EXTRA_USER_CLICK_REQUIRED = YES in MAX Web wrapper; NO click on landing
MINI_APP_VISIBLE_DURATION_MS = NOT_MEASURABLE

REAL_JOIN_REQUEST_OBSERVED = NO
JOIN_USER_EQUALS_MINIAPP_USER = NOT_TESTED
JOIN_TO_VISIT_RESOLUTION = BLOCKED
JOIN_TO_TEST_MARKER_RESOLUTION = BLOCKED
MEMBER_CONFIRMED = NOT_PERFORMED

REAL_MAX_IDENTITY_E2E = BLOCKED
PRODUCTION_CODE_CHANGED = NO
PRODUCTION_CONFIG_CHANGED = NO
PRODUCTION_TEST_DATA_CREATED = YES
EXISTING_PRODUCTION_DATA_MODIFIED = NO
YANDEX_CONVERSION_SEND = DISABLED
```

### Доказательства runtime

Для visit с `yclid=REAL_MAX_E2E_001` production DB зафиксировала:

- `BOT_REDIRECT_SENT`;
- `max_identity_linked`;
- `channel_open_clicked`.

Hash `max_user_id` в `TrafficVisit` и `MaxIdentityLink` совпадает:
`af8adb57889535acac46a052d0f49408`. Это доказывает прохождение существующего
production `/mini` через validated MAX initData, получение реального identity и
bind к конкретному visit. `channel_open_clicked` записан с тем же identity и
TEST channel. Интервал между bind и channel-open по backend timestamps — около
51 мс; фактическое время видимости Mini App отдельно не измерялось.

### Точный оставшийся blocker

Доступная MAX browser session является владельцем `MAXIBOT E2E TEST` и уже
показывает `1 подписчик`. Владелец не может создать JOIN_REQUEST в собственный
private channel. Нужен второй собственный тестовый MAX-аккаунт, авторизованный в
MAX Web или нативном MAX, чтобы нажать «Вступить»; synthetic DB event и ручная
подстановка user_id запрещены. После появления второго аккаунта продолжение
начинается с уже созданного TEST landing/visit, повторно создавать production
сущности не требуется.

## Проверка доступных вторых MAX-сессий

Дата: 2026-08-17.

Проверены доступные browser surfaces read-only:

```text
CHROME_PROFILES_AVAILABLE = 1 (profile Alex)
MAX_WEB_SESSIONS_AVAILABLE = 1
IN_APP_BROWSER_MAX_SESSIONS = 0
SECOND_MAX_USER_AVAILABLE = NO
SECOND_USER_NOT_CHANNEL_OWNER = NOT_AVAILABLE
```

Открытая MAX Web-сессия соответствует owner-контексту, в котором тестовый
канал показывает одного подписчика. Второй обычный MAX user, не являющийся
владельцем и не состоящий в `MAXIBOT E2E TEST`, в доступном окружении отсутствует.
Никакие новые аккаунты, bot/Mini App или staging-контуры не создавались.

```text
REAL_JOIN_REQUEST_OBSERVED = BLOCKED
JOIN_USER_EQUALS_MINIAPP_USER = BLOCKED
JOIN_TO_VISIT_RESOLUTION = BLOCKED
JOIN_TO_TEST_MARKER_RESOLUTION = BLOCKED
REAL_MAX_IDENTITY_E2E = BLOCKED
OWNER_ACTION_REQUIRED = авторизовать второй обычный MAX-аккаунт для одного тестового вступления
```

## Попытка с авторизованным вторым MAX-аккаунтом

Дата: 2026-08-17.

Вторая обычная MAX Web-сессия была доступна. Invite тестового канала показал
`Подать заявку`, а после нажатия UI показал состояние: «Вы подпишетесь, когда
администратор канала одобрит заявку». Это подтверждает реальное создание
pending-заявки на стороне MAX; заявка не симулировалась.

Новый visit был создан через production landing flow. Из-за URL-экранирования
внутренний yclid сохранился как `REAL_MAX_E2E_001&utm_source=second-account`,
но TEST marker присутствует в начале значения и используется только для этого
TEST visit.

```text
SECOND_MAX_USER_AVAILABLE = YES
SECOND_USER_NOT_CHANNEL_OWNER = YES

VISIT_ID = 3985e286-34cf-4b60-84c8-da6a6b8776fe
VISITOR_TOKEN_HASH = bbef2df3c18fbcd848c56e27a7b4c85a
START_TOKEN_HASH = 40ecdcab2ec6b3ca252ce7d0eaed99b4
SECOND_MAX_USER_ID_HASH = 865ebf4e7611a83baa4a71b237f9737f

START_PARAM_RECEIVED = YES
REAL_MAX_USER_ID_RECEIVED = YES
MAX_USER_BOUND_TO_VISIT = YES
TEST_CHANNEL_TARGET_RESOLVED = YES
CHANNEL_OPENED = YES (MAXIBOT Mini App target card opened)

MAX_UI_JOIN_REQUEST_CREATED = YES
REAL_JOIN_REQUEST_OBSERVED_BY_PRODUCTION_WORKER = NO
JOIN_USER_EQUALS_MINIAPP_USER = NOT_PROVEN
JOIN_TO_VISIT_RESOLUTION = BLOCKED
JOIN_TO_TEST_MARKER_RESOLUTION = BLOCKED
REAL_MAX_IDENTITY_E2E = BLOCKED
```

Runtime events для второго visit:

```text
MAX_REDIRECT_SENT
max_identity_linked (user hash 865ebf4e7611a83baa4a71b237f9737f)
channel_open_clicked (same user hash)
```

### Точный технический blocker

В production DB для TEST channel отсутствуют polling bindings:

```text
channels.bot_connection_id = NULL
channels.max_owner_user_id = NULL
max_pending_join_requests rows = 0
max_join_events rows = 0
```

Поэтому MAX UI заявку показывает, но production worker не может связать её с
зарегистрированным каналом и записать `MaxPendingJoinRequest`. Для полного
PASS требуется штатно подключить существующий production bot к TEST channel
или зарегистрировать channel через штатный bot_added flow. Это изменение
внешнего channel/bot state не выполнялось в рамках текущего разрешения.

```text
OWNER_ACTION_REQUIRED = подключить существующий production MAXIBOT к TEST channel
                         штатным bot_added/application flow
```

## Повторная попытка подключения bot_added

Дата: 2026-08-17.

Новая заявка и новый visit намеренно не создавались. В доступном browser bridge
сейчас видна только обычная вторая MAX-сессия; owner MAX-сессия отсутствует.
Штатный `bot_added/application flow` требует owner-контекст TEST channel, поэтому
подключение production MAXIBOT не выполнялось вслепую.

```text
OWNER_MAX_SESSION_VISIBLE = NO
TEST_BOT_CONNECTED_TO_CHANNEL = NO
BOT_CONNECTION_ID_SET = NO
MAX_OWNER_USER_ID_SET = NO
PENDING_POLLING_ENABLED_FOR_TEST_CHANNEL = NO

REAL_JOIN_REQUEST_OBSERVED_BY_WORKER = NO
max_pending_join_requests = 0
max_join_events = 0
REAL_MAX_IDENTITY_E2E = BLOCKED
```

Единственный blocker текущего шага:

```text
OWNER_ACTION_REQUIRED = открыть owner MAX-сессию и выполнить штатный bot_added/
                         application flow только для MAXIBOT E2E TEST
```

### О malformed yclid

Значение `REAL_MAX_E2E_001&utm_source=second-account` появилось из-за
экранирования `&` в нашей внутренней тестовой команде при обращении к localhost
API (`%26` попал внутрь значения yclid). Это не доказательство ошибки parser-а
production landing. Перед реальным Директом нужен отдельный regression test с
нормальным URL вида `?yclid=...&utm_source=...`; production code в рамках этого
шага не менялся.

## Owner-flow: MAXIBOT добавлен в TEST channel

Дата: 2026-08-17.

В owner MAX-сессии выполнены только действия в пустом TEST channel:

1. `MAXIBOT` добавлен в участники `MAXIBOT E2E TEST`.
2. `MAXIBOT` назначен администратором канала.
3. Рабочие каналы, production Mini App и настройки Яндекса не затрагивались.

MAX UI подтвердил `MAXIBOT` среди участников и показал одну существующую
заявку. Однако штатный production worker не создал DB binding:

```text
OWNER_MAX_SESSION_VISIBLE = YES
MAX_UI_BOT_MEMBER = YES
MAX_UI_BOT_ADMIN = YES

TEST_BOT_CONNECTED_TO_CHANNEL = NOT_REFLECTED_BY_APP
BOT_CONNECTION_ID_SET = NO
MAX_OWNER_USER_ID_SET = NO
PENDING_POLLING_ENABLED_FOR_TEST_CHANNEL = NO
REAL_JOIN_REQUEST_OBSERVED_BY_WORKER = NO
max_pending_join_requests = 0
max_join_events = 0
```

### Причина

В текущем коде `Channel.bot_connection_id` является FK на таблицу
`bot_connections`, которая обслуживает отдельные custom bots. Production
MAXIBOT работает через основной production MAX client и не имеет отдельной
`BotConnection`-строки (`bot_connections: 1 total, 0 ACTIVE`). Обработчик
`bot_added` вызывает `sync_max_chat_metadata`, который заполняет metadata,
owner и target link уже подключённого канала, но не создаёт произвольный
`BotConnection` и не связывает его с production client.

Поэтому ручная подстановка `bot_connection_id` или `max_owner_user_id` в DB не
является штатным flow и не выполнялась. Pending-заявка остаётся видимой в MAX
UI, но worker не может записать её в `MaxPendingJoinRequest`.

```text
OWNER_ACTION_REQUIRED = отдельное утверждённое изменение application binding
                         contract для polling этого TEST channel
```

Заявка не одобрена, новые visit/JOIN_REQUEST не создавались.

## PRIMARY client binding implementation: 2026-08-17

Выполнено минимальное изменение connection contract без special-case для TEST
channel и без ручной подстановки FK:

- добавлен явный `Channel.max_connection_kind` со значениями `PRIMARY` и
  `BOT_CONNECTION`;
- миграция `0027_channel_max_connection_kind` переводит существующие строки с
  `bot_connection_id` в `BOT_CONNECTION`, остальные — в `PRIMARY`;
- `bot_added` основного клиента при синхронизации канала устанавливает
  `PRIMARY`, только если у канала нет custom `BotConnection`;
- переключение custom bot сохраняет обратную совместимость и явно выставляет
  `BOT_CONNECTION`/`PRIMARY`;
- добавлен единый resolver, который возвращает primary client либо active
  custom client и явно отклоняет missing/disabled/несовместимые связи.

Production deploy выполнен на `135.106.166.46`; API и выделенный worker
пересозданы штатно. Env/config, production bot settings и рабочие каналы не
изменялись. Миграция применена. TEST channel теперь имеет `PRIMARY`,
`bot_connection_id` остаётся `NULL`, как и должно быть для основного клиента.

```text
PRIMARY_CLIENT_BINDING_IMPLEMENTED = YES
CUSTOM_BOT_CONNECTION_REGRESSION = PASS (targeted tests + compileall)
TEST_CHANNEL_BOUND_TO_PRIMARY = YES
PENDING_WORKER_POLLING_TEST_CHANNEL = BLOCKED

REAL_JOIN_REQUEST_OBSERVED_BY_WORKER = NO
JOIN_USER_EQUALS_MINIAPP_USER = NOT_PROVEN
JOIN_TO_VISIT_RESOLUTION = NOT_PROVEN
JOIN_TO_TEST_MARKER_RESOLUTION = NOT_PROVEN

MAX_PENDING_ROW_CREATED = NO
MAX_JOIN_EVENT_CREATED = NOT_PROVEN_BY_PENDING_WORKER
DUPLICATE_JOIN_PREVENTED = PASS (existing unique/dedupe contract; no new event created)
MEMBER_CONFIRMED = NOT_PERFORMED
REAL_MAX_IDENTITY_E2E = BLOCKED

YANDEX_CONVERSION_SEND = DISABLED
PRODUCTION_CODE_CHANGED = YES (connection contract only)
PRODUCTION_CONFIG_CHANGED = NO
PRODUCTION_TEST_DATA_CREATED = NO
EXISTING_PRODUCTION_DATA_MODIFIED = NO
```

### Оставшийся технический blocker

Официальный MAX Bot API предоставляет `bot_added`, `user_added`,
`user_removed`, `get members` и администраторов, но не предоставляет endpoint
для чтения pending join requests или approve конкретной заявки. Поэтому
PRIMARY binding теперь штатно разрешается, но pending worker нельзя честно
сделать рабочим на официальном transport contract. Существующий адаптер
`MaxWebSession` использует user-level WebSocket; его подключение запрещено
AGENTS.md для этого проекта без отдельного утверждённого ТЗ. Реальный worker
PASS и `REAL_MAX_IDENTITY_E2E = PASS` до появления разрешённого официального
pending transport не заявляются.

Итог по текущей задаче: connection contract — PASS; реальное чтение pending
заявки и обратная attribution chain — BLOCKED.

## Controlled MaxWebSession read attempt: 2026-08-17

Выполнена одна попытка read-only подключения через существующий сохранённый
owner auth-контур. Запрос был ограничен pending-list для
`MAXIBOT E2E TEST`; opcode approve не отправлялся.

```text
MAX_WEB_SESSION_CONNECTED = NO
TEST_CHANNEL_PENDING_READ = NO
REAL_JOIN_REQUEST_OBSERVED = NO
```

WebSocket endpoint был доступен, но сохранённая авторизация завершилась
`ConnectionClosedOK` до перехода в authenticated state. Старый локальный
pending capture не использован: его channel fingerprint не совпадает с
TEST channel, а user fingerprint не совпадает с ранее доказанным вторым MAX
identity. Это исключает перенос старого результата или ручную подстановку
user_id.

Ограничение не связано с connection binding или базой MAXIBOT:

```text
JOIN_USER_EQUALS_MINIAPP_USER = NOT_PROVEN
JOIN_TO_VISIT_RESOLUTION = NOT_PROVEN
JOIN_TO_TEST_MARKER_RESOLUTION = NOT_PROVEN
MAX_PENDING_ROW_CREATED = NO
MAX_JOIN_EVENT_CREATED = NO
DUPLICATE_JOIN_PREVENTED = NOT_RUN
MEMBER_CONFIRMED = NOT_PERFORMED
REAL_MAX_IDENTITY_E2E = BLOCKED
YANDEX_CONVERSION_SEND = DISABLED
```

Единственный blocker для продолжения: обновить/передать действующий owner
MAX Web auth state из текущей авторизованной сессии. После этого допустим ровно
один `list_pending_join_requests` с allowlist только
`-77886885970047`; approve по текущему ТЗ не требуется.

## Fresh auth-state retry: 2026-08-17

Обычная Chrome owner-сессия доступна и открыта на TEST channel. При этом
доступный `MaxWebSession` не умеет безопасно извлечь из неё новый auth state:
сохранённый state даёт закрытие WebSocket до `AUTHENTICATED`. Cookies,
localStorage, raw auth token и raw frames не читались и в отчёт не выводились.

```text
OWNER_CHROME_SESSION_VISIBLE = YES
FRESH_AUTH_STATE_EXPORTED = NO
MAX_WEB_SESSION_AUTHENTICATED = NO
TEST_CHANNEL_PENDING_READ = NOT_RUN
REAL_MAX_IDENTITY_E2E = BLOCKED
```

Единственный требуемый owner action: обновить auth state для MaxWebSession
через согласованный secret-safe способ (без публикации токена). После этого
останется выполнить только один allowlisted read pending-list для
`-77886885970047`.

## Official `user_added` attribution path: 2026-08-17

По новой схеме `MaxWebSession` не использовался. После ручного approve в owner
MAX UI выделенный production worker получил официальный update `user_added`.
Обработан только TEST channel; raw user id не выводится.

```text
USER_ADDED_RECEIVED = YES
USER_ADDED_CHANNEL = TEST_ONLY
MEMBER_USER_HASH = 865ebf4e7611a83baa4a71b237f9737f
MEMBER_USER_EQUALS_MINIAPP_USER = YES

MEMBER_TO_VISIT_RESOLUTION = PASS
MEMBER_TO_YCLID_RESOLUTION = PASS
RESOLVED_VISIT_ID = 3985e286-34cf-4b60-84c8-da6a6b8776fe
RESOLVED_TEST_MARKER = REAL_MAX_E2E_001
REAL_MAX_IDENTITY_E2E = PASS
ATTRIBUTION_CHAIN_PROVEN = YES
YANDEX_CONVERSION_SEND = DISABLED
```

Проверенный read-only lookup:

```text
user_added.user_id
  → MaxIdentityLink
  → visit 3985e286-34cf-4b60-84c8-da6a6b8776fe
  → yclid REAL_MAX_E2E_001 (test value)
```

В тестовом визите сохранено дополнительное значение
`utm_source=second-account` из прежнего malformed test request; оно не является
частью production parser proof и не отправлялось в Яндекс.

Важно: текущий conservative membership handler записал официальный
`user_added` как membership, но оставил `TrafficMembership.attribution_status`
`UNATTRIBUTED`, поскольку без pending `join_request` он намеренно не продвигает
временную attribution. Поэтому identity chain доказана read-only через
`MaxIdentityLink`, но отдельный production status promotion
`user_added + exact MaxIdentityLink → EXACT` остаётся последующим минимальным
изменением, если этот статус должен храниться в `TrafficMembership`.

## `user_added` → EXACT → `max_subscriber` queue: 2026-08-17

Реализовано и развёрнуто минимальное правило exact attribution:

```text
official user_added
  → same channel_id + user_id
  → exactly one MaxIdentityLink visit
  → TrafficMembership.attribution_status = EXACT
  → OfflineConversion(goal=max_subscriber, target=YANDEX, dry_run)
```

Exact match не создаётся, если identity отсутствует или один MAX user связан с
несколькими visits в этом channel. Ссылки другого channel не участвуют в
разрешении. Повторный `user_added`/reconciliation использует существующий
unique ключ `(visit_id, membership_id, target)` и не создаёт второй conversion.

Текущий существующий TEST membership reconciled через application layer без
переигрывания MAX event:

```text
USER_ADDED_TO_EXACT = PASS
MAX_SUBSCRIBER_CREATED = PASS
IDEMPOTENCY = PASS (queue count remains 1 after repeat)
CONVERSION_QUEUE_READY = YES
YANDEX_SEND = DISABLED (status=dry_run)
REGRESSION = PASS (37 targeted tests, compileall PASS)
```

Production API и выделенный worker после deploy healthy; рабочие каналы,
production config и Yandex settings не изменялись. Добавлена миграция
`0028_offline_conversion_goal` для отдельного поля `goal`.

## Controlled Yandex offline conversion stage: 2026-08-17

Yandex preflight для TEST channel выполнен без отправки:

```text
YANDEX_AUTH_READY = NO
METRIKA_COUNTER_READY = NOT_VERIFIED
MAX_SUBSCRIBER_GOAL_READY = NOT_VERIFIED
OFFLINE_CONVERSION_PAYLOAD_VALID = PASS (local contract test)
TEST_CONVERSION_SENT = NO
YANDEX_ACCEPTED = NOT_RUN
RETRY_IDEMPOTENCY = PASS (queue unique key + controlled sender guard)
```

Причина остановки — для TEST channel отсутствует действующий сохранённый
Yandex OAuth token. Массовый sender не включался.

Также исправлен payload contract: согласно официальной документации Yandex
`Target` в CSV должен содержать ID цели, а не её имя. Controlled sender теперь
находит/создаёт `max_subscriber` goal, использует его numeric ID и отправляет
только после exact-membership gate. `YANDEX_CONVERSION_DRY_RUN` оставлен
`true`; production queue sender не разрешён до успешного single-test upload.

## Promotion channel visibility and Yandex binding: 2026-08-17

Исследован фактический список раздела «Продвижение». `render_screen` строит его
по условию `Channel.admin_id == current Admin.id`; отдельного фильтра по названию,
`is_test`, `chat_id`, `TrafficProfile` или landing нет. Поэтому TEST-канал не был
виден не из-за promotion eligibility, а потому что до исправления был привязан к
другому `Admin`.

До исправления:

```text
TEST_CHANNEL_EXISTS = YES
TEST_CHANNEL_OWNER_BINDING = WRONG_ADMIN
TEST_CHANNEL_VISIBLE_TO_CURRENT_USER = NO
TEST_CHANNEL_PROMOTION_ELIGIBLE = YES (TrafficProfile + active LandingPage)
FILTER_EXCLUDING_TEST_CHANNEL = Channel.admin_id != current Admin.id
```

Исправлен общий безопасный owner-binding в штатном `sync_max_chat_metadata`:
канал находится по внешнему `chat_id` и может быть принят текущим владельцем
только когда официальный `GET /chats/{chat_id}` подтверждает того же владельца;
при конфликтующем подтверждённом владельце перебиндинг запрещён. Специальной
ветки по `chat_id=-77886885970047` нет. Изменено только состояние пустого TEST
канала; рабочие каналы не менялись.

После deploy выполнен application-layer sync TEST-канала через официальный MAX
chat metadata; owner совпал с owner рабочих каналов. Повторный read-only запрос
по тем же условиям Promotion показал:

```text
TEST_CHANNEL_OWNER_BINDING = FIXED
TEST_CHANNEL_VISIBLE_TO_CURRENT_USER = YES
TEST_CHANNEL_PROMOTION_ELIGIBLE = YES
TEST_CHANNEL_VISIBLE_IN_PROMOTION = YES
FIX_REQUIRED = DONE (generic bot_added/sync owner binding)
PRODUCTION_CODE_CHANGED = YES (generic fix deployed)
WORKING_CHANNELS_MODIFIED = NO
```

Текущий Yandex OAuth фактически channel-scoped: Redis содержит token mapping
для channel key рабочего канала, а отдельного mapping для TEST нет. Поэтому
account-level переиспользование токена рабочего канала не применяется.

```text
YANDEX_AUTH_PATH = Promotion → MAXIBOT E2E TEST → Яндекс → OAuth connect
TEST_YANDEX_AUTH_READY = NO (до отдельного OAuth connect)
YANDEX_CONVERSION_SEND = DISABLED
```

Конверсия в рамках этой проверки не отправлялась; counter/goal для TEST не
считались подтверждёнными до отдельного OAuth connect в его Promotion flow.

## Controlled Yandex test attempt: 2026-08-17

После подключения TEST channel выполнен preflight через application layer:

```text
YANDEX_AUTH_READY = YES
METRIKA_COUNTER_READY = YES (counter 111675886)
MAX_SUBSCRIBER_GOAL_READY = NO (до controlled setup)
```

Цель `max_subscriber` была создана штатным Yandex Metrica API в TEST-счётчике;
read-only проверка после этого показала `status=Active`. Массовый sender и его
настройка `YANDEX_CONVERSION_DRY_RUN=true` не изменялись.

Была выполнена ровно одна controlled POST-загрузка из существующего EXACT test
membership. Яндекс ответил:

```text
OFFLINE_CONVERSION_PAYLOAD_VALID = PASS (локальный contract gate)
TEST_CONVERSION_SENT = NO
YANDEX_ACCEPTED = NO
HTTP_STATUS = 400 Bad Request
YANDEX_UPLOAD_STATUS = NOT_CREATED (read-only upload list is empty)
RETRY_IDEMPOTENCY = PASS (повторный POST не выполнялся)
YANDEX_CONVERSION_DRY_RUN = true (массовая очередь)
```

Тело ошибки не было сохранено первым временным probe и повторная отправка для
получения body намеренно не выполнялась: это могло бы создать второй внешний
upload. Поэтому точный отклонённый параметр пока `UNKNOWN`; обходов и повторов
не делалось. До успешной single-test загрузки production queue sender остаётся
запрещён.

## Yandex CSV contract retry: 2026-08-17

Исправлен sender contract и задеплоен на production API/worker:

```text
POST /management/v1/counter/111675886/offline_conversions/upload
Authorization = OAuth (not logged)
multipart/form-data field = file
CSV encoding = UTF-8
CSV header = Yclid,DateTime,Target
Target = numeric goal id
DateTime = Unix timestamp in the past
```

Добавлено безопасное non-2xx logging: HTTP status, ограниченное тело ответа,
request-id/relevant headers и только hash yclid; OAuth и полный yclid не пишутся.

Выполнен ровно один разрешённый retry той же тестовой строки:

```text
HTTP_STATUS = 400
YANDEX_ERROR_BODY = {"errors":[{"error_type":"invalid_uploading","message":"Incorrect data format, error in line number 1"}],"code":400,"message":"Incorrect data format, error in line number 1"}
FAILED_FIELD = yclid (наиболее вероятно; API поле явно не назвал)
REQUEST_FORMAT = Yclid,DateTime,Target; multipart/form-data field=file; UTF-8
TEST_CONVERSION_SENT = NO
YANDEX_ACCEPTED = NO
UPLOAD_ID = NONE
UPLOAD_STATUS = NOT_CREATED
RETRY_IDEMPOTENCY = PASS
YANDEX_CONVERSION_DRY_RUN = true
```

Причина остаётся ограниченной доказательством API: тело указывает на неверный
формат строки, но не называет колонку. Тестовый yclid является синтетическим
маркером `REAL_MAX_E2E_001`, а не реальным идентификатором клика Yandex Direct,
поэтому он является главным кандидатом на отклонённое поле. Новых POST после
этого retry не выполнялось. Массовый sender не включён.

## REAL YCLID production smoke preflight: 2026-08-17

Выполнен read-only production DB scan без conversion upload:

```text
VISITS_WITH_YCLID = 49
NON_TEST_YCLID_VISITS = 46
NON_TEST_YCLID_WITH_EXACT_MEMBERSHIP = 0
```

Ни один существующий non-test yclid нельзя использовать: среди них нет
реального подписчика с однозначным EXACT membership. Синтетические значения
не используются и в Yandex больше не отправляются.

Система готова к одному реальному Direct smoke:

```text
PROMOTION_URL = https://go.maxibot.jobjin.ru/e2e-test-20260817
REAL_YCLID_CAPTURE = production visit.yclid
ATTRIBUTION = existing visit → startapp → verified MAX user → user_added → EXACT
UPLOAD_LIMIT = exactly one controlled upload after real member confirmation
MASS_SENDER = DISABLED
```

Текущий статус: `REAL_YCLID_PRODUCTION_SMOKE = BLOCKED`, пока не будет
совершён настоящий клик из Яндекс Директа и полный новый пользовательский flow.
Сгенерировать настоящий yclid внутри MAXIBOT нельзя; тестовые маркеры для этого
не подходят.

## REAL YCLID one-visit monitoring: 2026-08-17

Для следующего прогона подготовлен allowlist мониторинга только для нового
визита TEST promotion URL. Synthetic/test markers отбрасываются; conversion
upload до полного подтверждения не выполняется.

```text
MONITOR_TARGET = https://go.maxibot.jobjin.ru/e2e-test-20260817
MONITOR_SCOPE = new TrafficVisit after monitor start
REAL_YCLID_CAPTURED = WAITING_FOR_REAL_DIRECT_CLICK
STARTAPP_FLOW = WAITING
MAX_IDENTITY_BOUND = WAITING
USER_ADDED_RECEIVED = WAITING
ATTRIBUTION_STATUS = WAITING
MEMBER_TO_REAL_YCLID = WAITING
MAX_SUBSCRIBER_CREATED = WAITING
MASS_SENDER = DISABLED
```

Для запуска этого monitor нужен внешний реальный клик из Яндекс Директа.
Открытие promotion URL напрямую не создаёт настоящий yclid и не считается
smoke-тестом.

## Promotion slug routing bugfix and browser smoke: 2026-08-17

Проверка slug `FZ3kpwshUOqIvA` показала точное разрешение:

```text
FZ3kpwshUOqIvA
  → LandingPage (active; id hash de7fb585b2bcf685)
  → GET /{public_id}
  → landing_root()
  → _landing_page()
  → _traffic_redirect(via_bot=True)
```

Это не Suggestion route. Причина внешнего текста Предложки была в данных:
`LandingPage.description` при создании копировал `Channel.welcome_text`, а в
канале был текст Предложки. Поэтому traffic handler визуально рендерил чужой
copy, хотя DB object и route были promotion/traffic.

Исправление общее:

- новые landing получают отдельное traffic-описание;
- legacy landing с маркерами Suggestion не рендерят их в traffic redirect;
- special-case по `FZ3kpwshUOqIvA` отсутствует.

Browser smoke именно по production URL после deploy:

```text
PROMOTION_SLUG_RESOLVES_TO_TRAFFIC_LANDING = YES
WRONG_SUGGESTION_LANDING_GONE = YES
VISIT_CREATED = YES
START_TOKEN_CREATED = YES
MAX_DEEPLINK_GENERATED = YES
MAX_DEEPLINK_OPENED = NOT_PROVEN (Chrome оставил max:// fallback)
MINI_APP_OPENED = NO (не доказано этим Chrome smoke)
CHANNEL_OPENED = NO (не достигнуто)
READY_FOR_YANDEX_DIRECT = NO
```

Read-only DB после smoke: новый визит имеет `status=BOT_REDIRECT_SENT`,
`startapp_token` присутствует и есть `MAX_REDIRECT_SENT`; MAX user ещё не
привязан. Поэтому routing bug исправлен, но полный MAX identity E2E этим
браузерным проходом не объявляется готовым.
## Production browser MAX handoff smoke: 2026-08-17

Root cause второго production bug: `window.location.href = max://...` делал
custom scheme обязательной top-level навигацией, поэтому Safari показывал
`invalid address` до web fallback.

Исправлено без изменения attribution: native `max://` остаётся hidden iframe
попыткой, а через 1500 ms открывается HTTPS MAX URL с тем же startapp token.
Новый visit/token на fallback не создаётся.

Безопасная структура, подтверждённая browser JS:

```text
ACTUAL_NATIVE_DEEPLINK = max://max.ru/se13802047_1_bot?startapp=<opaque-token>
ACTUAL_WEB_FALLBACK_URL = https://max.ru/se13802047_1_bot?startapp=<same-opaque-token>
STARTAPP_PRESENT = YES
STARTAPP_TOKEN_LENGTH = 32
STARTAPP_TOKEN_ENCODING = URL-safe base64 alphabet; no double encoding observed
DEEPLINK_SCHEME = max:
DEEPLINK_HOST = max.ru
DEEPLINK_PATH = /se13802047_1_bot
```

Финальный browser execution smoke по production URL:

```text
LANDING_LOADED = YES
TRAFFIC_FLOW_RENDERED = YES
JS_EXECUTED = YES
CONSOLE_ERRORS = NONE from page (only unrelated extension warnings)
NETWORK_ERRORS = NONE observed from page
VISIT_CREATED = YES
START_TOKEN_CREATED = YES
VALID_MAX_TARGET_GENERATED = YES
SAFARI_INVALID_ADDRESS = NOT_REPRODUCED after fallback fix
FALLBACK_TRIGGERED_IF_NEEDED = YES
MAX_OPENED = YES (HTTPS MAX web target)
MINI_APP_OPENED = NOT_AUTOMATABLE
CHANNEL_MOSCOW365_OPENED = NOT_AUTOMATABLE
LANDING_TO_DEEPLINK_MS = browser execution observed; exact native handoff timing not measurable
DEEPLINK_TO_FALLBACK_MS = 1500 configured / observed browser transition
OWNER_MANUAL_CHECK_REQUIRED = YES
MANUAL_TEST_URL = https://go.maxibot.jobjin.ru/FZ3kpwshUOqIvA
EXPECTED_BEHAVIOR = traffic landing → native attempt → HTTPS MAX bot with same startapp
READY_FOR_YANDEX_DIRECT = NO
```

Read-only DB подтверждает новый визит со статусом `BOT_REDIRECT_SENT`,
startapp token и `MAX_REDIRECT_SENT`. Chrome automation достиг HTTPS MAX
страницы с `/se13802047_1_bot?startapp=...`; нативный Mini App и открытие
канала нельзя доказать без реального MAX Web/native context.
## Production Mini App handoff: official HTTPS contract, 2026-08-17

### Root cause

Предыдущая реализация пыталась открыть `max://max.ru/<bot>?startapp=<token>`
через скрытый iframe, а затем через таймер переводила браузер на HTTPS URL.
В доступном browser smoke это приводило к странице профиля бота; на Safari
custom scheme мог давать `invalid address`. Официальная документация MAX для
Mini App описывает HTTPS deep link, а не такой iframe-handoff.

### Изменение

В `app/main.py` удалён custom-scheme iframe и задержка fallback. Landing
сразу выполняет `window.location.replace()` официального URL:

```text
https://max.ru/<botName>?startapp=<same-opaque-token>
```

Новый visit и новый token на handoff не создаются. Target channel и attribution
логика не менялись.

### Production smoke

```text
URL = https://go.maxibot.jobjin.ru/FZ3kpwshUOqIvA
LANDING_LOADED = YES
TRAFFIC_FLOW_RENDERED = YES
VISIT_CREATED = YES
START_TOKEN_CREATED = YES
JS_REDIRECT = YES
TARGET_SCHEME = https
TARGET_HOST = max.ru
TARGET_PATH = /<configured MAX bot username>
STARTAPP_PRESENT = YES
STARTAPP_TOKEN = opaque, URL-safe, same visit token (raw value omitted)
CUSTOM_SCHEME_IN_HTML = NO
NATIVE_HANDOFF_IFRAME = NO
API_HEALTH_READY = PASS
```

### Native UX status

Официальный контракт подтверждает `https://max.ru/<botName>?startapp=<payload>`;
payload передаётся в `start_param`. Документация также указывает, что
`openMaxLink` открывает такой URL внутри уже работающего MAX Mini App, но это
не является API для запуска из Safari/Chrome. В desktop browser smoke HTTPS
URL открывает страницу профиля бота, поэтому по принятому критерию:

```text
BOT_PROFILE_PAGE_SHOWN = YES (desktop HTTPS smoke)
MAX_OPENED = NO (profile page is not accepted as native MAX)
MAX_NATIVE_OPENED = NOT_PROVEN
MINI_APP_OPENED = NOT_PROVEN
START_PARAM_RECEIVED = NOT_PROVEN in this desktop smoke
CHANNEL_MOSCOW365_OPENED = NOT_PROVEN
EXTRA_CLICK_REQUIRED = NOT_PROVEN
SAFARI_INVALID_ADDRESS = NOT_REPRODUCED after removing custom scheme
```

Это не PASS для mobile/native funnel. Для окончательного PASS нужен ручной
smoke на iOS и Android с установленным MAX: открыть production URL, проверить
что Universal/App Link передаёт пользователя сразу в native MAX Mini App, затем
проверить `start_param` и автоматическое открытие Москва 365. Desktop Chrome не
может доказать этот системный handoff.

```text
PROMOTION_URL_USABLE = NO
READY_FOR_YANDEX_DIRECT = NO
OWNER_MANUAL_CHECK_REQUIRED = YES
MANUAL_TEST_URL = https://go.maxibot.jobjin.ru/FZ3kpwshUOqIvA
EXPECTED_BEHAVIOR = landing → native MAX Mini App with same startapp → Москва 365
```

## Desktop Web funnel final fix — 2026-08-17

This section supersedes the earlier desktop `NOT_PROVEN` result above.

Fresh browser tracing proved that the MAXIBOT `se..._bot` route supports the
same Mini App/startapp capabilities as the competitor bot. The bot owner
prefix was not the cause. With a fresh one-time token,
`https://web.max.ru/<MAXIBOT>?startapp=...` automatically launched `/mini` and
delivered signed MAX identity plus the same `start_param`.

The exact blocker was the final line of the Mini App: plain
`window.location.replace(target_url)` navigated only the embedded iframe to the
channel invite. It did not navigate the containing MAX client.

Implemented and deployed as `maxibot-vps-api:20260817-openmaxlink`:

```text
desktop landing → automatic web.max.ru/<bot>?startapp=<token>
mobile landing  → automatic max.ru/<bot>?startapp=<token>
Mini App        → identify → channel-open → WebApp.openMaxLink(target_url)
```

Fresh production Chrome E2E:

```text
LANDING_CLICK_REQUIRED = NO
STARTAPP_RECEIVED = YES
SIGNED_MAX_IDENTITY_RECEIVED = YES
MAX_IDENTITY_LINK = YES
CHANNEL_OPEN_CLICKED_BACKEND_EVENT = YES
TOP_LEVEL_CHANNEL_OPENED = YES
FINAL_CHANNEL = Москва 365
EXTRA_CLICK_BEFORE_CHANNEL = 0
```

The verification account was already an owner/member, so no join-request CTA
was expected in this run. The separate second-user test remains the proof for
`Подать заявку → manual approve → official user_added → EXACT`.

```text
TARGETED_TESTS_IN_PRODUCTION_CONTAINER = 22 PASS
API_HEALTH = ready / max_mode=real
API_POLLING = false
WORKER_POLLING = true
YANDEX_SEND = DISABLED / NOT PERFORMED
PROMOTION_URL_USABLE_IN_TESTED_DESKTOP_WEB_SESSION = YES
```

## iOS channel handoff correction — 2026-08-17

Owner iPhone smoke reached the MAXIBOT Mini App but remained on its blank
surface instead of opening the channel. Server evidence and the screenshot
showed that the failure was after Mini App launch. The generic implementation
used `WebApp.openMaxLink()` for every platform; this was already proven for MAX
Web but was not valid evidence for iOS channel-invite behavior.

The public competitor runtime uses a platform-specific handoff: Web calls
`openMaxLink`, Android navigates to the target, and iOS programmatically clicks
a normal `https://max.ru/...` anchor with `_blank` and deliberately does not
close the Mini App because closing it also closes the join-request sheet.

Deployed production image: `maxibot-vps-api:20260817-ioshandoff`.

```text
WEB_CHANNEL_HANDOFF = WebApp.openMaxLink(target_url)
ANDROID_CHANNEL_HANDOFF = window.location.replace(target_url)
IOS_CHANNEL_HANDOFF = anchor target=_blank click
IOS_MINI_APP_AUTO_CLOSE = NO
NORMAL_USER_CLICK_BEFORE_CHANNEL = 0
STUCK_FALLBACK = visible after 3.2 seconds only
PRODUCTION_CONTAINER_TESTS = 22 PASS
API_HEALTH = ready / max_mode=real
API_POLLING = false
WORKER_POLLING = true
IOS_REAL_RETEST = REQUIRED
YANDEX_SEND = NOT PERFORMED
```

## Yandex goal alignment — 2026-08-17

The approved learning goal is `userDidSubscribe`. The internal name
`max_subscriber` must not be used as the Yandex Metrica goal/CSV Target.

Production image: `maxibot-vps-api:20260817-userdidsubscribe`.

```text
official user_added → EXACT → OfflineConversion.goal=userDidSubscribe
Yandex CSV Target → numeric ID of Metrica goal userDidSubscribe
legacy send_max_subscriber helper → delegates to userDidSubscribe sender
```

The only existing `max_subscriber / dry_run` TEST queue record was migrated to
`userDidSubscribe / dry_run` (`UPDATE 1`). No sent/pending record was touched.
No migration was required because `OfflineConversion.goal` already exists.

```text
TARGETED_PRODUCTION_TESTS = 25 PASS
API_HEALTH = ready / max_mode=real
API_POLLING = false
WORKER_POLLING = true
QUEUE_GOAL = userDidSubscribe
QUEUE_STATUS = dry_run
YANDEX_UPLOAD = NOT PERFORMED
```
## Fast atomic Mini App bridge: 2026-08-17

По пользовательской записи экрана подтверждено, что production flow открывал
канал автоматически, но перед ним были заметны bot conversation и загрузка
Mini App. Bot conversation является контейнером MAX для привязанного Mini App;
скрыть его со стороны HTML нельзя. Для сокращения контролируемой приложением
части задержки выполнено минимальное совместимое изменение:

- добавлен `POST /api/traffic/resolve`, который в одной транзакции валидирует
  signed MAX initData, связывает MAX user с visit/startapp, фиксирует оба
  idempotent event и возвращает только валидный channel target;
- `miniapp/traffic.html` переключён с двух последовательных POST
  (`identify`, затем `channel-open`) на один `resolve`;
- прежние endpoints сохранены для обратной совместимости;
- membership, `user_added -> EXACT`, conversion queue и `userDidSubscribe` не
  изменялись.

Production image: `maxibot-vps-api:20260817-fastbridge`. API healthy,
`MAX_RUN_POLLING=false`; worker running, `MAX_RUN_POLLING=true`. Public `/mini`
содержит один `/api/traffic/resolve` и не вызывает старые два endpoint;
анонимный вызов нового endpoint отклоняется fail-closed. Внутри image targeted
suite: 34 passed; дополнительный локальный attribution regression: 33 passed.

```text
ATOMIC_IDENTITY_CHANNEL_RESOLVE = PASS
LEGACY_ENDPOINT_COMPATIBILITY = PRESERVED
MAX_INIT_DATA_VALIDATION = PRESERVED
STRICT_CHANNEL_TARGET = PRESERVED
ATTRIBUTION_AND_YANDEX_LOGIC_CHANGED = NO
PRODUCTION_HEALTH = PASS
SINGLE_POLLER_NEW_HOST = PASS
LEGACY_HOST_RECHECK = BLOCKED_BY_SSH_AUTH
SYSTEM_BOT_CONTAINER_CAN_BE_FULLY_HIDDEN = NO (MAX client behavior)
OWNER_VISUAL_RETEST_REQUIRED = YES
```
