# REAL MAX Mini App Identity PoC

Дата: 2026-08-17

## Production-flow preflight for REAL_MAX_E2E_001

```text
EXISTING_PRODUCTION_MINI_APP_USED = YES (read-only inspection only)
TEST_CHANNEL_TARGET_RESOLVED = NO
TEST_DATA_CREATED = NO
REAL_MAX_IDENTITY_E2E = BLOCKED
PRODUCTION_CODE_CHANGED = NO
PRODUCTION_CONFIG_CHANGED = NO
ONLY_TEST_DATA_CREATED = NO (no data created)
YANDEX_CONVERSION_SEND = DISABLED
```

Read-only production DB preflight found 2 `channels` and 2
`traffic_profiles`, but no channel with MAX external id
`-77886885970047` and no channel titled `MAXIBOT E2E TEST`. Therefore the
existing production application has no registered target/profile for the test
channel. The штатный landing flow cannot create a `TrafficVisit` for that
channel; it requires an existing `LandingPage`/`TrafficProfile`. Creating or
connecting that channel/profile would be a production configuration change,
which is explicitly prohibited by this test.

No production visit, token, identity link, redirect, JOIN_REQUEST or Yandex
conversion was created.

```text
EXACT_BLOCKER = test channel exists in MAX Web but is not registered in
production MAXIBOT DB; registering it requires production config mutation
```

## Read-only проверка существующего MAXIBOT Mini App

```text
EXISTING_MAXIBOT_READY = YES
EXISTING_MINI_APP_READY = YES
CURRENT_MINI_APP_URL = https://go.maxibot.jobjin.ru/mini
EXISTING_MAXIBOT_USED = NOT_USED_FOR_WRITE_TEST
EXISTING_MINI_APP_USED = YES (read-only browser inspection)
```

Фактическая реализация `/mini` отдаёт `miniapp/traffic.html`:

- подключает `https://st.max.ru/js/max-web-app.js`;
- читает `window.WebApp?.initData`;
- передаёт его в `POST /api/traffic/identify` через `X-MAX-Init-Data`;
- backend валидирует launch data, получает `start_param` из verified values и
  реальный MAX user id из `identity["external_id"]`;
- после bind вызывает `POST /api/traffic/channel-open`;
- затем делает `window.location.replace(data.target_url)`.

В обычном browser-контексте вне MAX `window.WebApp` и `initData` отсутствуют,
что ожидаемо. Production `/mini` визуально отдаёт silent страницу «Переход в
канал» без видимого CTA.

```text
START_PARAM_READ_BY_BACKEND = YES
REAL_MAX_USER_ID_SOURCE = verified MAX initData external_id
CURRENT_CHANNEL_OPEN = backend target_url → window.location.replace
TEST_ONLY_PATCH_REQUIRED = NO (для текущего bridge-контракта)
```

## Почему write E2E не запускался

Existing `/mini` и bot deep-link обслуживаются production backend. Signed token,
созданный в local staging, не существует в production DB. Подключение созданного
`MAXIBOT E2E TEST` к production bot/profile означало бы изменение production
конфигурации, поэтому не выполнялось.

```text
REAL_E2E_WRITE_TEST = BLOCKED
EXACT_BLOCKER = нет отдельного test bot/Mini App, направленного на staging;
использование production bot с staging token невозможно без смешения DB
```

## Последняя проверка существующего test contour

```text
OWNER_SESSION_AVAILABLE = YES (MAX Web owner account)
TEST_CHANNEL_IDENTIFIED = YES
TEST_CHANNEL_NAME = MAXIBOT E2E TEST
TEST_CHANNEL_PRIVATE = YES
TEST_CHANNEL_CHAT_ID = -77886885970047
TEST_CHANNEL_URL_OR_INVITE = max.ru/join/zrBnF6TpPFgbR-...-8DKZ3RuRPwaj2N48
JOIN_REQUEST_SUPPORTED = YES
JOIN_REQUESTS_ENABLED = YES
TEST_PRIVATE_CHANNEL_READY = YES

VERIFIED_BUSINESS_PROFILE_AVAILABLE = NO
TEST_BOT_ALREADY_EXISTS = NOT_FOUND
TEST_BOT_CREATION_AVAILABLE = BLOCKED BY BUSINESS AUTH
TEST_BOT_READY = BLOCKED
TEST_MINI_APP_READY = BLOCKED
STARTAPP_DEEPLINK_READY = BLOCKED
```

