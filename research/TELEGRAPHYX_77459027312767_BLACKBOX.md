# TeleGraphyx `77459027312767`: read-only black-box analysis

Дата исследования: 2026-08-17 (сетевые ответы TeleGraphyx имеют серверную дату 2026-08-16).  
Режим: read-only. Оплата, обход paywall, авторизация, изменение настроек и изменяющие POST/WS-команды не выполнялись.

## Короткий результат

```text
LANDING_WORKS                         = PROVEN
PAYWALL_FOUND                         = PROVEN (`https://telegraphyx.ru/nopay`)
VISITOR_ID_CREATED                    = PROVEN by public connector code (`clientId`, `tgphxID` response fields)
UNIQUE_VISITOR_ID                     = NOT_PROVEN (runtime values not exposed safely)
YCLID_CAPTURED                        = PROVEN by connector code; runtime server receipt NOT_PROVEN
YCLID_TO_VISITOR_BINDING              = STRONG_INFERENCE (same visit request carries both fields)
UNIQUE_TRACKING_TOKEN                 = NOT_PROVEN (no runtime token values published)
MAX_URL_FOUND                         = PROVEN as configured fallback `https://max.ru/channel_365msk`
NATIVE_MAX_INVITE_FOUND               = NOT_PROVEN / not found
UNIQUE_NATIVE_INVITE_PER_VISITOR      = NOT_PROVEN
INVITE_CREATED_BEFORE_PAYWALL          = NOT_PROVEN; no invite endpoint or invite token observed
PAYWALL_CHECK_LOCATION                = UNKNOWN exact endpoint; STRONG_INFERENCE backend/resolver path
CAN_INFER_TELEGRAPHYX_ATTRIBUTION_MECHANISM = YES, partially: client/visitor mapping before redirect is evidenced
CONFIDENCE                            = medium for connector pipeline; low for exact backend matching and MAX membership
```

Главный вывод: это не прямой HTTP redirect на `max.ru/join/<token>`. Landing отдаёт HTML `200`, подключает `landing-connector`, отправляет визит на backend, получает внутренний `clientId`, а затем браузерной навигацией вызывает resolver `/api/landing/redirect`. В открытых материалах этой страницы нет доказательства native per-visitor MAX invite или связи с конкретным MAX `user_id`.

## Метод и границы

- Выполнены пять независимых HTTP-сессий A–E с отдельными свежими cookie jar. Значения cookies не выводились.
- Выполнены пять независимых браузерных заходов C–E плюс два базовых/marker-захода. Каждый дошёл до страницы `nopay`.
- Запрошены тестовые URL с `yclid=TGX_TEST_A/B` и UTM. Эти маркеры не отправлялись как conversions.
- Сырые cookies, auth-данные, персональные идентификаторы и потенциальные токены в отчёт не включены.
- Прямое чтение browser localStorage/sessionStorage не выполнялось: это не требовалось для доказательства connector-кода и исключено политикой безопасного browser inspection. В HTML/JS поиск storage-вызовов выполнен.

## 1. Пять независимых HTTP-сессий

Временная отметка ответов: `Sun, 16 Aug 2026 21:37:02 GMT` по заголовку сервера.

| SESSION_ID | INITIAL_URL | HTTP_REDIRECTS | FINAL_URL | FINAL_STATUS |
|---|---|---:|---|---:|
| A | `https://max.tgryx.ru/77459027312767` | 0 | та же URL | 200 |
| B | `https://max.tgryx.ru/77459027312767` | 0 | та же URL | 200 |
| C | `https://max.tgryx.ru/77459027312767` | 0 | та же URL | 200 |
| D | `https://max.tgryx.ru/77459027312767` | 0 | та же URL | 200 |
| E | `https://max.tgryx.ru/77459027312767` | 0 | та же URL | 200 |

Все пять ответов имели `content-length: 23431`, `content-type: text/html; charset=UTF-8`, `x-cache-status: HIT`, одинаковый SHA-256 тела. HTTP `Location` не было. Следовательно, переход далее выполняется JavaScript-кодом, а не 301/302/307/308.

### Сравнение visitor/session признаков

