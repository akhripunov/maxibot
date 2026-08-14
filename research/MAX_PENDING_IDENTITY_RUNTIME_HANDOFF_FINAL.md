# Final handoff: verified pending identity → opcode 77

Дата: 2026-08-13.

## Verdict

```text
PROGRAMMATIC_PENDING_LIST = WORKING
PENDING_USER_ID_VERIFIED = YES
PROGRAMMATIC_OPCODE_77 = WORKING
PENDING_REMOVAL_CONFIRMED = YES
MEMBERSHIP_CONFIRMATION = WORKING
AUTO_APPROVE_E2E = PASSED
```

`A_B_ADDRESSABILITY_PROOF` не выполнялся: второго одновременно pending
пользователя не было.

## Evidence

До действия MAX Web UI показывал:

```text
Заявки на вступление: 1 заявка
displayName: Любимая Печенька
```

Python отправил доказанный pending request opcode 59 с `type=JOIN_REQUEST`,
target `chatId` и `count=50`. Ответ содержал:

```text
members[0].contact.id = [REDACTED]
members[0].contact.names = Любимая Печенька
```

Identity guard сравнил `contact.id` pending response с единственным user ID в
captured opcode-77 frame. `userIds` содержал ровно одного пользователя.

## Approve

Программно отправлен ровно один captured opcode 77:

```text
opcode = 77
operation = add
type = JOIN_REQUEST
chatId = target channel
userIds = [one verified user]
```

Correlated binary response получен, длина 3634 байта. UI approve не
использовался. Второй destructive request не отправлялся.

## Post-conditions

После RPC:

```text
pending UI: Нет заявок
target row: отсутствует
channel subscriber/member state: 11 подписчиков
```

Approve response содержал обновление `chat`; вместе с исчезновением pending и
актуальным subscriber state это принято как membership confirmation.

## Sanitized timeline

```text
T0  MAX Web auth available
T1  UI showed 1 pending request
T2  Python opcode 59 sent
T3  pending response received; displayName/contact.id verified
T4  Python opcode 77 sent for exactly one user
T5  correlated approve response received (3634 bytes)
T6  pending UI became empty
T7  member/subscriber state confirmed at 11
```

## Изменённые файлы

- `research/max_ws_reproduction/max_ws_probe.py` — pending response capture,
  identity decode и single-user guard;
- этот sanitized handoff report.

Production worker, Attribution Engine, БД, миграции и feature flags не менялись.

## Tests / security

- AUTH + read-only RPC + pending RPC + single approve probe выполнены;
- `contact.id` и approve user ID совпали в локальной проверке;
- raw frames и token остаются только в ignored private-каталоге;
- секреты в отчёт не выводились;
- ручной approve не выполнялся.
