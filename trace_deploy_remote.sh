#!/bin/sh
set -eu
docker build -t maxibot-vps-api:ads-runtime-trace-20260902 /opt/maxibot/releases/maxibot_ads_demo_fix_20260902
docker run --rm --network maxibot-vps-internal --env-file /opt/maxibot/.env -e MAX_RUN_POLLING=false maxibot-vps-api:ads-runtime-trace-20260902 python -c 'import app.main; print("IMPORT_OK")'
docker tag maxibot-vps-api:ads-runtime-trace-20260902 maxibot-vps-api:20260813-landing1
docker compose -f /opt/maxibot/docker-compose.vps.yml up -d --force-recreate worker
echo TRACE_DEPLOY_DONE=YES
