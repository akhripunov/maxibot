#!/bin/sh
set -eu
exec /usr/bin/env python3 "$(dirname "$0")/docker_safe_maintenance.py" --host vps "$@"