| Признак | Наблюдение | Статус |
|---|---|---|
| URL/path per session | одинаковый `/77459027312767` | UNIQUE_URL_PER_SESSION = NO (PROVEN) |
| query в исходном HTML | marker-значения не отражены в HTML body | PROVEN |
| response cookies | `Set-Cookie` отсутствует во всех пяти HTML-ответах | PROVEN |
| cookie jar после только HTTP GET | пустой | PROVEN for HTTP-only fetch |
| visitor/client runtime values | не выведены; создаются JS/backend-цепочкой | NOT_PROVEN |
| unique visitor ID | collision test по значениям невозможен без выдачи безопасного runtime response | NOT_PROVEN |
| unique tracking token | не найден в URL/HTML | NOT_PROVEN |

## 2. HTML и публичный connector

HTML подключает:

```text
GET https://max.tgryx.ru/landing-connector
  ?tgChannelID=-77459027312767
  &yaCounterID=111341722
  &redirectToChannel=true
  &maxUsername=channel_365msk
```

В HTML также есть fallback-ссылка:

```text
https://max.ru/channel_365msk
```

Конфигурация connector содержит `platform: "max"`, `tgChannelID: "-77459027312767"`, `maxUsername: "channel_365msk"`, `redirectToChannel: true`, `inviteLink: ""`.

### Доказанная логика connector

1. Читает cookie `tgphxID` (если есть).
2. Читает из URL `yclid`.
3. Читает `utm_source`, `utm_medium`, `utm_campaign`, `utm_term`, `utm_content`.
4. Читает `_ym_isad`, `yabs-sid`, `_ym_uid` или Yandex ClientID.
5. Отправляет визит на:

```text
POST https://max.tgryx.ru/api/landing/visit
```

   В query собираются `visitEpoch`, `yaCounterID`, `tgphxID` (если уже есть), `tgChannelID`, `yaClientID`, `yclid`, `isad`, `sid`, UTM и другие присутствующие параметры.

6. Ответ разбирается как JSON. Если присутствуют поля `clientId` и `tgphxID`, connector сохраняет их в runtime; `tgphxID` дополнительно записывается в cookie на 30 дней.
7. После callback и задержки 500 ms выполняется:

```text
GET https://max.tgryx.ru/api/landing/redirect
  ?tgChannelID=<configured channel id>
  &clientId=<response clientId>
```

8. Перед браузерной навигацией connector вызывает цель Яндекс.Метрики `toChannel` с callback; при недоступной Метрике навигация выполняется напрямую.

Важно: `clientId` и `tgphxID` — имена полей из публичного connector-кода. Их конкретные runtime-значения в отчёт не выводились.

## 3. Фактический browser E2E до paywall

Пять свежих браузерных заходов дали одинаковый результат:

```text
https://max.tgryx.ru/77459027312767
  → JavaScript landing connector
  → browser navigation/resolver path
  → https://telegraphyx.ru/nopay
```

На конечной странице виден текст:

```text
Ой. TeleGraphyx не оплачен
Поэтому вы попали сюда
```

Страница `nopay` ответила `200`. В наблюдаемом browser network после перехода присутствуют ресурсы страницы `telegraphyx.ru/nopay` и её Yandex.Metrika; это не является доказательством conversion `toChannel` или подписки.

Точная промежуточная HTTP-ответная строка `/api/landing/redirect` не извлечена из browser event stream: навигация к `telegraphyx.ru/nopay` происходит очень быстро и scope сетевого наблюдения переключается на конечный origin. Поэтому тип и статус ответа resolver не объявляются доказанными.

## 4. Network/XHR/fetch inventory

| Этап | METHOD | URL | Параметры/структура | Статус |
|---|---|---|---|---|
| HTML | GET | `/77459027312767` | path landing ID | PROVEN, 200 |
| Connector | GET | `/landing-connector?...` | channel ID, Yandex counter, redirect flag, MAX username | PROVEN, 200 |
| Visit | POST | `/api/landing/visit?...` | visit time, channel, Yandex IDs, `yclid`, UTM, existing tracking cookie | PROVEN by loaded public JS; runtime response not captured |
| Resolver | GET | `/api/landing/redirect?...` | channel ID + returned `clientId` | PROVEN by loaded public JS; response status/Location NOT_PROVEN |
| Yandex goal | JS API | `ym(..., 'reachGoal', 'toChannel', ...)` | goal name `toChannel` | PROVEN in code; actual call not independently isolated |
| Paywall page | GET | `https://telegraphyx.ru/nopay` | final browser document | PROVEN, 200 |

