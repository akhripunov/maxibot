# Frozen production paths

`BOT` and `CHANNEL` are separate `PAID_TRAFFIC_CRITICAL` production paths.
Their current stable baseline is commit
`9bfc4e04232bfa33bd61139e8ac82f400cd1971f`, tagged as
`maxibot-bot-handoff-stable-2026-09-05`.

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

Required regression contracts remain listed in the manifest. No runtime source,
landing flow, attribution semantics, migration, or deployment behavior is
changed by this governance artifact.
