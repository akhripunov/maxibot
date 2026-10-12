# MAXIBOT Release Orchestrator V3 — Impact-Based CI

Status: local candidate; requires GitHub FULL Prepare, canonical preflight and
separate exact-SHA / exact-Alembic owner authorization for production.

## Canonical workflow and fail-closed source selection

MAXIBOT V3 keeps coalescing, frozen paid-traffic guard, immutable Prepare,
production Docker 29.1.3, exact SHA and API/Worker identity parity, private
containerd rehearsal, Alembic target approval, backup/rollback, ADS write and
autopilot OFF guards, Worker cgroup/heartbeat verification and postdeploy evidence.

The impact selector obtains the most recent *immutable verified* MAXIBOT
production Deploy evidence, rather than the last Git commit or a manually
authored Linear state. It rejects a newer failed/in-progress production attempt.
The entire source diff from proven production to candidate determines the scope.

Only these paths can qualify as UI-only:
- miniapp/index.html
- miniapp/owner.css
- miniapp/style.css
- miniapp/tuning.css

UI-only candidates still execute fixed targeted UI, frozen-path, release asset,
migration, worker health, platform, provenance and V3 tests, build a fresh image
for the exact candidate, and complete private containerd rehearsal and all
production guards. Other paths, including miniapp/app.js and miniapp/traffic.html,
backend, payments, advertising APIs, migrations, Worker, Docker, shared config,
CI, tests, docs and unknown changes always require FULL.

Receipt records test_scope and an immutable impact plan for UI-only. Production
runner recomputes the full Git diff and refuses Deploy if exact source proof
does not match, or if live production SHA differs from claimed baseline.
Old V2 receipts are treated as FULL only. Missing evidence fails closed.

## Docker optimization

BuildKit uses repository-scoped GHA cache of Docker build *layers*, ignoring
cache-export failures only. Because dependencies are installed before final
COPY, repeated builds with stable dependencies can reuse those layers. Every
Prepare still creates an exact-SHA linux/amd64 image with correct labels and
a fresh immutable archive; no old image is relabeled as a new SHA.

## Evidence and rollout

The first V3 infrastructure change necessarily runs FULL. Branch-only
workflow_dispatch rehearsal is read-only with no release publication, Linear
mutation, or production Deploy. The main Prepare and immutable private
rehearsal, automatic preflight and separately approved production Deploy remain
required. No UI-only speedup percentage is asserted until measured on a real
later UI-only update with V3 established in production.

Test with Python pytest test_maxibot_impact_v3.py, test_release_prod_tooling.py,
full MAXIBOT suite in GitHub branch and main, frozen path guard and exact image
private rehearsal. Document release status in MAXIBOT Rules, Journal and the
shared Release Queue. Do not create new ME issues just to record the release.
GPoint's production V3 is unaffected; MAXIBOT requires its own deployment.
