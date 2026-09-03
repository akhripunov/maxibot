# External smoke evidence — 2026-09-03

Sanitized owner evidence received by QA:

| Case | Result |
|---|---|
| `TC-TRAFFIC-016` Android handoff | PASS — target channel opened |
| `TC-EXT-001` MAX join/member flow | PASS — join completed and member confirmed |

No tokens, cookies, user IDs, raw frames, or personal data are stored here.

The supported-runtime QA evidence remains `603 passed, 0 failed`, with Docker
build, local staging and E2E all PASS. An isolated local production-like
equivalent was then executed: candidate image `c1dae6a32dda` passed deploy and
smoke, rollback image `6a3740d7bdd5` passed rollback and smoke, with DB/Redis
healthy throughout. The live production environment was not accessed.

```text
TC-EXT-002 = VERIFIED_WORKING
PRODUCTION_CHANGED = NO
QA_VERDICT = GO
```
