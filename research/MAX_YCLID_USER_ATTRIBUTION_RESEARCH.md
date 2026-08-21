# YCLID ↔ MAX user_id: доказательное исследование

Дата актуализации: 2026-08-16  
Режим: research-only; код MAXIBOT, production funnel и настройки конверсий не изменялись.

## Цель и шкала доказательности

Проверяется не маркетинговое заявление, а наблюдаемая цепочка:

```text
yclid → visitor/tracking token → MAX startapp → MAX user_id → membership → Yandex conversion
```

Статусы:

- **PROVEN** — наблюдалось в контролируемом тесте или подтверждено фактическим событием/полем.
- **STRONG_INFERENCE** — следует из нескольких наблюдений, но критическое поле не наблюдалось напрямую.
- **POSSIBLE** — технически совместимо с наблюдением, но есть альтернативные объяснения.
- **NOT_PROVEN** — доказательств недостаточно.

Секреты, auth cookies, access tokens, raw frames и персональные данные в отчёт не включались.

## Ограничение текущего прогона

Публичные кабинеты, собственные кабинеты сервисов, два контролируемых MAX-пользователя и API event payload сервисов не были доступны. Поэтому требования ТЗ по пяти независимым сессиям, collision test, реальному join/member event и проверке Yandex payload не могут быть честно отмечены как выполненные.

Это отдельный результат: без owner-controlled кабинета/канала и доступа к событиям нельзя доказать `yclid ↔ MAX user_id`, даже если redirect визуально работает.

## A/B. Публичные наблюдения

### TeleGraphyx

