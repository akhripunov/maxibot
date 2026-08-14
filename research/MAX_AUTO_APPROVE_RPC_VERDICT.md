# MAX Auto-Approve investigation

Дата проверки: 2026-08-13.

## Что проверено

В авторизованном MAX Web открыт канал «Твой Петербург». В списке заявок была
ровно одна запись: «Жанылай».

До действия UI показывал:

```text
Заявки на вступление: 1 заявка
Жанылай — Одобрить заявку / Отклонить заявку
```

Нажата только кнопка `Одобрить заявку`, без `Approve All`. После действия:

```text
Заявка одобрена
Заявки на вступление: Нет заявок
Вы разобрали все заявки
```

## RPC verdict

```text
SINGLE_APPROVE_RPC = FOUND
OPCODE = 77 (0x4d)
REQUEST_KIND = JOIN_REQUEST
OPERATION = add
```

Захват WebSocket был включён до клика. Единственный отправленный кадр
содержал структуру с отдельными полями `chatId`, `userIds`, `showHistory`,
`operation=add` и `type=JOIN_REQUEST`. Идентификаторы в отчёт не записываются.
По форме кадра подтверждено, что UI отправляет адресную операцию для одного
пользователя, а не команду массового одобрения.

После отправки получен ответ MAX Web; UI показал уведомления `Заявка
одобрена`, а список изменился с одной заявки на `Нет заявок`.

## Pending-list verdict

```text
PENDING_LIST_RPC = NOT_CAPTURED
```

UI-поведение подтверждено визуально, но capture списка был включён после его
открытия, поэтому transport-level запрос загрузки списка ещё не зафиксирован.

## Реализовано безопасно

- `max_pending_join_requests` с `first_seen_at`, lifecycle status и retry state;
- `Channel.auto_approve_enabled`, default `false`;
- `MaxAutoApproveWorker` с отдельными provider/approver интерфейсами;
- при отсутствии доказанного transport worker возвращает `AUTH_REQUIRED` и не
  отправляет никаких запросов;
- worker обрабатывает каждого пользователя отдельным approve call, не имеет
  режима `Approve All`;
- `pending_first_seen` поддержан как ранний attribution event, но не выдаётся
  за native request timestamp;
- конфигурация и polling/worker flags по умолчанию выключены.

## Ограничение

Точный кадр одобрения теперь зафиксирован, но он получен из авторизованной
сессии MAX Web, тогда как backend проекта работает через отдельный
bot/provider-контур. Поэтому `MaxAutoApproveWorker` пока остаётся выключенным
lifecycle-контуром, а не активным transport adapter: нельзя безопасно
перенести пользовательскую сессию MAX Web в backend без отдельной авторизации
и проверки API-контракта.

## Production-TЗ verdict

```text
PENDING_LIST_RPC = FOUND
PENDING_LIST_OPCODE = JOIN_REQUEST (numeric frame opcode not isolated)
MAX_WEB_SESSION = AUTH_BLOCKED
PROGRAMMATIC_OPCODE_77 = NOT_WORKING
A_B_ADDRESSABILITY_PROOF = BLOCKED
AUTO_APPROVE_E2E = BLOCKED
PYTHON_3_12_ENV = FAILED
PYTEST_BASELINE = NOT_RUN (pytest unavailable)
MAX_WS_HANDSHAKE = UNKNOWN
MAX_BINARY_ENVELOPE = PARTIAL
```

Для pending-list ранее был сохранён sanitized-захват: запрос `JOIN_REQUEST`
с `chatId` и `count=50`, ответ с верхним уровнем `members`; в каждом
наблюдаемом элементе доступен `contact.id`, иногда имя. `request_id`,
`requested_at`, invite/source ID в response не обнаружены.

Добавлены `MaxWebSession`, узкие методы list/approve/session-state, состояния
сессии, auth/error boundary, single-user worker adapters и parser observed
response. Реальная отправка не включается: в репозитории нет credentials
администратора MAX Web и полностью подтверждённого binary frame envelope.
Placeholder JSON не отправляется как production RPC.

Новый capture до загрузки MAX Web из текущего Browser API не удалось получить:
raw CDP требует уже загруженную HTTP(S)-страницу и не отдал handshake-события
для новой вкладки. Cookies, local storage и системные cookie stores не
читались. Поэтому WS headers, auth bootstrap frames и точный общий envelope
остаются неизвестными.

В окружении проекта отсутствуют Python 3.12 и `pytest`; найден только Python
3.11.15 без pytest. Выполнены только `py_compile` и безопасный import smoke.

A/B и end-to-end не объявляются успешными: для них нужны два одновременно
pending пользователя и авторизованный серверный connector. Яндекс остаётся в
`YANDEX_CONVERSION_DRY_RUN=true`, все auto-approve flags — `false`.