Канал проверен через MAX Web: после создания он отображается как приватный,
содержит только владельца (`1 подписчик`), а в настройках включён switch
«Заявки на вступление». Рабочие «Москва 365» и «Твой Петербург» не
использовались.

`business.max.ru/self` в текущем браузере перенаправляет на
`id.business.max.ru/auth` и показывает форму «Введите номер телефона». Вход,
OTP и ручная owner verification недоступны в текущей session; credentials не
запрашивались и не вводились. Это единственный текущий blocker продолжения:

```text
EXACT_OWNER_ACTION_REQUIRED = войти владельцем в business.max.ru/self и
завершить owner/business verification, после чего создать отдельный test bot
и привязать staging Mini App URL.
```

## Итог

```text
TEST_BOT_READY = BLOCKED
TEST_MINI_APP_READY = BLOCKED
TEST_PRIVATE_CHANNEL_READY = BLOCKED

STAGING_READY = YES (local, MAX_CLIENT_MODE=stub)
MINI_APP_CAN_REACH_STAGING = NOT_PROVEN from MAX WebView

START_PARAM_RECEIVED = NOT_STARTED
START_PARAM_EQUALS_SENT_TOKEN = NOT_STARTED
REAL_MAX_USER_ID_RECEIVED = NOT_STARTED

START_TOKEN_RESOLVED = NOT_STARTED
LAUNCH_DATA_VALIDATED = NOT_STARTED
MAX_USER_BOUND_TO_VISIT = NOT_STARTED

CHANNEL_OPENED = NOT_STARTED
EXTRA_USER_CLICK_REQUIRED = NOT_MEASURED
MINI_APP_VISIBLE_DURATION_MS = NOT_MEASURED

REAL_JOIN_REQUEST_OBSERVED = NOT_STARTED
JOIN_USER_EQUALS_MINIAPP_USER = NOT_STARTED

JOIN_TO_VISIT_RESOLUTION = NOT_STARTED
JOIN_TO_TEST_MARKER_RESOLUTION = NOT_STARTED

MEMBER_CONFIRMED = NOT_PERFORMED

TOKEN_REPLAY_BLOCKED = NOT_STARTED
TOKEN_TAMPER_BLOCKED = NOT_STARTED
TOKEN_WRONG_USER_REUSE_BLOCKED = NOT_STARTED

REAL_MAX_IDENTITY_POC = BLOCKED
PRODUCTION_CHANGED = NO
YANDEX_CONVERSION_SEND = DISABLED
```

## Точный blocker

В доступной MAX Web-сессии нет отдельного owner-flow для создания нового
закрытого канала. Developer-чат «Каналы в MAX для бизнеса» после owner
верификации предлагает создание/публикацию публичных каналов, а раздел «МОИ»
не содержит действия создания private channel.

Отдельный test bot и Mini App также нельзя подготовить безопасно без первого
шага и без отдельной owner-сессии/регистрации. Production bot, «Москва 365» и
«Твой Петербург» не использовались.

Согласно ТЗ остановка выполнена именно на требуемом owner-действии:

```text
OWNER_ACTION_REQUIRED = создать отдельный закрытый MAX-канал
OWNER_ACTION_REQUIRED_FOR_BOT = зарегистрировать отдельный test bot/Mini App
```

## Staging

Существующий изолированный staging работает:

```text
API = 127.0.0.1:18000
DB = отдельный staging container/volume
Redis = отдельный staging container/volume
GET /health/ready = 200
MAX_CLIENT_MODE = stub
MAX_RUN_POLLING = false
```

Local stub не считается доказательством REAL MAX identity. Публичный tunnel не
оставлен включённым после предыдущей проверки; staging доступен локально.

## Что не выполнялось

- production MAXIBOT и рабочие каналы не открывались для теста;
- MAX user.id вручную не подставлялся;
- JOIN_REQUEST не симулировался;
- manual approve не выполнялся;
- Яндекс API и conversions не подключались.

## Следующий шаг

После создания владельцем отдельного private test channel, test bot и Mini App
можно продолжить с реальным `startapp=<SIGNED_TOKEN>`. До этого
`REAL_MAX_IDENTITY_POC` остаётся `BLOCKED`; `NEXT_STEP = NOT_SET`.
