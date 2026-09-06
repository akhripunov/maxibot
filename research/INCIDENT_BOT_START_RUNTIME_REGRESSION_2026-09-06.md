# BOT start runtime regression — 2026-09-06

## Root cause

The observability change introduced a runtime reference to `BotTrafficEvent`
inside `MenuRepository.bind_system_startapp()` without importing that model.
The production worker received the real `bot_started` update, then raised
`NameError` before the normal home menu was sent.

## Why tests missed it

The previous tests checked observability classification and source contracts,
but did not execute the real `bot_started` → `bind_system_startapp()` → menu
path with the observability code enabled. The missing import therefore escaped
collection/execution coverage.

## Why the frozen-path guard missed it

The frozen-path guard validates changed-path scope and OWNER approval. It is
not an import/runtime execution test and does not catch a missing Python model
import. The bounded QA suite now covers the runtime entrypoint and menu handoff.

## Permanent prevention

`tests/test_bot_start_menu_regression.py` executes the real bot-start handler
shape, asserts automatic home-menu rendering, and verifies that the
`bot_started` conversion remains guarded by the existing single-record check.
