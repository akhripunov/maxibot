#!/usr/bin/env python3
"""Fail-closed Docker maintenance for local development and MAXIBOT VPS."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()


def docker(*args: str) -> str:
    return run("docker", *args)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--host", choices=("mac", "vps"), required=True)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    p.add_argument("--retention-days", type=int, default=int(os.getenv("UNUSED_IMAGE_RETENTION_DAYS", "14")))
    p.add_argument("--stopped-container-retention-days", type=int, default=int(os.getenv("STOPPED_CONTAINER_RETENTION_DAYS", "7")))
    return p.parse_args()


def image_rows() -> list[dict]:
    raw = docker("image", "ls", "--no-trunc", "--format", "{{json .}}")
    return [json.loads(line) for line in raw.splitlines() if line]


def active_rows() -> list[dict]:
    raw = docker("ps", "-a", "--no-trunc", "--format", "{{json .}}")
    return [json.loads(line) for line in raw.splitlines() if line]


def image_id(ref: str) -> str | None:
    try:
        return docker("image", "inspect", "--format", "{{.Id}}", ref).strip()
    except subprocess.CalledProcessError:
        return None


def refs_from_release_files() -> set[str]:
    roots = [Path("/opt/maxibot/releases"), Path("releases")]
    refs: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in root.glob("**/docker-compose*.yml"):
            for line in path.read_text(errors="replace").splitlines():
                match = re.match(r"\s*image:\s*([^\s#]+)", line)
                if match:
                    ref = match.group(1)
                    # Compose interpolation is not a concrete image reference;
                    # never turn an unresolved optional QA variable into a
                    # production maintenance blocker.
                    if "$" not in ref:
                        refs.add(ref)
    return refs


def protected_image_ids(host: str) -> tuple[set[str], set[str], list[str]]:
    protected_ids: set[str] = set()
    protected_refs: set[str] = set()
    blockers: list[str] = []
    containers = active_rows()
    for row in containers:
        name = row.get("Names", "")
        try:
            data = json.loads(docker("inspect", name))[0]
            protected_ids.add(data["Image"])
            protected_refs.add(data.get("Config", {}).get("Image", ""))
        except (subprocess.CalledProcessError, ValueError, KeyError, IndexError) as exc:
            blockers.append(f"container_inspect:{name}:{type(exc).__name__}")
    if host == "vps":
        refs = refs_from_release_files()
        if not refs:
            blockers.append("rollback_release_images_unresolved")
        protected_refs.update(refs)
        for row in image_rows():
            tag = f"{row.get('Repository')}:{row.get('Tag')}"
            if "rollback" in tag.lower():
                protected_refs.add(tag)
    for ref in protected_refs:
        resolved = image_id(ref)
        if resolved:
            protected_ids.add(resolved)
        elif ref and host == "vps":
            blockers.append(f"protected_image_unresolved:{ref}")
    return protected_ids, protected_refs, blockers


def created_epoch(value: str) -> float | None:
    try:
        return time.mktime(time.strptime(value.split(" ")[0], "%Y-%m-%d"))
    except (ValueError, IndexError):
        return None


def emit(label: str, value: object) -> None:
    print(f"{label}={value}")


def _size_bytes(value: str) -> int:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(B|KB|MB|GB|TB)", value or "", re.I)
    if not match:
        return 0
    return int(float(match.group(1)) * {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}[match.group(2).upper()])


def _df_reclaimable(raw: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        kind = str(row.get("Type", "")).lower().replace(" ", "_")
        result[kind] = _size_bytes(str(row.get("Reclaimable", "")).split(" (")[0])
    return result


def main() -> int:
    args = parse_args()
    if args.retention_days < 1 or args.stopped_container_retention_days < 1:
        emit("MAINTENANCE_BLOCKER", "retention_must_be_positive")
        return 2
    if shutil.which("docker") is None:
        emit("MAINTENANCE_BLOCKER", "docker_unavailable")
        return 2
    try:
        before = docker("system", "df", "--format", "{{json .}}")
        disk_before = shutil.disk_usage("/").used
        images = image_rows()
        containers = active_rows()
        protected_ids, protected_refs, blockers = protected_image_ids(args.host)
    except (subprocess.CalledProcessError, OSError) as exc:
        emit("MAINTENANCE_BLOCKER", f"discovery_failed:{type(exc).__name__}")
        return 2
    now = time.time()
    cutoff = now - args.retention_days * 86400
    candidates: list[tuple[str, str, str]] = []
    skipped = list(blockers)
    for row in images:
        ref = f"{row.get('Repository')}:{row.get('Tag')}"
        ident = row.get("ID", "")
        epoch = created_epoch(row.get("CreatedAt", ""))
        if not ident or ident in protected_ids:
            continue
        if row.get("Repository") in ("<none>", "") or row.get("Tag") in ("<none>", ""):
            continue  # handled only by Docker's dangling-only prune below
        if args.host == "vps" and not ref.startswith("maxibot-vps-api:"):
            skipped.append(f"non_maxibot_image:{ref}")
            continue
        if epoch is None or epoch > cutoff:
            skipped.append(f"too_new_or_unknown:{ref}")
            continue
        candidates.append((ident, ref, row.get("Size", "unknown")))

    stopped: list[str] = []
    if args.host == "mac":
        for row in containers:
            status = row.get("Status", "")
            if status.startswith(("Up", "Created", "Restarting")):
                continue
            # No -v is ever used; ambiguous/non-compose containers are skipped.
            if "com.docker.compose.project" not in docker("inspect", row["Names"], "--format", "{{json .Config.Labels}}"):  # noqa: E501
                skipped.append(f"container_owner_unknown:{row['Names']}")
                continue
            stopped.append(row["Names"])

    emit("HOST", args.host.upper())
    emit("MODE", "DRY_RUN" if args.dry_run else "EXECUTE")
    emit("DOCKER_BYTES_BEFORE", before.replace("\n", ";"))
    emit("DISK_USED_BEFORE", disk_before)
    emit("PROTECTED_IMAGES", len(protected_ids))
    emit("PROTECTED_REFS", len(protected_refs))
    emit("CANDIDATE_IMAGES", len(candidates))
    emit("CANDIDATE_CONTAINERS", len(stopped))
    emit("SKIPPED_AMBIGUOUS", len(skipped))
    reclaimable = _df_reclaimable(before)
    emit("BUILD_CACHE_RECLAIMABLE", reclaimable.get("build_cache", 0))
    emit("UNUSED_IMAGES_CANDIDATES", len(candidates))
    emit("STOPPED_CONTAINER_CANDIDATES", len(stopped))
    # Volumes are never deletion candidates in this tool, even when Docker
    # reports them as unreferenced; ownership and data safety remain unknown.
    emit("VOLUME_CANDIDATES", 0)
    emit("SAFE_RECLAIMABLE_TOTAL", reclaimable.get("build_cache", 0))
    for ident, ref, size in candidates:
        print(f"CANDIDATE_IMAGE id={ident} ref={ref} size={size} reason=unreferenced_older_than_{args.retention_days}d")
    for name in stopped:
        print(f"CANDIDATE_CONTAINER name={name} reason=stopped_older_than_{args.stopped_container_retention_days}d")
    for reason in skipped[:20]:
        print(f"SKIPPED {reason}")
    if blockers:
        emit("STATUS", "BLOCKED")
        return 2
    if args.dry_run:
        emit("STATUS", "PASS")
        return 0

    # These commands cannot touch volumes. Explicit image IDs are removed only
    # after the protections above have been resolved.
    for ident, ref, _ in candidates:
        subprocess.run(["docker", "image", "rm", ident], check=True, text=True)
        print(f"DELETED_IMAGE id={ident} ref={ref}")
    for name in stopped:
        subprocess.run(["docker", "rm", name], check=True, text=True)
        print(f"DELETED_CONTAINER name={name}")
    subprocess.run(["docker", "image", "prune", "-f", "--filter", f"until={args.retention_days * 24}h"], check=True)
    subprocess.run(["docker", "builder", "prune", "-f", "--filter", f"until={args.retention_days * 24}h"], check=True)
    after = docker("system", "df", "--format", "{{json .}}")
    disk_after = shutil.disk_usage("/").used
    emit("DOCKER_BYTES_AFTER", after.replace("\n", ";"))
    emit("DISK_USED_AFTER", disk_after)
    emit("VOLUMES_DELETED", 0)
    emit("STATUS", "PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
