#!/usr/bin/env bash
set -euo pipefail

SHA="${1:?exact source SHA required}"
IMAGE="${TELEGRAM_EXECUTOR_IMAGE:-maxibot-telegram-executor:${SHA}}"
docker build --platform linux/amd64 --build-arg RELEASE_SHA="$SHA" --label org.maxibot.release.sha="$SHA" -f deploy/telegram-executor/Dockerfile -t "$IMAGE" .
test "$(docker image inspect "$IMAGE" --format '{{index .Config.Labels "org.maxibot.release.sha"}}')" = "$SHA"
test "$(docker image inspect "$IMAGE" --format '{{.Os}}/{{.Architecture}}')" = linux/amd64
echo "EXECUTOR_IMAGE=$IMAGE"
echo "EXECUTOR_IMAGE_SHA=$SHA"
echo "EXECUTOR_IMAGE_PLATFORM=linux/amd64"
