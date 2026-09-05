#!/bin/sh
set -eu
EXPECTED_SHA=${1:?expected source SHA required}
IMAGE="maxibot-vps-api:${EXPECTED_SHA}"
docker build --platform linux/amd64 -t "$IMAGE" /opt/maxibot/releases/maxibot_ads_demo_fix_20260902
python3 /opt/maxibot/scripts/deploy_platform_guard.py "$EXPECTED_SHA" "$IMAGE"
docker run --rm --platform linux/amd64 --network maxibot-vps-internal --env-file /opt/maxibot/.env -e MAX_RUN_POLLING=false "$IMAGE" python -c 'import app.main; print("IMPORT_OK")'
docker compose -f /opt/maxibot/docker-compose.vps.yml up -d --force-recreate worker
echo TRACE_DEPLOY_DONE=YES
