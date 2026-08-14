# MAX: поиск аналога Telegram `createChatInviteLink`

Дата: 2026-08-11. Проверен актуальный официальный MAX Web frontend на собственном приватном канале. Operation 55 повторно не тестировалась как кандидат: её rotate/revoke-смысл уже доказан.

## Verdict

**UNKNOWN globally / NO candidate in Web.**

В Web bundle не найден отдельный RPC или объект, похожий на `createChatInviteLink`, который создаёт независимую ссылку без отзыва текущей. Однако Desktop/mobile-клиенты в текущем окружении не установлены, поэтому глобальный verdict для всех официальных клиентов нельзя честно заменить на `NO`.

Практический результат для MAXIBOT: additional invite pool пока не подтверждён и реализовывать его нельзя.

## Operation 55 и соседние channel RPC

В bundle подтверждён уже известный вызов:

```text
send(55, { chatId: e.id, revokePrivateLink: true })
```

Он находится в функции обновления/отзыва private link. В том же наборе channel-management вызовов обнаружены операции общего назначения:

```text
52  remove/delete chat
54  remove/clear message or chat state
55  pin/change owner/revoke private link — payload зависит от ветки
58  chat membership/info-related operation
61  chat settings/privacy-related operation
64  send message/post
75  subscribe/unsubscribe post
77  add/remove members and JOIN_REQUEST operations
```

Отдельного operation с семантикой `create additional invite`, `list invites`, `revoke invite_id` или параметрами per-link не обнаружено. Номер operation не трактуется как API-контракт сам по себе; вывод основан на frontend call sites и payloads.

## Поиск по структурам данных

В загруженных Web bundles (~4.0 MB) проверены references и строки по направлениям:

```text
privateLink / privateLinks
inviteLink / inviteLinks
additionalLink / accessLink / customInvite
createInvite / invite_id / linkId
usageLimit / usage_count / request_count / join_count
expiresAt / expireAt / ttl
joinRequest / pendingJoinRequest
```

Найденное `inviteLinks` относится к UI-шару «поделиться ссылкой» и списку каналов действий (`link`, Telegram, VK, WhatsApp), а не к массиву серверных invite-сущностей. `privateLinks` во множественном числе, `createInvite`, `invite_id`, `linkId`, `usageLimit`, usage counters и additional/access-link сущности не найдены.

`expiresAt` относится к QR-аутентификации Web, а не к сроку действия channel invite. Это исключено из candidate operations.

## Что возвращает текущий link object

Текущая native ссылка приходит как часть объекта `chat`/его link-представления и отображается UI как одна строка `https://max.ru/join/<opaque-token>`. При rotate ответ обновляет объект `chat` и заменяет ссылку. В observed data нет массива `invites[]`/`links[]` с независимыми объектами.

## Сопоставление с Telegram

| Telegram | MAX Web result |
|---|---|
| `exportChatInviteLink` | **FOUND**: operation 55 с `revokePrivateLink:true`, rotate primary |
| `createChatInviteLink` | **NOT FOUND in Web** |
| `ChatInviteLink` | **NOT FOUND** как отдельный объект additional invite |
| `ChatJoinRequest.invite_link` | **NOT FOUND** в ранее наблюдавшемся MAX pending request |
| `revokeChatInviteLink` | **CANDIDATE/partial**: operation 55 отзывает current private link, отдельного invite_id не принимает |
| `approveChatJoinRequest` | **NOT FOUND** в публичном API и проверенном Web contract |

Официальная документация MAX описывает приглашение в канал через ссылку/QR и обновление настроек приватного канала, но отдельный публичный API дополнительной invite-ссылки не описывает. [Управление каналами MAX](https://dev.max.ru/docs/channels/manage)

## Почему PoC не добавлен

Без candidate RPC таблица `max_invites` была бы только локальным назначением одного и того же primary token разным visitor. Это не создало бы независимые ссылки и не прошло бы критерий:

```text
A active + B active + C active
```

Поэтому `create_additional_max_invite(channel_id)` и пул invite в MAXIBOT не добавлялись.

## Ограничение исследования

Web-ветка полностью проверена статически. Desktop/mobile требуют отдельной установки и отдельного авторизованного тестового сеанса; их отсутствие не является доказательством, что backend MAX принципиально не имеет скрытого или будущего additional-invite API.

