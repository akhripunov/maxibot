# MAX native invite: перехват и воспроизведение операции

Дата проверки: 2026-08-11. Объект: собственный приватный канал MAX `Твой Петербург`. Проверка выполнена в официальном MAX Web под администратором. Токены и сессионные данные в отчёте не публикуются.

## Executive verdict

**PARTIAL — обнаружен только ROTATE INVITE.**

MAX Web действительно получает native invite URL и умеет перевыпустить её, но новая ссылка отзывает предыдущую. Дополнительные одновременно активные invite-ссылки для одного канала не подтверждены.

```text
Invite A → active
rotate → Invite B, Invite A revoked
rotate → Invite C, Invite B revoked
```

Итоговый критерий `A + B + C одновременно active` не выполнен.

## Найденный transport и операция

Invite-ссылка приходит внутри WebSocket-ответа общего состояния канала через:

```text
wss://api.oneme.ru/websocket
```

В официальном frontend bundle обнаружен вызов обновления приватной ссылки:

```text
RPC operation: 55
request fields:
  chatId: <test-channel-id>
  revokePrivateLink: true
```

Фактический transport — бинарный WebSocket RPC, поэтому wire frame не является JSON. В исходящем frame после подтверждения UI присутствуют поля `chatId` и `revokePrivateLink`; ответ содержит обновлённый объект `chat` с новой ссылкой/состоянием канала.

Frontend bundle также содержит логику:

```text
await send(55, { chatId: e.id, revokePrivateLink: true })
await chats.upsert(response.chat)
```

Смысл операции подтверждён самим UI: перед вызовом показывается предупреждение «Предыдущая ссылка на канал перестанет работать».

## Реальный эксперимент

Токены ниже намеренно редактированы. Это не потеря данных: для результата важны различие и состояние ссылок.

| Шаг | Ссылка | Результат |
|---|---|---|
| До rotate | `https://max.ru/join/[TOKEN_A]` | исходная ссылка |
| Rotate #1 | `https://max.ru/join/[TOKEN_B]` | новый токен, A отозван |
| Проверка A | `/join/[TOKEN_A]` | «Сюда можно попасть только по пригласительной ссылке» |
| Rotate #2 | `https://max.ru/join/[TOKEN_C]` | новый токен, B отозван |
| Проверка B | `/join/[TOKEN_B]` | ссылка отозвана |
| Проверка C | `/join/[TOKEN_C]` | открывается карточка канала «Твой Петербург» |

Таким образом, вызовы возвращают разные токены, но не создают независимый пул:

```text
call #1 → TOKEN_B
call #2 → TOKEN_C
```

Операция является **ROTATE/REVOKE CURRENT PRIVATE LINK**, а не `CREATE ADDITIONAL INVITE`.

## Что не найдено

В проверенных Web UI и загруженных frontend bundle не обнаружены подтверждённые методы или объекты для:

```text
create additional invite
get invites list
invite_id / link_id
usage_count / request_count / join_count
usage_limit
expires_at для invite
revoke отдельного invite_id
```

Публичная документация MAX описывает приглашение ссылкой или QR-кодом и обновление ссылки, но не документирует API пула независимых invite. [Управление каналами MAX](https://dev.max.ru/docs/channels/manage)

## Безопасность воспроизведения

Отдельный программный PoC-клиент не добавлялся: воспроизведение RPC с авторизационными WebSocket frames потребовало бы переносить сессионные credentials и могло бы повторно отзывать рабочую ссылку. Достаточный black-box результат получен штатным UI на собственном канале: точный operation ID и payload semantics установлены, а поведение rotate проверено двумя последовательными вызовами.

Если понадобится технический внутренний скрипт для собственного окружения, его безопасная форма должна использовать действующий авторизованный клиентский сеанс и явное подтверждение перед каждым `revokePrivateLink`, поскольку операция необратимо отключает предыдущую ссылку.

## Итог для MAXIBOT

```text
Can MAXIBOT generate multiple simultaneously active native invites?
NO — через найденный официальный механизм только rotate одной ссылки.

Can MAXIBOT obtain the current native invite programmatically?
PARTIAL — WebSocket RPC 55 обнаружен, но публичного стабильного API-контракта нет.

Can MAXIBOT maintain Invite A/B/C pool?
NO — старые ссылки отзываются при выпуске новой.
```