В connector-коде нет вызовов `max.ru/join/`, `createInvite`, `invite`, `join` или API выдачи native invite. Есть только fallback `max.ru/channel_365msk` и resolver URL.

## 5. Paywall decision

```text
PAYWALL_CHECK_LOCATION = UNKNOWN exact component;
                       STRONG_INFERENCE: backend/resolver or upstream landing middleware
PAYWALL_REQUEST        = exact request not captured safely
PAYWALL_RESPONSE       = final document GET https://telegraphyx.ru/nopay (200)
```

Frontend connector не содержит проверки тарифа и не сравнивает `paid/active/tariff`. Он выполняет visit, затем resolver. Поэтому решение «не оплачено» не принимается видимой frontend-функцией; наиболее вероятно, что его принимает backend/resolver либо серверная прослойка при обработке landing/resolver. Нельзя утверждать, что invite создан до paywall: ни invite endpoint, ни invite token не обнаружены.

## 6. Поиск MAX URL и native invite

Найдено в HTML/JS:

```text
MAX_URL_FOUND = YES
MAX_URL = https://max.ru/channel_365msk (fallback/configured channel URL)
```

Не найдено:

```text
max.ru/join/<token>
native invite token
per-visitor invite endpoint
invite/source field in a join event
```

`max.ru/channel_365msk` — публичная username-ссылка канала, не доказательство native invite и не доказательство per-visitor attribution.

## 7. Yandex marker tests

Проверены:

```text
https://max.tgryx.ru/77459027312767?yclid=TGX_TEST_A&utm_source=yandex&utm_campaign=A
https://max.tgryx.ru/77459027312767?yclid=TGX_TEST_B&utm_source=yandex&utm_campaign=B
```

Оба получили тот же HTML `200`, длину 23431 и тот же hash, что и базовый landing. Маркеры не появились в статическом HTML. Это ожидаемо: connector читает их из `window.location.search` уже на клиенте и добавляет в `/api/landing/visit`.

```text
YCLID_CAPTURED = PROVEN in connector code
YCLID_IN_STATIC_HTML = NO
YCLID_RUNTIME_POST_RECEIPT = NOT_PROVEN
YCLID_TO_VISITOR_BINDING = STRONG_INFERENCE, not event-proven
```

Причина `STRONG_INFERENCE`: в одном и том же кодовом пути `yclid`, UTM и полученный/существующий tracking ID собираются в параметры одного visit request; но ответ backend и записи его базы без owner-controlled кабинета не видны.

## 8. Восстановленный pipeline со статусами

```text
landing URL /77459027312767
  → static HTML 200
  → landing-connector config with channel ID and MAX username
  → read yclid/UTM/Yandex identifiers
  → POST /api/landing/visit
  → response clientId/tgphxID
  → GET /api/landing/redirect?tgChannelID&clientId
  → final unpaid page https://telegraphyx.ru/nopay
```

| Звено | Статус | Обоснование |
|---|---|---|
| Landing URL отдаёт HTML | PROVEN | пять HTTP-сессий, 200 |
| Visitor/tracking fields существуют | PROVEN | публичный connector содержит `tgphxID`, `clientId`, `visitEpoch` |
| yclid/UTM читаются | PROVEN | публичный connector |
| yclid/UTM отправляются в visit request | PROVEN by code | параметры добавляются в URL POST |
| backend создаёт/возвращает clientId | PROVEN by code contract; runtime value NOT_PROVEN | response parser ожидает `clientId` |
| clientId связывается с channel ID | PROVEN by code | resolver передаёт оба поля |
| resolver выполняется перед финальным переходом | STRONG_INFERENCE | connector задаёт `window.location.href` на resolver |
| tariff check | STRONG_INFERENCE backend; exact location UNKNOWN | frontend проверки нет, финал nopay |
| native MAX invite generated | NOT_PROVEN | нет endpoint/token |
| MAX user_id obtained | NOT_PROVEN | нет bot/startapp/user event |
| join/member event | NOT_PROVEN | не было тестового вступления и owner API |
| unsubscribe tracking | NOT_PROVEN | paywall не позволяет пройти подписочный путь |
| Yandex userDidSubscribe | NOT_PROVEN | конверсия подписки не наблюдалась |

