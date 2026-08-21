# Working production release — 2026-08-21

## Checked public promotion flow

- Promotion landing: `https://go.maxibot.pro/80988024303860`
- Target: `Москва 365`
- iPhone from MAX: **PASS**
- iPhone from Safari: **PASS** after the OS-controlled `Открыть в MAX` confirmation
- Mini App → target channel: **PASS**, no Mini App button tap

The recorded working handoff contract is protected by
`tests/regression/test_mobile_max_handoff_contract.py` and
`research/MAX_MOBILE_HANDOFF_RELEASE_RUNBOOK.md`.

## Explicit limitations

- Android code path is covered by automated contract tests, but a real Android
  device E2E smoke has not yet been recorded: **BLOCKED**.
- Do not treat a desktop/headless browser result as proof of iOS/MAX native
  navigation.
- Do not publish environment files, MAX auth frames, cookies or startapp
  tokens.
