#!/bin/sh
set -eu

# Runtime proof gate. This intentionally checks running state, not source files
# or compose declarations. Usage: runtime_release_guard.sh EXPECTED_RELEASE API_URL
EXPECTED_RELEASE=${1:?expected release id required}
API_URL=${2:?api URL required}

READY=$(curl -fsS "$API_URL/health/ready")
printf '%s\n' "$READY" | grep -Fq "\"release_id\":\"$EXPECTED_RELEASE\""
printf '%s\n' "$READY" | grep -Fq "\"release\":\"$EXPECTED_RELEASE\""

if command -v docker >/dev/null 2>&1 && docker inspect maxibot-vps-api-1 >/dev/null 2>&1; then
    API_IMAGE=$(docker inspect maxibot-vps-api-1 --format '{{.Image}}')
    WORKER_IMAGE=$(docker inspect maxibot-vps-worker-1 --format '{{.Image}}')
    test "$API_IMAGE" = "$WORKER_IMAGE"
fi

echo "RUNTIME_RELEASE_PASS=$EXPECTED_RELEASE"
