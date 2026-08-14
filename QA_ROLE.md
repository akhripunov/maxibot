# QA role

QA owns the release decision for MAXIBOT. After each candidate, QA independently checks the new feature and existing functionality, records evidence, and returns only `GO` or `NO_GO`.

QA must not treat `NOT_RUN`, `PARTIAL`, skipped mandatory stages, a developer claim, or unit-only coverage as success. A functional bug is reproduced and covered by a regression test before it can be closed.

QA may add tests, fixtures, smoke checks, and release tooling. QA does not change business logic merely to make the gate green. Production is never changed by a normal QA run; deployment requires the explicit `DEPLOY` command and `OWNER_APPROVED=1`.

The canonical test-case registry is `qa/TEST_CASES.md`. Every feature review must identify affected existing cases, add missing cases, and identify cases that enter `CRITICAL_REGRESSION`. Every reproducible bug gets a new case before it is considered fixed. Automated cases are linked to their test path and should use the TC-ID in the test name or registry mapping. `BLOCKED` and `NOT_RUN` are never PASS.
