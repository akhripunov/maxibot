# Handoff: Yandex offline conversion Target contract

## Root cause

The controlled upload was accepted at the upload layer, but Yandex marked it
`Не привязан`: the CSV `Target` contained the internal numeric goal ID. The
counter has an active action goal named `userDidSubscribe`; for this offline
conversion contract `Target` must be the goal/event identifier used by the
counter, namely `userDidSubscribe`, not the numeric management ID.

This is an upload-format defect, not an attribution defect. The existing
failed upload cannot be repaired in place because its CSV Target is immutable.

## Change

- `YandexMetricaClient.upload_offline_conversion` now accepts a goal identifier.
- The sender verifies/creates the action goal by name and sends
  `Target=userDidSubscribe`.
- The one-row controlled retry script uses the goal name and keeps the queue in
  dry-run mode.
- Added a regression contract preventing a numeric goal ID from being used by
  the sender/retry path.

## Safety

- No new conversion was sent in this change.
- Existing failed uploads were not modified or resent.
- Mass sender remains disabled/dry-run.
- Attribution, landing, membership, Mini App and counter selection are
  unchanged.

## Verification

- `py_compile`: PASS for changed application, script and test files.
- Focused pytest in the Python 3.12 production image: `11 passed`.
- Local `py_compile`: PASS, including the new contract test.
- Production API deploy: PASS on `135.106.166.46`; API health is ready.
- Production flags after deploy: API `MAX_RUN_POLLING=false`, sole worker
  `MAX_RUN_POLLING=true`; worker was not recreated.
- Runtime verification: the active API container contains
  `ensure_action_goal_identifier` and the retry script uses the goal name.

## Next step

One explicitly approved controlled upload was sent after deploy. Sanitized
result: counter `111595093`, goal identifier `userDidSubscribe`, upload id
`1186156497`, API/upload status `UPLOADED`, no reported error. Yandex documents
that processing can take up to two hours, so linkage is still pending and must
be checked later. `UPLOADED` is not yet proof of `MATCHED`/`PROCESSED`.
The mass sender remains disabled.
