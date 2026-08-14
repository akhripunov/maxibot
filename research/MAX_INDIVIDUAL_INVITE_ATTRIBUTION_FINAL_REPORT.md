# MAX individual invite attribution — sanitized final handoff

Дата исследования: 2026-08-13  
Область: только read-only black-box research на собственных материалах и публичном TeleGraphyx flow.

## Итог

```text
INDIVIDUAL_INVITE = NOT_PROVEN
TELEGRAPHYX_MECHANISM = PARTIAL
INVITE_A_AND_B_SIMULTANEOUS = NOT_TESTED
INVITE_A_TO_USER_A = NOT_PROVEN
INVITE_B_TO_USER_B = NOT_PROVEN
YCLID_TO_MAX_USER = NOT_PROVEN
ALTERNATIVE_BOT_STARTAPP_MECHANISM = PROVEN
EXACT_ATTRIBUTION_READY = NO
AUTO_APPROVE_TOUCHED = NO
PRODUCTION_CHANGED = NO
```

`ALTERNATIVE_BOT_STARTAPP_MECHANISM = PROVEN` означает только то, что в публичном
TeleGraphyx flow наблюдается переход в MAX-бота с `startapp`. Это не доказывает
связь конкретного MAX user с конкретным рекламным визитом после подписки.

## Наблюдаемые URL и flow

Публичный пример TeleGraphyx:

```text
https://max.tgryx.ru/69092437303297
  -> https://max.ru/id510107251374_bot?startapp=client_38393234313936
  -> max://... или web.max.ru/... в зависимости от клиента
```

В наблюдаемом конечном URL нет `join/<native-invite-token>`, `yclid`,
`visitor_id` или отдельного `click_id`. Два публичных открытия одного landing
не дали двух различных invite: конечный bot/startapp URL был одинаковым.
Значение `startapp` выглядит связанным с landing/object ID, но это только
гипотеза по публичному примеру; доказательства привязки к browser visitor и
последующему MAX user нет.

## A/B evidence

| Сценарий | Что проверено | Результат | Почему этого недостаточно |
|---|---|---|---|
| TeleGraphyx A | Публичный landing A → конечный URL | Bot deep-link со `startapp` | Нет private callback и user identity |
| TeleGraphyx B | Публичный landing B/повторное открытие | Тот же наблюдаемый bot-flow | Нет доказательства отдельного visitor binding |
| MAX Invite A/B | Две одновременно действующие native invite | `NOT_TESTED` | Нет второго собственного тестового аккаунта и безопасного API дополнительных invite |
| MAX Invite A → User A | invite source в заявке/member state | `NOT_PROVEN` | В доступном state нет доказанного invite reference |
| MAX Invite B → User B | invite source в заявке/member state | `NOT_PROVEN` | То же ограничение |
| yclid A/B → MAX user | Сквозная identity-связь | `NOT_PROVEN` | Native invite и membership state не несут доказанный yclid/source |

## Собственный MAX Web evidence

В ранее выполненном read-only исследовании собственного закрытого канала
обнаружено:

- Web transport: `wss://api.oneme.ru/websocket`;
- операция `55` обновляет текущую private invite link с признаком
  `revokePrivateLink: true`;
- после выпуска новой ссылки предыдущая ссылка становится недействительной;
- отдельный pool invite, `invite_id`, `link_id`, usage counter или источник
  заявки не обнаружены;
- pending/member state содержит связь с каналом и MAX user, но не содержит
  доказанного invite ID/source/referrer.

Следовательно, operation 55 — это ротация/отзыв основной ссылки, а не
доказанный `createAdditionalInvite`. Использовать её для A/B нельзя: это может
инвалидировать предыдущую ссылку.

## TeleGraphyx public evidence

Публичный frontend раскрывает аналитические категории вроде `yandex_direct`,
`invite_link` и `unknown`, а также dashboard API для источников и подписок.
Это подтверждает наличие серверной агрегации источников, но не подтверждает
публично точное поле `visitor_id → max_user_id`. В доступном публичном коде не
найдены доказанные поля `visitor_id`, `click_id`, `landing_id`, `max_user_id`
или invite usage mapping.

Приватная авторизованная сессия TeleGraphyx и собственный тестовый проект с
двумя чистыми профилями не предоставлены, поэтому приватный callback/ботовый
binding не наблюдался.

## Вывод для MAXIBOT

Точная attribution без промежуточного identity-binding сейчас не доказана.
Обычная native invite даёт переход/заявку/участника, но в доступных данных нет
надёжного `invite_id → MAX user → yclid`.

Для доказательства нужен один из вариантов:

1. официальный MAX API или подтверждённый read-only RPC, который возвращает
   отдельные одновременно активные invite с `invite_id/link_id` и source в
   join-request/member event;
2. собственный bot/startapp flow, где start payload принимается сервером,
   связывается с visitor/yclid, а затем тот же bot identity подтверждается при
   membership;
3. иной официальный callback с явной связкой рекламного visitor и MAX user.

Минимальный следующий тест для варианта 2: два собственных аккаунта MAX,
два чистых browser profile, два уникальных start payload, два ручных approve и
санitized evidence `payload → bot_started user_id → membership user_id`.

До получения такого evidence нельзя помечать attribution как EXACT и нельзя
создавать production adapter, который выдумывает invite/source поля.

## Изменения и проверки

Изменён только этот research-файл:

```text
research/MAX_INDIVIDUAL_INVITE_ATTRIBUTION_FINAL_REPORT.md
```

Production worker, Attribution Engine, БД, миграции, funnel, WebSocket
transport и auto-approve не изменялись. Opcode 77 не отправлялся, UI approve не
использовался, новые invite RPC не воспроизводились.

Проверены/использованы существующие sanitized reports:

```text
research/TELEGRAPHYX_TRAFFIC_MAX_BLACKBOX_REPORT.md
research/MAX_INVITE_ATTRIBUTION_REPORT.md
research/MAX_NATIVE_INVITE_ROTATE_INTERCEPT_REPORT.md
research/MAX_CREATE_ADDITIONAL_INVITE_ANALYSIS.md
```

Секреты, cookies, токены, raw frames и session data в этот файл не заносились.
