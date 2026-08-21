# Handoff: automatic group-chat inbox connection

Date: 2026-08-21

## Implemented

- PRIMARY official `bot_added` with `is_channel=false` is handled as a group-chat flow.
- Official `GET /chats/{chat_id}` is required and accepted only for type `chat` or `group`.
- Binding occurs only when the owner has an explicit pending `inbox_connect`/`inbox_change` channel.
- No forwarded post or test message is required.
- Missing pending state and ambiguity fail closed with an owner notification.
- Duplicate binding remains idempotent through the existing channel binding.
- `bot_removed` clears the matching inbox chat, disables receiving, and notifies the owner.
- Channel auto-connect, Yandex, attribution, landing, and traffic flows were not changed.

## Files

- `app/bot/runtime.py`
- `app/bot/menu.py`
- `tests/test_bot_added_auto_connect.py`

No migrations or new environment variables.

## Tests

Targeted suite:

```text
23 passed
```

Covered source contracts include valid group metadata, explicit pending
channel, no forwarded-message dependency, no-pending/ambiguous rejection,
idempotency, conflict protection, and safe unbind path.

## Production

- Host: `135.106.166.46`
- API: healthy
- API `MAX_RUN_POLLING=false`
- Sole worker `MAX_RUN_POLLING=true`
- Production deploy: PASS

## Real E2E

Status: **BLOCKED — owner action required**.

To complete the real test, the owner must:

1. Open MAXIBOT → `Мои каналы` → select a test/allowed channel → `Подключить чат`.
2. Create or select a test group chat.
3. Add MAXIBOT to that group as required by MAX.

Do not forward a post and do not send a test message. After the event, verify
worker logs for `GET /chats`, binding, owner notification, and updated channel
screen. Production working channels must not be used for this test.

Yandex conversion: disabled/not touched.
