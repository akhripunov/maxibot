# MAX Auto-Approve real RPC verification

Дата: 2026-08-13. В рамках этого теста ручной approve в MAX Web не выполнялся.

## Verdict

```text
PROGRAMMATIC_MAX_AUTH = WORKING
PENDING_LIST_RPC = PARTIAL
PROGRAMMATIC_PENDING_LIST = NOT_WORKING
PROGRAMMATIC_OPCODE_77 = NOT_WORKING
MEMBERSHIP_CONFIRMATION = NOT_WORKING
A_B_ADDRESSABILITY_PROOF = BLOCKED
MAX_WEB_SESSION_TRANSPORT = PARTIAL
AUTO_APPROVE_E2E = BLOCKED
```

## Что доказано

Python-клиент с локальным AUTH-frame и токеном собственной MAX Web-сессии:

```text
AUTH → wss://api.oneme.ru/websocket → binary response
→ captured read-only chatIds RPC → binary response
```

`PROGRAMMATIC_READ_RPC = WORKING`. Никаких pending/approve RPC отправлено не
было.

## Pending

В MAX Web UI одновременно видна одна заявка, имя отображается как «Любимая
Печенька». Отдельный outgoing pending-list frame при открытии панели в текущем
сеансе не появился: список был уже включён в sync state. Предыдущий
sanitized-захват знает только `JOIN_REQUEST`, `chatId`, `count=50` и
`members[].contact.id`, но полного request envelope для Python нет.

Поэтому `PROGRAMMATIC_PENDING_LIST` не объявляется успешным, а opcode 77 не
отправляется. Это защищает от approve не того пользователя.

## Следующий блокер

Нужен один новый capture именно pending-list request/response: создать новую
заявку после установления capture либо вызвать доказанный UI refresh, чтобы
получить outgoing frame и correlated response. Только после сопоставления
User A из response с UI можно отправлять opcode 77.
