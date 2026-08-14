# MAX WebSocket reproduction probe

This probe is intentionally not imported by production code. It is read-only
and fail-closed.

```bash
MAX_WEB_TOKEN='(provided through the secret manager)' \
  python research/max_ws_reproduction/max_ws_probe.py
```

The token must be supplied manually through the environment or secret manager.
The probe never reads browser cookie stores and never uses `MAX_BOT_TOKEN`.

At the current capture state it stops before opening a socket because the
binary AUTH/RPC encoder is not proven byte-for-byte. This prevents accidental
transmission of a guessed frame.
