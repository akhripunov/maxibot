# MAX mobile handoff release runbook

## Locked production behavior

```text
landing → same opaque startapp → MAX Mini App → /api/traffic/resolve
        → iPhone/iPad: HTTPS channel invite via anchor target=_blank
        → Android: location.replace(target_url)
        → non-Apple MAX Web: WebApp.openMaxLink(target_url)
```

The iPhone branch was proven on 2026-08-21 with two owner recordings: entry
from a MAX chat and entry from external Safari. Both ended in Москва 365.

## Mandatory automated gate

Before every release that changes `app/main.py`, `app/traffic/deep_link.py` or
`miniapp/traffic.html`:

```bash
python -m pytest -q \
  tests/regression/test_mobile_max_handoff_contract.py \
  tests/test_traffic_deep_link.py \
  tests/test_landing_ui_contract.py \
  tests/test_update_payload.py
```

Run with project Python 3.12 (normally inside the candidate Docker image).
The normal `scripts/qa_check.sh` regression phase includes the new test and
must block deploy on any failure.

## Mandatory production acceptance

After a handoff-affecting deploy, use a fresh visit/token for each test:

1. Real iPhone, open the promotion URL from inside MAX.
2. Real iPhone, open the promotion URL in Safari and accept the iOS system
   `Открыть в MAX` prompt.
3. In both flows verify that Mini App opens and then the intended channel
   opens without a Mini App button tap.
4. Check sanitized production evidence: `/mini = 200` and
   `/api/traffic/resolve = 200`; never publish the raw startapp token/user ID.
5. Verify API `MAX_RUN_POLLING=false`, production worker `true`, and the legacy
   worker stopped.

If either real-device flow fails, the release is `BLOCKED`; restore the saved
rollback version rather than trying another handoff variant in production.

## What automation cannot prove

Headless Chrome cannot prove the iOS system handoff or MAX native navigation.
Automated tests protect the exact known-good implementation and server
contract, while the two real-device smokes remain the final acceptance gate.
