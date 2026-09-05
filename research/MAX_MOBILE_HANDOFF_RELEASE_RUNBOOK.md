# MAX mobile handoff release runbook

## Deployment invariants and evidence

The release owner is `ARCHITECT CODEX`; infrastructure execution belongs to
`SYSADMIN`. The canonical production host and build platform are
`135.106.166.46` and `linux/amd64`. Native ARM Mac production builds are not
allowed. Before replacement, record the exact source SHA, VPS architecture,
image architecture, disk/volume state, current API/worker images, migration
head, and rollback compose snapshot. Enforce `VPS_ARCH == IMAGE_PLATFORM` and
block on unknown architecture, failed inspection, or source-SHA mismatch.

Use the canonical Compose procedure: exact-SHA QA GO, rollback snapshot,
amd64 image build, `scripts/deploy_platform_guard.py`, image replacement,
health/ready/live checks, API/worker image parity, `MAX_RUN_POLLING=false` on
API and `true` only on the production worker, duplicate-poller checks, fresh
error checks, and frozen CHANNEL regression. Confirm disk space before build
and cleanup only known release artifacts after evidence is retained. Do not
run Direct/VK or campaign mutations; advertising control remains OWNER_ONLY.

Rollback restores the saved Compose file and prior API/worker image tags,
then repeats health, polling, error, parity, and CHANNEL checks. Do not
downgrade an additive database migration as an improvised rollback step.

### SYSADMIN release checklist

```text
EXPECTED_SHA=<40-hex commit approved by ARCHITECT CODEX>
HOST=135.106.166.46
VPS_ARCH=$(docker info --format '{{.Architecture}}')
IMAGE_ARCH=$(docker image inspect <image> --format '{{.Architecture}}')
SOURCE_SHA=$(git rev-parse HEAD)
```

Required evidence before replacement:

- `EXPECTED_SHA`, `SOURCE_SHA`, and the image label/tag match exactly;
- `VPS_ARCH=amd64` and `IMAGE_ARCH=amd64`; unknown or mismatch is BLOCKED;
- `df -h` and Docker disk usage have sufficient free space for build and
  rollback image;
- rollback directory contains the exact Compose file, API/worker image tags,
  and migration head;
- canonical build uses `docker build --platform linux/amd64`; inspect the
  resulting image before `compose up`;
- replacement is limited to the authorized API/worker services.

Required evidence after replacement:

- `/health/live` and `/health/ready` are healthy and report the exact SHA;
- API/worker image IDs and source SHA are equal;
- API `MAX_RUN_POLLING=false`, sole production worker `true`, legacy worker
  stopped with restart policy `no`;
- restart status is healthy, recent logs contain no new traceback/error surge,
  and no duplicate poller exists;
- frozen CHANNEL landing/startapp/Mini App/resolve/open/attribution regression
  passes before Architect CODEX gives the final verdict.

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
