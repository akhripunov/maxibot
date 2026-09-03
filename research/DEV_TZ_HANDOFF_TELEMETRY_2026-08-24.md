# ТЗ для Dev: безопасная telemetry Landing → MAX → Mini App

Дата: 2026-08-24

## Цель

Для каждого нового визита из tracking-пути определить последний подтверждённый этап между выдачей MAX deep link и запуском Mini App:

- потеря до подтверждённого запуска Mini App;
- ошибка получения или проверки MAX initData;
- ошибка разрешения startapp;
- ошибка bind;
- ошибка запроса открытия канала.

Telemetry должна локализовать последний подтверждённый этап, но не обязана доказывать, что ОС/MAX фактически отрисовали канал или что пользователь нажал «Подать заявку».

Текущий baseline кампании 713798021: 19 уникальных tracking-визитов, 19 startapp, 11 Mini App/identity/channel-open, 8 случаев без подтверждения Mini App. Исторические 8 случаев не переобъяснять задним числом.

## Жёсткие ограничения

Не менять бизнес-путь:

```text
landing → visit → startapp → MAX → Mini App → bind → channel
```

Не менять redirect/deep-link, visit/startapp, attribution/exact-gate, `user_added`, `userDidSubscribe`, Yandex sender, Mini App channel handoff и iPhone/Android handoff contract.

Telemetry не должна создавать новый visit/token, блокировать или задерживать redirect, требовать клика, открывать fallback-ветку, менять attribution/membership или создавать/отправлять Yandex conversion.

## Безопасность данных

Raw yclid, startapp token, MAX initData, MAX user_id, cookies, полный User-Agent и полный Referer не сохранять и не выводить.

Разрешённые низкокардинальные поля:

```text
device_family = IOS_MOBILE | ANDROID_MOBILE | WINDOWS_DESKTOP | MAC_DESKTOP | LINUX_DESKTOP | UNKNOWN
browser_family = SAFARI | CHROME | YANDEX | FIREFOX | OTHER | UNKNOWN
referrer_host = yandex.ru | max.ru | other host | null
target_scheme = max | https
target_host = max.ru | web.max.ru | other allowed host
target_path = allowlisted bot path only, без query и token
```

Для корреляции использовать существующий server-side `visit_id` и односторонние hash startapp/yclid. Hash не является новой идентичностью и не используется для attribution-решений.

## 1. Landing telemetry

Добавить для существующего visit/startapp идемпотентные best-effort события:

```text
LANDING_RENDERED
MAX_REDIRECT_ATTEMPTED
JS_LANDING_STARTED
PAGE_HIDDEN
PAGE_VISIBLE_AGAIN
PAGEHIDE
BEFOREUNLOAD
FOCUS_LOST
FOCUS_RETURNED
WEB_FALLBACK_SHOWN
WEB_FALLBACK_CLICKED
```

`PAGE_HIDDEN`, `PAGEHIDE`, `BEFOREUNLOAD`, `FOCUS_LOST` могут не отправиться. Их отсутствие не является доказательством обратного.

Использовать `navigator.sendBeacon` или `fetch(..., {keepalive:true})`; timeout telemetry-запроса не более 1 секунды. Telemetry не должна повторно запускать navigation.

Для `MAX_REDIRECT_ATTEMPTED` сохранять только:

```text
redirect_type
target_scheme
target_host
target_path
has_startapp
```

## 2. Mini App telemetry

События Mini App отправлять через существующий authenticated endpoint после проверки MAX initData, если это возможно:

```text
MINIAPP_JS_STARTED
MAX_WEBAPP_OBJECT_AVAILABLE
INIT_DATA_PRESENT
INIT_DATA_VALID
START_PARAM_PRESENT
START_PARAM_RESOLVED
IDENTITY_RECEIVED
BIND_REQUEST_SENT
BIND_SUCCESS
BIND_FAILED
CHANNEL_OPEN_REQUESTED
CHANNEL_OPEN_ACCEPTED_BY_BACKEND
CHANNEL_OPEN_FAILED
```

До получения initData допустимы только client-side best-effort события с пометкой `UNTRUSTED_CLIENT_TELEMETRY`. Они не могут менять visit, identity, attribution или conversion queue.

`CHANNEL_OPEN_ACCEPTED_BY_BACKEND` означает только успешный ответ MAXIBOT с валидной target-ссылкой. Это не означает, что MAX отрисовал канал или что пользователь нажал вступление.

## 3. Ошибки

Добавить best-effort обработку `window.onerror`, `unhandledrejection`, fetch failure, timeout и MAX bridge/API exception.

Сохранять только:

