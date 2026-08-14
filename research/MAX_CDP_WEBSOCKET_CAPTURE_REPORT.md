# MAX WebSocket CDP capture report

Дата: 2026-08-13. Capture выполнен в собственной авторизованной сессии MAX
Web; production-код, БД, worker и Attribution Engine в рамках probe не
изменялись.

## Verdict

```text
CDP_CAPTURE = WORKING
MAX_WS_URL = FOUND
MAX_WS_HANDSHAKE = CAPTURED
MAX_WS_AUTH = AUTH_FRAME
BINARY_ENVELOPE = PARTIAL
PENDING_LIST_RPC = PARTIAL
OPCODE_77_FRAME = FULLY_CAPTURED
OPCODE_77_RESPONSE = CAPTURED
```

## Handshake

Найден основной WebSocket:

```text
wss://api.oneme.ru/websocket
HTTP response: 101 Switching Protocols
requestId: 40067.168
```

CDP capability не отдал handshake request headers, поэтому наличие Cookie и
Origin в upgrade нельзя утверждать. В раннем исходящем binary frame найдено
поле `token`; значение не сохранялось. Следовательно, наблюдаемая авторизация
происходит через WS auth/sync frame. Cookie presence остаётся UNKNOWN.

Последовательность:

```text
WS created
→ handshake request
→ 101 response
→ bootstrap: userAgent/deviceType/WEB and client metadata
→ bootstrap response
→ auth/sync frame with token=[REDACTED]
→ profile/chats response
→ first normal RPC: chatIds
```

## Binary envelope

Общий envelope частично понятен по префиксам кадра:

```text
direction marker + sequence/request marker + opcode + flags/length + binary payload
```

Подтверждено, что operation marker коррелирует между approve request и response,
а payload использует MessagePack-like строки/ext values. Поля sequence, opcode,
flags и payload length видны в hex, но полный стабильный encoder/decoder ещё не
доказан. Production reproduction не выполнялся.

## Pending-list

Из предыдущего sanitized capture подтверждены `JOIN_REQUEST`, `chatId`,
`count=50` и response `members[].contact.id`. Свежий handshake probe не мог
подтвердить pending response: в тестовой вкладке не было pending-заявки, а
создание/одобрение пользователя не выполнялось.

## Opcode 77

Предыдущий собственный capture содержит полный sanitized approve request:

```text
opcode = 77
chatId = present
userIds = exactly one user
showHistory = present
operation = add
type = JOIN_REQUEST
```

Отдельно захвачен ответ того же operation marker; в нём присутствуют `checkResult`
и обновлённый `chat`. Секреты и реальные идентификаторы в отчёт не включены.

## Files

- `capture_summary.json` — sanitized handshake/timeline/verdict;
- `frames_sanitized.jsonl` — длины, hex heads и безопасные поля кадров;
- `.gitignore` — raw capture extensions исключены из репозитория.

Raw auth frames намеренно не сохранялись. Для полноценного `BINARY_ENVELOPE =
UNDERSTOOD` нужен отдельный capture pending-list в момент, когда реальная заявка
видна в UI, плюс fixture-backed decoder; этот probe не отправлял approve и не
включал auto-approve.
