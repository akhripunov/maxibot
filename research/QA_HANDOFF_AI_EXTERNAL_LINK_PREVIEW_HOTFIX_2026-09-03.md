# QA handoff — AI external-link preview hotfix — 2026-09-03

## Scope

Reviewed candidate `2f31bb975ff597e8c10c41d0ef489225bd0597b8` and the Dev
hotfix in `app/bot/menu.py` plus
`tests/test_ads_ai_external_link_preview.py`. No production, live DB, Direct,
VK, or Git history was changed.

## Hotfix evidence

Targeted Python 3.12 Docker run:

```text
23 passed
```

Covered:

- Dzen/arbitrary HTTPS URL removal from user-facing AI prose;
- removal of Markdown external-link preview syntax;
- preservation of internal provenance data;
- preservation of existing AI promotion buttons;
- existing AI/UI and promotion contracts.

The case is registered as `TC-ADS-001` in `qa/TEST_CASES.md`.

## Canonical QA gate

```text
STATIC = PASS
BUILD = PASS
UNIT = FAIL
TOTAL_TESTS = 391
PASSED = 378
FAILED = 13
SKIPPED = 0
INTEGRATION = NOT_RUN
REGRESSION = NOT_RUN
STAGING = NOT_RUN
E2E = NOT_RUN
QA_VERDICT = NO_GO
```

The 13 failures are existing current-branch MAXIBOT/UI/runtime contract
incompatibilities outside the AI sanitizer (main-menu copy, owner promotion,
Ads worker source contract, banner dependency/source contract, public-site
copy, and runtime admin/navigation contracts). They require Dev triage and
must not be hidden by weakening the release gate.

## Manual rendering blocker

Actual MAX rendering of the user-facing AI card was not executed: no isolated
MAX owner/browser session was available in this QA run. Stub and source-level
tests are not evidence of live MAX rendering. A fresh manual MAX smoke is
required to verify that no external preview remains and provenance/buttons are
still rendered.

```text
PRODUCTION_CHANGED = NO
QA_VERDICT = NO_GO
```
