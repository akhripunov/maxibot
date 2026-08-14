# MAX Worker Bridge Feasibility Extension

This is a local, read-only Manifest V3 diagnostic. It is not imported by
MAXIBOT production code and never sends WebSocket frames, opcode 59/77, clicks,
or UI actions. It records only target type, MAX origin, masked target/session
IDs, and sanitized attach/runtime/network status. WebSocket events are reduced
to event name, direction, origin and payload length; cookies, tokens, frame
payloads, and auth material are not stored.

## Load in Chrome

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Click **Load unpacked**.
4. Select this directory:
   `research/max_worker_bridge_extension/`
5. Open MAX in the same Chrome profile.
6. Open the extension popup and click **Start capture & scan**.
7. Only after the popup reports `SCANNING`/`COMPLETE`, reload the MAX tab.
   The service worker polls for newly created MAX targets and attaches them
   automatically.

The scan may report `TARGET_ALREADY_ATTACHED`/attach failure if another
debugger owns a target. Disable/remove the extension after the feasibility
check if no longer needed.

## Safety

The extension has no `webRequest`, `cookies`, `scripting`, or storage access to
MAX page data. `debugger` is used only for read-only Runtime/Network commands.
