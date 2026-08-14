# MAX Auto-Approve: SYNC-state verification

Дата: 2026-08-13. Approve через UI и программный opcode 77 не выполнялись.

## Verdict

```text
PROGRAMMATIC_MAX_AUTH = WORKING
SYNC_JOIN_REQUEST = FOUND
PROGRAMMATIC_PENDING_SOURCE = SYNC_STATE
PROGRAMMATIC_PENDING_LIST = NOT_WORKING
PENDING_USER_ID_VERIFIED = NO
PROGRAMMATIC_OPCODE_77 = NOT_WORKING
PENDING_REMOVAL_CONFIRMED = NO
MEMBERSHIP_CONFIRMATION = NOT_WORKING
A_B_ADDRESSABILITY_PROOF = BLOCKED
MAX_WEB_SESSION_TRANSPORT = PARTIAL
AUTO_APPROVE_E2E = BLOCKED
```

## Что найдено

После AUTH в server sync frame обнаружены реальные строки/структуры
`pendingJoinRequest` и `JOIN_REQUEST`. Это подтверждает наличие pending-состояния
в SYNC, без отдельного outgoing pending-list RPC.

В MAX Web UI одновременно отображается одна заявка «Любимая Печенька».
Однако доступный raw sync-фрейм не содержит доказуемо сопоставимого в отчёте
вида `contact.id` для этой конкретной записи; identity-поле закодировано в
нестабильном binary payload. Одного имени из UI недостаточно для безопасного
выбора пользователя.

Поэтому `PROGRAMMATIC_PENDING_LIST` не объявлен рабочим и approve намеренно
остановлен до получения проверяемого `max_user_id` для того же User A.

## Безопасно доказано

- Python AUTH работает;
- Python read-only RPC работает;
- SYNC содержит `JOIN_REQUEST`;
- production flags не включались;
- токен/raw frames не выводятся и остаются в private-каталоге.

## Следующий блокер

Для следующего шага добавлен отдельный read-only runtime probe:
`research/max_cdp_runtime_instrumentation/runtime_probe.py`. Он подключается к
уже авторизованному CDP endpoint, перехватывает только декодированные JSON,
видимый DOM и React props/fiber-кандидаты, содержащие признаки заявки, и
сохраняет sanitized evidence. Cookies, storage, raw binary frames и secrets не
читаются. Probe ничего не нажимает и не отправляет.

Текущий verdict остаётся `PENDING_USER_ID_VERIFIED = NO`: доступной CDP-сессии
в этом запуске нет, а существующие artifacts не содержат доказанной связки
одного объекта `displayName=Любимая Печенька` + тестовый `chatId` +
`type=JOIN_REQUEST` + `contact.id/userId`. До такой связки opcode 77 не
отправляется.
