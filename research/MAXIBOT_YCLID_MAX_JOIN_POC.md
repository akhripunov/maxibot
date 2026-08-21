# MAXIBOT YCLID → MAX JOIN_REQUEST PoC

Дата: 2026-08-17

## Результат локального PoC

```text
LANDING_VISITOR_CREATED = PASS (in-memory PoC)
VISIT_CREATED = PASS
YCLID_BOUND_TO_VISIT = PASS

START_TOKEN_CREATED = PASS
START_TOKEN_RESOLVED = PASS

MAX_USER_ID_CAPTURED = PASS (test value supplied by Mini App boundary)
MAX_USER_BOUND_TO_VISITOR = PASS

CHANNEL_OPENED = PASS (no extra user click in PoC)
JOIN_REQUEST_OBSERVED = PASS (synthetic event)
JOIN_USER_EQUALS_BOUND_USER = PASS

YCLID_TO_MAX_USER_CHAIN = PASS (TEST_YCLID → visit → signed token → max-user-42)
MAX_USER_TO_YCLID_CHAIN = PASS (max-user-42 → exact visit → TEST_YCLID)

E2E_ATTRIBUTION = BLOCKED for real MAX; PASS for isolated PoC
EXTRA_USER_CLICK_BEFORE_CHANNEL = 0
MINI_APP_VISIBLE_DURATION = NOT_MEASURED (requires real MAX client)
PRODUCTION_CHANGED = NO
```

## Что реализовано

`app/traffic/identity_poc.py` содержит минимальный изолированный state machine:

1. Создаёт отдельные `visitor_id` и `visit_id`, сохраняет `TEST_YCLID`.
2. Выпускает opaque signed start token с коротким сроком действия.
3. Проверяет подпись, срок действия и consumes token exactly once.
4. Принимает фактический `max_user_id` на границе Mini App и связывает его с visit.
5. Открывает валидный HTTPS MAX channel URL без промежуточной кнопки.
6. Принимает JOIN_REQUEST только при exact match `channel_id + max_user_id`.
7. Восстанавливает обе стороны цепочки: JOIN_REQUEST → user → visit → YCLID.

Модуль намеренно не импортируется application runtime. Миграций, env-переменных,
polling, webhook-подписок, MAX API и Яндекс API не добавлялось.

## Ограничение доказательства

Реальный MAX E2E в рамках этой задачи не выполнен: не предоставлен безопасный
тестовый MAX-аккаунт, закрытый тестовый канал и доступный для запуска Mini App
контур, поэтому фактические `MAX user_id` и `JOIN_REQUEST` нельзя честно
назвать наблюдёнными. Synthetic JOIN_REQUEST проверяет только контракт точного
сопоставления и не заменяет VPS/E2E.

## Тесты

- `tests/test_identity_poc.py` добавляет три проверки: полный chain, подпись и
  one-time replay protection, отказ от JOIN_REQUEST другого пользователя.
- Compile/import smoke test: PASS.
- Functional smoke assertions: PASS (`identity_poc_smoke: PASS`).
- `pytest` не запущен: в доступном окружении отсутствует пакет `pytest`.

## Handoff / статус

```text
CODE-POC = PASS
AUTOMATED-TEST-CODE = PASS
REAL-MAX-MINI-APP-E2E = BLOCKED
REAL-JOIN_REQUEST-E2E = BLOCKED
PRODUCTION-DEPLOY = NOT PERFORMED
MIGRATIONS = NONE
ENV = none
```
