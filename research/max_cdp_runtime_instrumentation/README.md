# MAX Web runtime instrumentation probe

Research-only, read-only probe for correlating the visible pending request
`Любимая Печенька` with the decoded frontend object rendered by MAX Web.

It requires the operator to supply an already authenticated CDP endpoint. The
probe does not inspect cookies, browser storage, profiles, credentials, or raw
WebSocket binary frames. It does not navigate, click, approve, reject, or send
WebSocket messages. Only sanitized candidates containing a relevant marker are
written.

## Manual run

```bash
MAX_CDP_ENDPOINT=http://127.0.0.1:9222 \
  python research/max_cdp_runtime_instrumentation/runtime_probe.py
```

Open the pending-request panel in the already authenticated MAX Web tab before
running the probe. The output is `evidence.jsonl`; it is intentionally ignored
by Git. A positive verdict still requires one object to contain, in the same
object/record, `displayName`, the test `chatId`, `type=JOIN_REQUEST`, and
`contact.id` or `userId`. A name-only DOM candidate is not identity evidence.
