#!/bin/sh
set -eu

PROJECT_DIR="/Users/akhripuniov/Downloads/Codex/MAXIBOT"
ENV_FILE="$PROJECT_DIR/.ads_codex_worker.env"
PYTHON="$PROJECT_DIR/.venv312/bin/python"
WORKER="$PROJECT_DIR/scripts/ads_codex_worker.py"

export HOME="/Users/akhripuniov"
export PATH="/Applications/ChatGPT.app/Contents/Resources:/usr/bin:/bin:/usr/sbin:/sbin"
export ADS_CODEX_WORKER_RELEASE_ID="ads-ui-facts-render-fix-20260903-r3"

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
