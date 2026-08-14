# MAX Web runtime instrumentation report

Дата: 2026-08-13

## Scope

Добавлен отдельный manual-run CDP/Playwright probe для получения декодированного
frontend-кандидата, из которого MAX Web рисует pending-заявку. Probe не читает
cookies, browser storage или профили, не отправляет WebSocket frames и не
выполняет UI approve/reject.

## Verdict

```text
RUNTIME_INSTRUMENTATION_TOOL = READY
AUTHORIZED_CDP_SESSION = NOT_AVAILABLE_IN_THIS_RUN
DISPLAY_NAME_CAPTURED = NOT_VERIFIED
CHAT_ID_CAPTURED = NOT_VERIFIED
JOIN_REQUEST_TYPE_CAPTURED = NOT_VERIFIED
PENDING_USER_ID_VERIFIED = NO
PROGRAMMATIC_OPCODE_77 = NOT_ATTEMPTED
```

## Evidence boundary

Существующие sanitized wire artifacts доказывают `JOIN_REQUEST`, `chatId` и
наличие `contact.id` в отдельном response, но не доказывают, что этот ID
относится к отображаемой в UI записи «Любимая Печенька». В текущем запуске
авторизованная CDP-сессия недоступна, поэтому новый probe не запускался и
identity не объявляется подтверждённой.

Положительный verdict разрешён только если один runtime candidate содержит все
поля одновременно: `displayName=Любимая Печенька`, тестовый `chatId`,
`type=JOIN_REQUEST` и `contact.id` либо `userId`. DOM-кандидат с одним именем
не считается доказательством.

## Manual next step

```bash
MAX_CDP_ENDPOINT=http://127.0.0.1:9222 \
  python research/max_cdp_runtime_instrumentation/runtime_probe.py
```

Перед запуском оператор должен открыть pending-request panel в уже
авторизованной MAX Web-сессии. Результат появится в локальном
`research/max_cdp_runtime_instrumentation/evidence.jsonl`, который исключён из
Git. После ручного запуска нужно проверить одну запись-кандидат и обновить
этот отчёт только при наличии полного доказательства связки.

## Verification

```text
pytest tests/test_max_cdp_runtime_probe.py = 4 passed
py_compile = passed with PYTHONPYCACHEPREFIX=/tmp/maxibot-pycache
manual run without MAX_CDP_ENDPOINT = blocked, exit 2
runtime evidence written in this run = no
project Git repository = unavailable at project root
```
