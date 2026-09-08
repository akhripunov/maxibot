#!/bin/sh
set -eu
export PATH="/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources:/usr/bin:/bin:/usr/sbin:/sbin"
exec /usr/bin/env python3 "$(dirname "$0")/docker_safe_maintenance.py" --host mac "$@"