## Что доказано

- Landing доступен и стабильно отдаёт HTML `200`.
- Статический HTTP redirect отсутствует.
- Автоматический runtime-путь запускает connector и приводит браузер к `telegraphyx.ru/nopay`.
- Connector читает `yclid`, UTM и Yandex identifiers.
- Connector отправляет visit request с этими параметрами согласно публичному JS.
- Connector ожидает `clientId`/`tgphxID` и передаёт `clientId` в channel resolver.
- Публичный маршрут этой страницы не показывает native invite `max.ru/join/...`.

## Что не доказано

- конкретные значения visitor ID, `clientId` или `tgphxID`;
- уникальность этих значений между пятью посетителями;
- фактическая запись `yclid → clientId` на backend;
- точный HTTP status/response resolver;
- момент и место тарифной проверки внутри backend;
- выдача MAX user_id, bot startapp, invite token или membership event;
- подтверждённая подписка/отписка и conversion в Яндекс.

## Что требует оплаченного/owner-controlled аккаунта

- увидеть dashboard/source rows и subscription records;
- провести контролируемое вступление двумя собственными MAX-аккаунтами;
- сопоставить `clientId`/visitor с конкретным MAX user_id и member event;
- проверить отписку и фактический `userDidSubscribe` payload;
- установить точную backend-точку paywall и resolver response.

## Можно ли использовать механизм в MAXIBOT

Только частично и без утверждения, что это копия TeleGraphyx: доказан общий паттерн `yclid/UTM → opaque client/visitor mapping → resolver`. Для MAXIBOT это подтверждает полезность server-side click record и opaque tracking payload, но не доказывает прямую native invite-атрибуцию и не отменяет требование получить подтверждённый MAX `user_id` и membership перед `userDidSubscribe`.

## Итоговый handoff

- Изменён файл: `research/TELEGRAPHYX_77459027312767_BLACKBOX.md`.
- Миграции: нет.
- Env-переменные: нет.
- Изменения production: нет.
- Тесты: пять fresh HTTP sessions; пять fresh browser visits; два yclid/UTM marker requests; статический анализ landing HTML и connector JS; browser final-state check.
- VPS/E2E MAXIBOT: не применялся, поскольку ТЗ ограничено исследованием TeleGraphyx.
- PASS: landing 200; отсутствие HTTP 30x; final unpaid page; connector pipeline; yclid/UTM capture logic; отсутствие найденного native invite.
- BLOCKED: runtime response values, exact paywall endpoint, native invite uniqueness, MAX user_id/member/join, unsubscribe and Yandex subscription conversion.

## Runtime follow-up: фактические `/visit` и `/redirect`

Дата follow-up: 2026-08-17. Для A и B использованы разные маркеры:

```text
yclid=TGX_RUNTIME_A
yclid=TGX_RUNTIME_B
```

### `/api/landing/visit`

CDP Fetch перехватил фактический запрос:

```text
METHOD = POST
URL    = https://max.tgryx.ru/api/landing/visit?...&yclid=TGX_RUNTIME_A/B&...
STATUS = 200
```

Значения query `tgphxID`, `yaClientID` и response IDs в открытом виде не публикуются. Тело ответа после base64-декодирования имеет JSON-структуру:

```json
{"clientId":"<masked>","tgphxID":"<masked>"}
```

Санитизированное сравнение:

```text
CLIENT_ID_A_HASH  = 92d0bae1ccdee641
CLIENT_ID_B_HASH  = 92d0bae1ccdee641
TGPHX_ID_A_HASH   = 5cd4278f781aa271
TGPHX_ID_B_HASH   = 5cd4278f781aa271

CLIENT_IDS_DIFFER = NO in this browser run
TGPHX_IDS_DIFFER  = NO in this browser run
```

