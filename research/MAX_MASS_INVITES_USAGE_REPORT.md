# MAX: массовые native invite-ссылки и определение использования

Дата проверки: 2026-08-11. Тестовый объект: собственный приватный канал MAX `Твой Петербург`, официальный MAX Web. Production-воронка не менялась; mini app, `startapp`, Метрика и временная атрибуция не использовались.

## Executive verdict

**NO / не доказано:** MAX Web показывает одну текущую invite-ссылку канала, но возможность создать одновременно несколько независимых native invite, получить их список и определить использование конкретной ссылки не подтверждена.

Поэтому MAXIBOT сейчас не может надёжно реализовать:

```text
visitor A → Invite A → request/member A
visitor B → Invite B → request/member B
```

без не документированного server-side механизма MAX, который должен вернуть per-invite usage или invite ID в заявке.

## Наблюдение в MAX Web

В профиле собственного приватного канала открыта форма «Пригласить по ссылке». Она содержит:

- одну текущую ссылку вида `https://max.ru/join/<opaque-token>`;
- «Копировать», «Отправить в MAX», QR-код;
- переключатель «Заявки на вступление»;
- меню ссылки, в котором в тесте был только пункт «Обновить».

Отдельные поля `id`, `invite_id`, `link_id`, `usage_count`, `request_count`, `join_count`, `usage_limit`, `expires_at` или `revoked` в UI не показаны.

Пункт «Обновить» не дал наблюдаемого отдельного HTTP/XHR endpoint или отправленного WebSocket operation в снятом сетевом окне; отображаемая ссылка после операции не изменилась. Поэтому считать эту кнопку операцией создания новой invite нельзя.

Официальная документация MAX описывает приглашение в канал как копирование/передачу ссылки или QR-кода, но не публикует метод `createInvite`, `getInvites`, `revokeInvite` или объект со счётчиками использования. [Управление каналами MAX](https://dev.max.ru/docs/channels/manage)

## Network / WebSocket

При загрузке собственного канала наблюдался WebSocket `wss://api.oneme.ru/websocket` с бинарными сообщениями. В декодируемых фрагментах присутствовали общие операции/объекты каналов, `JOIN_REQUEST`, `pendingJoinRequest`, `inviterIds`, `link`, `expires`, `requests`, но отдельной доказанной операции создания или списка invite не получено.

Объект pending request, ранее прочитанный через WebSocket `JOIN_REQUEST`, содержал `contact.id` и профиль пользователя. В нём не обнаружены `invite_id`, `link_id`, invite token, source или usage counter. Это не даёт per-invite attribution.

## Результаты этапов ТЗ

| Проверка | Результат |
|---|---|
| Одна native invite | Подтверждена, opaque URL |
| Multiple active invites | **NO / не подтверждено** |
| 5 последовательных createInvite | Не выполнено: endpoint создания не найден |
| 5/10/25/50 лимиты | Не выполнялись, так как механизм массового создания не доказан |
| Список invite | Не найден |
| Per-invite usage/request/join counter | Не найден |
| A/B User A → Invite A, User B → Invite B | Не выполнен: доступен один авторизованный MAX-аккаунт |
| Revoke конкретного invite | Не найден |
| usage_limit=1 | Не найден |
| expiry/TTL | Поле не найдено в объекте invite; не проверялось созданием |
| URL token как идентификатор | Токен уникален как URL-строка, но server-side состояние его использования не наблюдается |

## Что можно утверждать о заявке и участнике

Официальный `Update` содержит событие `user_added`, но его публичная схема не даёт invite ID или источник. [Документация Update](https://dev.max.ru/docs-api/objects/Update)

`GET /chats/{chatId}/members` позволяет получить участников и проверить конкретный `user_id`, но результат не связывает пользователя с invite URL. [Документация members](https://dev.max.ru/docs-api/methods/GET/chats/-chatId-/members)

Следовательно, даже наличие двух разных URL само по себе не создаёт доказуемую связку:

```text
invite_token → request/user → member
```

## Возможность для MAXIBOT

```text
Can MAXIBOT assign a unique native MAX invite to every visitor?
NO / не доказано по доступному MAX Web/API

Can MAXIBOT know that this exact invite produced a request/subscription?
NO
```

Локальная таблица пула invite не добавлялась: без сигнала MAX она записывала бы только `assigned_visitor_id`, но не факт использования. Это было бы лишь внутренним назначением ссылки, а не exact attribution.

## Условия пересмотра verdict

Verdict изменится на `YES` только после получения хотя бы одного из следующих server-side сигналов:

```text
Invite A.usage_count changed
Request.invite_id == A
Invite A contains User A
```

или официального API, возвращающего эти данные. В текущем тесте ни один сигнал не найден.

