# Публичные кейсы атрибуции рекламного посетителя в подписчика MAX

Дата проверки: 2026-08-13.

## Короткий ответ

Да, публичные сервисы заявляют, что умеют считать подписки в MAX. Но найденный технически описанный способ — **не прямая native invite-ссылка в канал**. Это цепочка:

```text
реклама / visitor_id / click_id
  → диплинк MAX
  → бот MAX со служебным параметром
  → бот получает MAX user_id
  → кнопка/ссылка в канал
  → бот получает событие подписки или проверяет членство
  → visitor связывается с user_id и конверсией
```

То есть задача решается через контролируемый ботом идентификатор пользователя, а не через `max.ru/join/...` и не через источник native invite.

## Самый сильный публичный источник: Mobzio

Mobzio прямо пишет, что обычная ссылка сразу на канал не позволяет увидеть, кто подписался после конкретного клика, а рабочий сценарий для MAX у них строится как «переход → бот → подписка». [Страница аналитики подписок MAX](https://mobz.io/max-podpiski-analytics)

В инструкции интеграции указано:

1. для каждого рекламного источника создаётся MAX-д диплинк;
2. к диплинку подключается MAX-бот и его API token;
3. бот добавляется администратором в целевой канал;
4. через `/start` и служебные параметры сохраняется связь клика с дальнейшими событиями;
5. в отчёте считается подписка, а не только переход.

Mobzio отдельно предупреждает, что вести рекламу прямо на канал недостаточно для той же прозрачности конверсии. [Инструкция интеграции MAX](https://mobz.io/help/max-integration)

Это практически совпадает с нашей гипотезой после black-box TeleGraphyx: идентификатор фиксируется на входе в бота, где доступен MAX user ID, а канал является последующим шагом.

Важно: это публичное описание продукта, а не опубликованный исходный код backend. Оно доказывает заявленную архитектуру и практический паттерн, но не раскрывает точные внутренние таблицы или endpoint Mobzio.

## TeleGraphyx

TeleGraphyx публично заявляет для MAX «подписки, отписки, источники», ежедневную аналитику кампаний и передачу подписок как конверсий. [Публичная страница TeleGraphyx Traffic](https://telegraphyx.ru/traffic/analitika-max-kanala/)

Однако публичный black-box маршрут TeleGraphyx, который мы уже проверили, ведёт посетителя в MAX-бота через `startapp`, а не в отдельный native invite канала. В публичной странице нет технической схемы `visitor_id → max_user_id → subscription`, поэтому точные внутренние поля TeleGraphyx не доказаны.

Наиболее вероятная схема — та же, что описывает Mobzio:

```text
visitor/campaign
  → bot startapp
  → bot_started с user_id
  → переход пользователя в канал
  → user_added/member check
  → conversion для campaign
```

Это inference по публичному маршруту и API-модели, а не утверждение о внутренней реализации TeleGraphyx.

## Что нашли на GitHub

Публичные GitHub-клиенты MAX в основном являются обёртками официального Bot API: чаты, участники, webhook/long polling, сообщения. В опубликованном списке методов Python-клиента нет `createInvite`, `getInvites`, `invite_id`, `createChatInviteLink` или отдельного API источника invite. [Публичный MAX Bot API client](https://github.com/green-api/maxbot-api-client-python)

В issue публичного TypeScript-клиента разработчики отдельно обсуждают, что события канала приходят боту, когда бот добавлен в канал; это согласуется с требованием использовать бота как наблюдаемую точку. [Issue о webhook и событиях канала](https://github.com/max-messenger/max-bot-api-client-ts/issues/237)

Исходников Mobzio/TeleGraphyx с реализацией их attribution backend в открытом доступе не найдено.

## Почему Telegram устроен проще

Telegram имеет именно те server-side primitives, которых нет в публичном MAX API:

| Задача | Telegram |
|---|---|
| Создать дополнительную ссылку | `createChatInviteLink` |
| Объект ссылки | `ChatInviteLink` |
| Отличить primary/additional | `is_primary`, `is_revoked` |
| Счётчик заявок по ссылке | `pending_join_request_count` |
| Увидеть ссылку в заявке | `ChatJoinRequest.invite_link` |
| Увидеть ссылку при вступлении | `ChatMemberUpdated.invite_link` |
| Отозвать конкретную ссылку | `revokeChatInviteLink` |
| Одобрить заявку | `approveChatJoinRequest` |

Официальная документация Telegram прямо говорит, что `createChatInviteLink` создаёт **дополнительную** ссылку, а объект `ChatInviteLink` содержит статус, creator, expiry, лимит и число pending requests. [Telegram Bot API: invite links](https://core.telegram.org/bots/api#createchatinvitelink)

Документация также показывает `invite_link` внутри `ChatJoinRequest` и `ChatMemberUpdated`, что даёт прямую per-invite атрибуцию. [Telegram Bot API: ChatJoinRequest](https://core.telegram.org/bots/api#chatjoinrequest), [ChatMemberUpdated](https://core.telegram.org/bots/api#chatmemberupdated)

## Что это означает для MAXIBOT

Есть два разных уровня результата:

### 1. Конкретный рекламный visitor стал подписчиком MAX

**Да, вероятно и практически реализуемо через бота.**

MAXIBOT может:

```text
visitor_id / yclid
  → уникальный signed startapp в MAX-боте
  → verified bot_started: max_user_id
  → user_added/member check в целевом канале
```

Если один и тот же `max_user_id` подтверждён ботом и событием канала, связка visitor → MAX user → membership является сильной и контролируемой. Это уже реализовано в локальном PoC как отдельный bot/startapp маршрут.

### 2. Конкретный visitor стал подписчиком через прямой native invite

**Публичного доказанного способа нет.**

Native invite даёт URL/токен перехода, но в доступном MAX Web/API:

- нет доказанного пула дополнительных invite;
- operation 55 только ротирует primary link;
- pending request не содержит invite/source ID;
- `user_added` и `GET /members` не раскрывают источник ссылки;
- публичные сервисы, которые заявляют атрибуцию, используют бота как промежуточную точку.

## Практический вывод

Наше исследование было направлено на самый строгий и сложный вариант — **прямой redirect в закрытый native MAX-канал без бота**. Для него ответ пока отрицательный.

Но более общий бизнес-вопрос имеет положительный ответ:

```text
рекламный visitor → конкретный подписчик MAX
```

решается через bot deep link/startapp и подтверждение membership. Именно это публично описывают Mobzio и, по маркетинговой части, TeleGraphyx.

Разница с Telegram: Telegram позволяет получить exact attribution, сохраняя native invite как объект. MAX-публичный стек заставляет перенести точку идентификации на бота.