Ограничение: доступный browser API создаёт новые вкладки в одном persistent browser profile; A и B разделили уже существующий `tgphxID` cookie. Поэтому результат `NO` доказывает одинаковость в этом общем profile, но не доказывает отсутствие уникальности между двумя действительно изолированными browser profiles. Для строгого cross-profile collision test требуется incognito/context API или два независимых браузерных профиля.

### `/api/landing/redirect`

После продолжения перехваченного `/visit` CDP зафиксировал до финальной навигации:

```text
METHOD       = GET
REQUEST      = https://max.tgryx.ru/api/landing/redirect?tgChannelID=<masked>&clientId=<masked>
NAVIGATION   = scriptInitiated
STATUS       = 302
FINAL TARGET = https://telegraphyx.ru/nopay
```

Событие `Page.frameRequestedNavigation` подтвердило URL resolver до перехода на `nopay`. Browser security API не разрешил получить тело/заголовки Document-stage response через `Fetch.getResponseBody`; `Location` header отдельно не извлечён. Поэтому response capture доказан по status и navigation event, но не по полному header/body.

```text
REDIRECT_RESPONSE_CAPTURED         = YES (status + request + navigation target; headers/body partial)
REDIRECT_A_EQUALS_B                = NOT_PROVEN (A/B redirect response headers/body unavailable)
REDIRECT_CONTAINS_NATIVE_MAX_URL   = NO observed
REDIRECT_CONTAINS_INVITE_METADATA  = NO observed
```

### Updated final statuses

```text
UNIQUE_CLIENT_ID                    = NO in shared browser profile; fresh-profile uniqueness NOT_PROVEN
UNIQUE_TGPHX_ID                     = NO in shared browser profile; fresh-profile uniqueness NOT_PROVEN
YCLID_TO_CLIENT_BINDING             = PROVEN for runtime request path; backend persistence semantics NOT_PROVEN
REDIRECT_RESPONSE_CAPTURED          = YES, partial
REDIRECT_A_EQUALS_B                 = NOT_PROVEN
REDIRECT_CONTAINS_NATIVE_MAX_URL    = NO
REDIRECT_CONTAINS_INVITE_METADATA   = NO
NEXT_UNKNOWN                        = isolated browser-profile collision test; redirect Location/body; backend mapping clientId→yclid and tariff decision internals
```

## Final identity test attempt: isolated Chrome profiles

Дата: 2026-08-17. Подняты три отдельных headless Chrome-профиля с разными `user-data-dir` и независимыми CDP-портами. Вкладки одного профиля между собой не использовались.

В каждом профиле request-stage перехватил отдельный:

```text
POST /api/landing/visit?yclid=TGX_PROFILE_A/B/C...
```

Запросы содержали profile-local `yaClientID`; cookies и значения идентификаторов не публиковались. Также в A был запущен повторный заход с `TGX_PROFILE_A_SECOND`.

Итоговый response-stage test заблокирован техническим ограничением внешнего Chrome CDP runner: после продолжения XHR request response envelope нестабилен и не отдаёт безопасно извлекаемое `result.body` во всех трёх профилях. Поэтому следующие поля не объявляются доказанными:

```text
CLIENT_A_HASH = NOT_CAPTURED
CLIENT_B_HASH = NOT_CAPTURED
CLIENT_C_HASH = NOT_CAPTURED
TGPHX_A_HASH  = NOT_CAPTURED
TGPHX_B_HASH  = NOT_CAPTURED
TGPHX_C_HASH  = NOT_CAPTURED

CLIENT_IDS_UNIQUE_ACROSS_PROFILES = NOT_PROVEN
TGPHX_IDS_UNIQUE_ACROSS_PROFILES  = NOT_PROVEN
CLIENT_ID_PERSISTS_WITHIN_PROFILE = NOT_PROVEN
TGPHX_ID_PERSISTS_WITHIN_PROFILE  = NOT_PROVEN
```

Resolver request-stage был виден в предыдущем persistent-browser capture, но в этом isolated-profile прогоне response body/hash для `/redirect` также не получен. Поэтому cross-profile identity model остаётся `NOT_PROVEN`, а `READY_FOR_MAXIBOT_POC` по этому ТЗ = `NO`.
