# Frozen production paths

`BOT` and `CHANNEL` are separate `PAID_TRAFFIC_CRITICAL` production paths.
The current BOT stable baseline is commit
`e9bf968347356c871dc6a59ac0a86ee872fdd3be`, tagged as
`maxibot-bot-handoff-fallback-stable-2026-09-05`.
The previous BOT baseline remains immutable as the rollback/reference tag
`maxibot-bot-handoff-stable-2026-09-05` at
`9bfc4e04232bfa33bd61139e8ac82f400cd1971f`.

BOT closeout evidence: `IOS_POSTDEPLOY_REAL_DEVICE = PASS`,
`ANDROID_PREVIOUS_STABLE_BASELINE = PASS`,
`ANDROID_POSTDEPLOY_REAL_DEVICE = WAIVED_BY_OWNER`,
`AUTO_REDIRECT = PASS`, `FALLBACK_BUTTON = PASS`,
`REAL_START = PASS`, `BOT_STARTED = PASS`.

This is a behavioral freeze, not a file-only freeze. Refactoring, cleanup,
unification, or changing a shared dependency can alter the paid-traffic
contract even when the named endpoint or file is not directly edited. Such
changes require explicit, task-scoped OWNER approval and the applicable
`FROZEN_PATH_REGRESSION` evidence.

The paths and shared dependencies are machine-readable in
[`frozen_paths.yaml`](frozen_paths.yaml). `scripts/frozen_path_guard.sh` is a
fail-closed pre-review guard:

- unrelated changes pass;
- direct BOT or CHANNEL changes are blocked without matching OWNER approval;
- shared-dependency changes are classified as `POSSIBLE` and blocked pending
  explicit path-scoped review;
- approval is never a broad environment bypass.

`BOT_AND_CHANNEL_SEPARATE = YES`: a BOT change does not authorize a CHANNEL
change, and vice versa. `FAST_PATH_WHEN_UNCHANGED = YES`: untouched frozen
paths do not require ceremonial role work or duplicate regression runs.

## CHANNEL approval contract

The frozen CHANNEL path remains:

```text
Direct → landing → TrafficVisit(channel_id) → token → startapp
→ MAX Mini App → verified MAX user → MaxIdentityLink → channel
→ join request → MANUAL APPROVE → official user_added
→ attribution → max_subscriber → Metrika → Direct
```

`CHANNEL_AUTOAPPROVE_STATUS = DEFERRED`. Manual owner approval is the canonical
production behavior. The only proven programmatic approval mechanism uses an
authenticated human MAX Web/user session; a normal MAX Bot API token is not
proven sufficient, and current onboarding does not provide that session.

MaxiBot must not require a customer to authorize a personal MAX account merely
to enable channel auto-approve. Login, SMS/QR, session or cookie upload,
userbot authorization, and storage of a customer's human session are forbidden
for this purpose. Historical internal-WebSocket evidence may remain in research,
but must not be promoted into customer production behavior.

Auto-approve may be reconsidered only after an official or otherwise technically
proven no-extra-auth bot/admin or partner mechanism is validated for arbitrary
customer channels. Competitor claims alone are insufficient.

Required regression contracts remain listed in the manifest. No runtime source,
landing flow, attribution semantics, migration, or deployment behavior is
changed by this governance artifact.
