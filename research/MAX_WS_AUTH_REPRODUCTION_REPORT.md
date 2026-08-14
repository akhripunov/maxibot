# MAX Web AUTH reproduction report

Дата: 2026-08-13.

## Verdict

```text
RAW_AUTH_FRAME_CAPTURED = YES
AUTH_FRAME_SCHEMA = PARTIAL
AUTH_FRAME_ROUNDTRIP = NORMALIZED_MATCH
MAX_WEB_TOKEN_PROVIDED = YES
WS_CONNECTION = WORKING
PROGRAMMATIC_MAX_AUTH = WORKING
```

Python AUTH-only probe подключился к `wss://api.oneme.ru/websocket`, отправил
captured bootstrap и AUTH-frame с токеном собственной авторизованной Chrome
сессии и получил бинарный ответ MAX длиной 141202 байта. Pending, approve и
opcode 77 не отправлялись.

Токен и raw AUTH-frame сохранены только в
`research/max_ws_reproduction/private/`. Значение токена не выводилось и не
логировалось; browser cookie/profile stores не читались. Private-каталог
игнорирует содержимое через `.gitignore`.

## Sanitized timeline

```text
T0 Python probe started
T1 WS connected: wss://api.oneme.ru/websocket
T2 HTTP 101
T3 bootstrap sent
T4 AUTH_FRAME sent
T5 MAX binary auth response received (141202 bytes)
T6 PROGRAMMATIC_MAX_AUTH = WORKING
T7 connection closed
```

## Ограничения

`AUTH_FRAME_SCHEMA` остаётся `PARTIAL`: доказана воспроизводимость конкретного
captured frame после подстановки токена, но универсальный binary encoder MAX
не строился. Дальнейшие read/pending/approve шаги этим ТЗ намеренно не
выполнялись.