Публичная страница: [telegraphyx.ru](https://telegraphyx.ru), раздел Traffic.

Наблюдаемое/заявленное:

- сервис публично заявляет интеграцию с Яндекс.Директом, лендинги, отчёты по подпискам/отпискам и цели Яндекс;
- ранее в public black-box прогоне наблюдался маршрут вида `landing → https://max.ru/<bot>?startapp=<opaque>`;
- в публичном интерфейсе не наблюдались `yclid`, visitor ID, `MAX user_id`, join event, invite ID или Yandex conversion payload;
- публичное наличие `startapp` доказывает только транспорт opaque-параметра до MAX-бота.

Статусы:

```text
YCLID_CAPTURE                    = NOT_PROVEN
VISITOR_IDENTIFIER               = NOT_PROVEN
OUTBOUND_TRACK_IDENTIFIER        = PROVEN for opaque startapp shape; source semantics NOT_PROVEN
NATIVE_MAX_INVITE                = NOT_PROVEN
UNIQUE_INVITE_PER_VISITOR        = NOT_PROVEN
MULTIPLE_ACTIVE_INVITES          = NOT_PROVEN
MAX_USER_ID_OBSERVED             = NOT_PROVEN
JOIN_EVENT_OBSERVED               = NOT_PROVEN
INVITE_ID_IN_JOIN_EVENT           = NOT_PROVEN
YCLID_TO_MAX_USER_BINDING         = NOT_PROVEN
BINDING_TYPE                      = POSSIBLE bot payload binding; not event-proven
MAX_USER_TO_YANDEX_CONVERSION     = NOT_PROVEN
CONFIDENCE                        = low for exact attribution
```

Маркетинговые заявления нельзя использовать как доказательство конкретного поля matching.

### «Откуда Подписки» / tgTrack

Публичная страница: [tgtrack.ru](https://tgtrack.ru).

На странице прямо заявлены: сохранение параметров клика/UTM, tracking link, переход посетителя сразу в канал, определение источника нового подписчика, отчёты по подпискам/отпискам и offline conversion в Яндекс. Также заявлена схема «невидимый бот».

Это **CLAIMED**, а не PROVEN: публичная страница не показывает поле `MAX user_id`, event payload, invite token в join-событии или алгоритм сопоставления.

Статусы:

```text
YCLID_CAPTURE                    = CLAIMED by public copy; direct field NOT_PROVEN
VISITOR_IDENTIFIER               = NOT_PROVEN
OUTBOUND_TRACK_IDENTIFIER        = CLAIMED; exact token NOT_PROVEN
NATIVE_MAX_INVITE                = NOT_PROVEN for the observed MAX flow
UNIQUE_INVITE_PER_VISITOR        = NOT_PROVEN
MAX_USER_ID_OBSERVED             = NOT_PROVEN
JOIN_EVENT_OBSERVED               = NOT_PROVEN
INVITE_ID_IN_JOIN_EVENT           = NOT_PROVEN
TEMPORAL_MATCHING                 = POSSIBLE, not proven
COLLISION_BEHAVIOR                = NOT_PROVEN
YCLID_TO_MAX_USER_BINDING         = NOT_PROVEN
BINDING_TYPE                      = POSSIBLE redirect/source matching; not event-proven
MAX_USER_TO_YANDEX_CONVERSION     = CLAIMED by public copy; payload NOT_PROVEN
CONFIDENCE                        = medium for claimed architecture, low for exact binding
```

## Controlled black-box observation: mx-t.su / «Откуда Подписки»

В переданном Chrome handoff от 2026-08-16 зафиксирована санитизированная цепочка:

```text
mx-t.su short link
  → branded HTML page: «Петербург 365 вход»
  → MAX bot startapp=<opaque>
  → bot screen: «Переход в канал от Откуда Подписки»
  → Start
  → embedded Mini App: «Пару секунд, переходим в Макс…»
  → automatic opening of target channel «Петербург 365 вход»
```

Что это доказывает:

- `short link → branded intermediate page`: **PROVEN**;
- `branded page → MAX bot startapp with opaque payload`: **PROVEN**;
- `startapp → silent Mini App → automatic target-channel opening`: **PROVEN** for this controlled path;
- промежуточный пользовательский CTA на landing не требуется: **PROVEN** for this path.

Что не доказано:

- `yclid` присутствовал и был сохранён сервисом;
- payload содержит или ссылается на visitor/click ID;
- `bot_started` передал сервису реальный `MAX user_id`;
- target-channel opening связано с тем же `MAX user_id`;
- join/member event содержит source/invite/ref;
- сервис отправил в Яндекс исходный `yclid` или ClientID;
- два разных источника дают два разных и корректно сопоставленных результата.

Итог наблюдения: **PROVEN UX/transport pattern**, но `yclid → MAX user_id` — **NOT_PROVEN**.

## C. Native invite и multiple active invites

В доступных public/controlled материалах не получено доказательств:

```text
UNIQUE_NATIVE_INVITE_PER_VISITOR = NOT_PROVEN
MULTIPLE_ACTIVE_NATIVE_INVITES   = NOT_PROVEN
INVITE_ID_PRESENT_IN_JOIN_EVENT  = NOT_PROVEN
```

Наблюдаемый `startapp` нельзя переименовывать в native `max.ru/join/<TOKEN>`. Это tracking payload бота/мини-приложения, а не доказательство per-visitor invite.

## D–F. MAX user, join event и collision test

Не выполнено из-за отсутствия owner-controlled test setup:

- два независимых MAX-пользователя;
- собственный тестовый канал для каждого сервиса;
- доступ к webhook/API event payload;
- возможность повторить A→B с задержками 5 секунд, 30 секунд, 2 минуты и 5 минут;
- наблюдение `JOIN_REQUEST`, `USER_ADDED`, `MEMBER` с `max_user_id`, `chat_id`, временем и source/ref/invite полями.

Следовательно:

```text
MATCH_BY_ORDER  = NOT_PROVEN
MATCH_BY_TIME   = NOT_PROVEN
MATCH_BY_INVITE = NOT_PROVEN
OTHER_SIGNAL    = NOT_PROVEN
COLLISION_RESULT = NOT_PROVEN
```

Нельзя заменять этот тест предположением, что подписчик был сопоставлен по ближайшему времени.

## G. Yandex conversion

Не наблюдался фактический запрос сервиса в Яндекс с sanitized payload. Поэтому неизвестно, что именно используется:

```text
MAX user_id → internal visitor → yclid/ClientID → Yandex conversion
```

или иной идентификатор.

Статус для обоих сервисов: `MAX_USER_TO_YANDEX_CONVERSION = NOT_PROVEN` по event-level evidence; для tgTrack/TeleGraphyx это только public `CLAIMED`.

## Сравнительная таблица

| Механизм | TeleGraphyx | tgTrack / «Откуда Подписки» |
|---|---|---|
| yclid сохраняется | NOT_PROVEN | CLAIMED / NOT_PROVEN field |
| visitor ID | NOT_PROVEN | NOT_PROVEN |
| opaque redirect/startapp | PROVEN public pattern | PROVEN in mx-t.su controlled path |
| unique native MAX invite | NOT_PROVEN | NOT_PROVEN |
| multiple active invites | NOT_PROVEN | NOT_PROVEN |
| MAX user_id получен | NOT_PROVEN | NOT_PROVEN |
| join/member event | NOT_PROVEN | NOT_PROVEN |
| invite token в join event | NOT_PROVEN | NOT_PROVEN |
| deterministic attribution | NOT_PROVEN | NOT_PROVEN |
| temporal/probabilistic attribution | POSSIBLE | POSSIBLE |
| MAX user_id ↔ visitor доказан | NOT_PROVEN | NOT_PROVEN |
| visitor ↔ yclid доказан | NOT_PROVEN | NOT_PROVEN |
| conversion → Yandex доказана | NOT_PROVEN | CLAIMED / NOT_PROVEN payload |

## Главный результат

### TeleGraphyx

```text
YCLID_CAPTURE                 = NOT_PROVEN
VISITOR_IDENTIFIER            = NOT_PROVEN
OUTBOUND_TRACK_IDENTIFIER     = opaque startapp observed
NATIVE_MAX_INVITE             = NOT_PROVEN
UNIQUE_INVITE_PER_VISITOR     = NOT_PROVEN
MULTIPLE_ACTIVE_INVITES       = NOT_PROVEN
MAX_USER_ID_OBSERVED          = NOT_PROVEN
JOIN_EVENT_OBSERVED            = NOT_PROVEN
INVITE_ID_IN_JOIN_EVENT        = NOT_PROVEN
YCLID_TO_MAX_USER_BINDING      = NOT_PROVEN
BINDING_TYPE                   = POSSIBLE bot-payload binding
MAX_USER_TO_YANDEX_CONVERSION  = NOT_PROVEN
EXACT_CHAIN                    = NOT_PROVEN
CONFIDENCE                     = low for exact attribution
```

### tgTrack / «Откуда Подписки»

```text
YCLID_CAPTURE                 = CLAIMED, direct storage NOT_PROVEN
VISITOR_IDENTIFIER            = NOT_PROVEN
OUTBOUND_TRACK_IDENTIFIER     = opaque startapp PROVEN in mx-t.su path
NATIVE_MAX_INVITE             = NOT_PROVEN
UNIQUE_INVITE_PER_VISITOR     = NOT_PROVEN
MAX_USER_ID_OBSERVED          = NOT_PROVEN
JOIN_EVENT_OBSERVED            = NOT_PROVEN
INVITE_ID_IN_JOIN_EVENT        = NOT_PROVEN
TEMPORAL_MATCHING              = POSSIBLE
COLLISION_BEHAVIOR             = NOT_PROVEN
YCLID_TO_MAX_USER_BINDING      = NOT_PROVEN
BINDING_TYPE                   = POSSIBLE redirect/source algorithm
MAX_USER_TO_YANDEX_CONVERSION  = CLAIMED, payload NOT_PROVEN
EXACT_CHAIN                    = NOT_PROVEN
CONFIDENCE                     = medium for UX pattern, low for exact binding
```

## Рекомендация для MAXIBOT

Лучший доказательно доступный для воспроизведения механизм:

```text
landing(yclid/UTM)
  → opaque click_id
  → MAXIBOT startapp
  → bot_started(MAX user_id, click_id)
  → server-side binding
  → Mini App/target конкретного канала
  → confirmed membership
  → userDidSubscribe
```

Это **STRONG_INFERENCE** как архитектурный паттерн, но не доказательство поведения конкурентов.

```text
CAN_IMPLEMENT_WITH_PUBLIC_MAX_API       = PARTIAL
REQUIRES_UNDOCUMENTED_MAX_RPC           = NOT_REQUIRED for bot_started binding; channel target acquisition must use official GET /chats/{chat_id}
CAN_IMPLEMENT_WITHOUT_USER_FACING_BOT   = NOT_PROVEN / not accepted for exact identity binding
EXPECTED_ATTRIBUTION_ACCURACY            = NOT_PROVEN until own two-user E2E
```

Для MAXIBOT обязательный acceptance test:

```text
visit A: yclid=Y1, click_id=C1
bot_started: user_id=U1, payload=C1
membership: user_id=U1, channel_id=ChannelA

visit B: yclid=Y2, click_id=C2
bot_started: user_id=U2, payload=C2
membership: user_id=U2, channel_id=ChannelA
```

Только при такой независимой связке разрешается `userDidSubscribe`. Direct shared invite не является заменой этому тесту.

## Оставшиеся блокеры

1. Собственный кабинет и тестовый канал TeleGraphyx.
2. Собственный кабинет и тестовый канал tgTrack.
3. Два независимых MAX user identity.
4. Sanitized webhook/API payload с `MAX user_id` и membership event.
5. Повторяемый collision test A/B.
6. Наблюдение фактического Yandex offline conversion payload.

До получения этих данных точный статус обоих сервисов по `yclid ↔ MAX user_id` остаётся `NOT_PROVEN`, а не PASS.
