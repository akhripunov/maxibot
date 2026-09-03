#!/bin/sh
set -eu

RUNTIME_DIR="/Users/akhripuniov/Library/Application Support/MAXIBOT"
ENV_FILE="$RUNTIME_DIR/.ads_codex_worker.env"
PYTHON="$RUNTIME_DIR/.venv312/bin/python"
WORKER="$RUNTIME_DIR/scripts/ads_codex_worker.py"

export HOME="/Users/akhripuniov"
export PATH="/Applications/ChatGPT.app/Contents/Resources:/usr/bin:/bin:/usr/sbin:/sbin"
export ADS_CODEX_WORKER_RELEASE_ID="ads-membership-wording-20260903-r1"

if [ ! -r "$ENV_FILE" ]; then
    echo "WORKER_ENV_MISSING" >&2
    exit 78
fi
if [ ! -x "$PYTHON" ]; then
    echo "WORKER_PYTHON_MISSING" >&2
    exit 78
fi

set -a
. "$ENV_FILE"
set +a
exec "$PYTHON" "$WORKER" --watch
