# Yandex OAuth and MAX target discovery handoff

## YANDEX_OAUTH

Implemented as an MVP without a database migration:

- one-time OAuth `state` is stored server-side in Redis with a 10-minute TTL;
- state binds the MAX admin external ID and selected channel ID;
- authorization code is exchanged server-side through Yandex OAuth;
- access token is encrypted with the existing application token encryption helper and stored in Redis;
- tokens, codes, client secrets, and authorization URLs containing secrets are not logged or sent to MAX messages;
- production conversion sending remains dry-run.

## TARGET_AUTO_DISCOVERY = BLOCKED

The current official MAX Bot API client exposes chat, membership, and message methods, but no read-only method returning the private native invite URL. Existing MAX Web research observed the UI link object, but no proven callable RPC contract that can safely fetch it for a selected channel. Pending/member objects also contain no invite source field.

Therefore MAXIBOT does not fabricate a target, use another channel's target, or retain manual invite entry as a substitute. Automatic target discovery requires a documented MAX API method or a separately verified read-only Web transport operation.
