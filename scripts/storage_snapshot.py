#!/usr/bin/env python3
"""Collect host storage metrics; default operation is a non-mutating dry-run."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path


def du(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            return 0
    return total


def docker_df() -> dict[str, int]:
    try:
        raw = subprocess.check_output(["docker", "system", "df", "--format", "{{json .}}"], text=True)
    except (OSError, subprocess.CalledProcessError):
        return {}
    result: dict[str, int] = {}
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        key = str(row.get("Type", "")).lower().replace(" ", "_")
        value = str(row.get("Size", "0"))
        if value.isdigit():
            result[key] = int(value)
    return result


def postgres_size(query: str) -> int | None:
    try:
        raw = subprocess.check_output(
            ["docker", "exec", "maxibot-vps-db-1", "psql", "-U", "maxibot", "-d", "maxibot", "-Atc", query],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return int(raw) if raw.isdigit() else None
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None


def sample() -> dict[str, object]:
    disk = shutil.disk_usage("/")
    return {
        "timestamp": int(time.time()),
        "disk": {"total": disk.total, "used": disk.used, "free": disk.free},
        "containerd": du(Path("/var/lib/containerd")),
        "releases": du(Path("/opt/maxibot/releases")),
        "postgres": postgres_size("select pg_database_size(current_database())") or du(Path("/var/lib/docker/volumes/maxibot-vps_maxibot_postgres_data/_data")),
        "ads": postgres_size("select coalesce(sum(pg_total_relation_size(c.oid)),0) from pg_class c join pg_namespace n on n.oid=c.relnamespace where c.relkind in ('r','m','t') and (c.relname ilike '%ads%' or c.relname ilike '%campaign%' or c.relname ilike '%conversion%' or c.relname ilike '%promotion%')"),
        "logs": du(Path("/var/log")) + du(Path("/var/lib/docker/containers")),
        "backups": du(Path("/opt/maxibot/backups")) + du(Path("/var/backups")),
        "docker": docker_df(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("/opt/maxibot/storage/storage-samples.jsonl"))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args()
    value = sample()
    print(json.dumps(value, sort_keys=True))
    if args.write:
        args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with args.output.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, sort_keys=True) + "\n")
        args.output.chmod(0o600)
        print("MODE=WRITE")
    else:
        print("MODE=DRY_RUN")
    print("STATUS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
