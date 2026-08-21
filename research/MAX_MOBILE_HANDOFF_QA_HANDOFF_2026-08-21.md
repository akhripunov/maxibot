# MAX mobile handoff QA handoff — 2026-08-21

## Scope

Reviewed the Dev handoff for the locked mobile MAX handoff contract. Production
code, production configuration, VPS `135.106.166.46`, and legacy VPS were not
accessed or changed.

## Registry and regression audit

- `TC-TRAFFIC-013` is present in `qa/TEST_CASES.md`.
- It is included in the Critical Regression set.
- `scripts/qa_check.sh` runs `tests` and `tests/regression`, so the new suite is
  included in the full regression phase.
- Added manual acceptance cases:
  - `TC-TRAFFIC-014` — iPhone from MAX, CRITICAL, MANUAL;
  - `TC-TRAFFIC-015` — iPhone from Safari, CRITICAL, MANUAL;
  - `TC-TRAFFIC-016` — Android real-device smoke, CRITICAL, MANUAL, UNKNOWN/BLOCKED.

## Documentation

The mandatory checklist is recorded in `QA_PROCESS.md` and `QA_ROLE.md`:

1. fresh iPhone from MAX smoke;
2. fresh iPhone from Safari smoke;
3. separate Android manual smoke, BLOCKED until real Android E2E.

## Executed evidence

- Docker Python 3.12 targeted suite: **39 passed**.
- Included:
  `tests/regression/test_mobile_max_handoff_contract.py`,
  `tests/test_traffic_deep_link.py`,
  `tests/test_landing_ui_contract.py`,
  `tests/test_update_payload.py`.
- Full gate attempted twice and a manual current-image build attempted once.

## Blockers

The full current-candidate Docker build is **BLOCKED** by repeated TLS timeout
or EOF while resolving `python:3.12-slim` from Docker Hub. Therefore the full
`scripts/qa_check.sh` result is **NO_GO** and cannot be called PASS.

The already-existing local image was not used as a substitute for the current
candidate. A supplemental pytest run in that stale image produced 219 passed
and 2 failures: missing `PIL` in the stale image and one stale source-string
expectation in `tests/test_runtime_entrypoints.py`. These results are not used
as the current release verdict.

The Android manual case remains `UNKNOWN/BLOCKED` until a real Android device
opens the target channel. The two iPhone recordings supplied in the Dev handoff
are recorded as evidence for `TC-TRAFFIC-014` and `TC-TRAFFIC-015`; they were
not replayed by this QA session.

## Verdict

```text
QA_VERDICT = NO_GO
PRODUCTION_CHANGED = NO
```

Next action: restore Docker registry access, rebuild the candidate image from
the current source, rerun `./scripts/qa_check.sh`, and attach sanitized fresh
iPhone MAX/Safari evidence plus real Android evidence before considering GO.