```text
error_class
short_message — не более 200 символов, с очисткой URL/token/initData
stage
server_timestamp
client_timestamp
```

Ошибки telemetry endpoint не должны порождать новые ошибки или повторные telemetry-запросы.

## 4. Временные отметки

Для каждого этапа сохранять server timestamp. Client timestamp — дополнительный сигнал.

Производные интервалы считать только при сопоставимых отметках:

```text
landing_to_redirect_ms
redirect_to_page_hidden_ms
redirect_to_miniapp_ms — approximate для разных WebView/page
miniapp_to_identity_ms
identity_to_bind_ms
bind_to_channel_request_ms
```

Не заявлять точность timing при переходе между Safari, MAX и Mini App.

## 5. Корреляция и идемпотентность

Каждое событие должно иметь:

```text
visit_id — если уже разрешён на сервере
event_type
event_time
dedupe_key = visit_id + event_type
source = landing | miniapp | server
trusted = true | false
```

Дубли не создавать. Добавить rate limit по visit/event и bounded retention по текущей политике хранения telemetry.

## 6. Классификация

Для новых визитов вычислять `final_status` по последнему подтверждённому этапу:

```text
REDIRECT_NOT_ATTEMPTED
REDIRECT_ATTEMPTED_PAGE_STAYED_VISIBLE
REDIRECT_ATTEMPTED_PAGE_HIDDEN_NO_MINIAPP_CONFIRMED
PAGE_HIDDEN_THEN_RETURNED
MINIAPP_STARTED_NO_INIT_DATA
INIT_DATA_PRESENT_INVALID
START_PARAM_MISSING
START_PARAM_UNRESOLVED
IDENTITY_FAILED
BIND_FAILED
CHANNEL_OPEN_REQUEST_FAILED
CHANNEL_OPEN_ACCEPTED_BY_BACKEND
COMPLETE_TO_CHANNEL_REQUEST
UNKNOWN_NO_TELEMETRY
```

`UNKNOWN_NO_TELEMETRY` оставлять, если после доступных сигналов нельзя надёжно определить следующий этап. Не превращать `PAGE_HIDDEN` автоматически в доказанный `HANDOFF_FAILED`.

## 7. Owner-scoped отчёт

Добавить read-only aggregate report без raw данных:

- количество визитов на каждом этапе;
- количество и доля каждого `final_status`;
- device_family/browser_family;
- trusted/untrusted события;
- средние и p50/p95 timing там, где они валидны;
- ошибки по error_class и stage;
- отдельная сверка raw requests, unique yclid и повторных yclid.

Raw requests и unique yclid не называть количеством кликов Директа без отдельной сверки с интерфейсом Директа.

## 8. Тесты до deploy

Обязательные проверки:

- attribution logic unchanged;
- `userDidSubscribe` sender unchanged;
- no conversion from telemetry;
- no new visit/token from telemetry;
- dedupe, rate limit и timeout работают;
- raw token/yclid/initData/user_id отсутствуют в логах;
- target scheme/path sanitization работает;
- existing mobile handoff contract tests проходят;
- Mini App business flow tests проходят.

До deploy сохранить rollback.

## 9. Проверка после deploy

На небольшом controlled traffic sample подтвердить:

```text
LANDING_EVENT_CHAIN_VISIBLE = YES
MINIAPP_EVENT_CHAIN_VISIBLE = YES
VISIT_TO_STARTAPP_CORRELATION = YES
STARTAPP_TO_MINIAPP_CORRELATION = YES, когда Mini App реально загрузился
CLIENT_ERRORS_CAPTURED = YES, если ошибка возникла
VALID_TIMINGS_AVAILABLE = YES для сопоставимых этапов
NO_ATTRIBUTION_SIDE_EFFECTS = YES
NO_YANDEX_SEND_SIDE_EFFECTS = YES
```

Отдельно проверить свежие iPhone и Android пути. Если меняются `app/main.py`, `app/traffic/deep_link.py` или `miniapp/traffic.html` и затрагивается handoff, применить mobile contract и real-device smoke из AGENTS.md. Если меняется только telemetry и handoff byte-for-byte не меняется, зафиксировать это в handoff.

## 10. Handoff Dev обязан вернуть

- изменённые файлы;
- отсутствие миграций или список миграций;
- env changes: обычно none;
- тесты и точный результат;
- rollback path;
- production health;
- API polling=false;
- единственный worker polling=true;
- controlled sample с aggregate-результатами;
- доказательство, что attribution/Yandex не изменились;
- список `UNKNOWN_NO_TELEMETRY` и причины, почему их нельзя классифицировать.
