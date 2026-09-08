#!/usr/bin/env python3
"""Fail-closed release retention planner.

The default mode is a report only.  A release is never considered disposable
unless it has a manifest, rollback compose metadata, and is outside the
current/previous plus rollback-safe retention set.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone


@dataclass(frozen=True)
class Release:
    path: Path
    size: int
    mtime: float
    rollback_safe: bool
    reason: str

    @property
    def sha(self) -> str:
        return self.path.name


def _du(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            return 0
    return total


def _running_refs() -> set[str]:
    try:
        out = subprocess.check_output(
            [
                "docker", "inspect", "maxibot-vps-api-1", "--format",
                "{{.Config.Image}}|{{index .Config.Labels \"org.maxibot.release.sha\"}}",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return set()
    image, label = (out.split("|", 1) + [""])[:2]
    refs = {image.rsplit(":", 1)[-1]} if ":" in image else set()
    if label and label != "<no value>":
        refs.add(label)
    return refs


def _release(path: Path) -> Release:
    manifest = path / "release-manifest.json"
    rollback = path / "rollback-compose.vps.yml"
    report = path / "release-report.env"
    if not manifest.is_file() or not rollback.is_file() or not report.is_file():
        return Release(path, _du(path), path.stat().st_mtime, False, "missing_manifest_rollback_or_success_report")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Release(path, _du(path), path.stat().st_mtime, False, "invalid_manifest")
    if data.get("release_id") != path.name or data.get("target_sha") != path.name:
        return Release(path, _du(path), path.stat().st_mtime, False, "manifest_identity_mismatch")
    try:
        report_text = report.read_text(encoding="utf-8")
    except OSError:
        return Release(path, _du(path), path.stat().st_mtime, False, "unreadable_success_report")
    if "FINAL_STATUS=PASS" not in report_text or "HEALTH_RESULT=PASS" not in report_text:
        return Release(path, _du(path), path.stat().st_mtime, False, "release_success_not_proven")
    return Release(path, _du(path), path.stat().st_mtime, True, "identity_rollback_and_success_verified")


def plan(root: Path, keep_successful: int) -> tuple[list[Release], set[Path], list[Release]]:
    releases = sorted((_release(p) for p in root.iterdir() if p.is_dir()), key=lambda item: item.mtime, reverse=True)
    protected: set[Path] = set()
    refs = _running_refs()
    for release in releases:
        if release.path.name in refs:
            protected.add(release.path)
    safe = [release for release in releases if release.rollback_safe]
    protected.update(release.path for release in safe[: max(2, keep_successful)])
    candidates = [release for release in releases if release.path not in protected and release.rollback_safe]
    return releases, protected, candidates


def _age_days(mtime: float) -> int:
    return max(0, int((datetime.now(timezone.utc).timestamp() - mtime) // 86400))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/opt/maxibot/releases"))
    parser.add_argument("--keep-successful", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true", required=True)
    args = parser.parse_args()
    if args.keep_successful < 5:
        print("STATUS=BLOCKED\nBLOCKER=keep_successful_must_be_at_least_5")
        return 2
    if not args.root.is_dir():
        print("STATUS=BLOCKED\nBLOCKER=releases_root_missing")
        return 2
    releases, protected, candidates = plan(args.root, args.keep_successful)
    reclaimable = sum(item.size for item in candidates)
    print(f"RELEASES_TOTAL={len(releases)}")
    print(f"RELEASES_KEEP={len(protected)}")
    print(f"RELEASES_DELETE_CANDIDATES={len(candidates)}")
    print(f"RECLAIMABLE_BYTES={reclaimable}")
    current_paths = {item.path for item in releases if item.path in protected and item.path.name in _running_refs()}
    previous_path = next(
        (item.path for item in releases if item.rollback_safe and item.path not in current_paths),
        None,
    )
    for item in candidates:
        current = item.path in current_paths
        previous = item.path == previous_path
        referenced = current
        rollback_required = current or previous or item.path in protected
        safe = item.rollback_safe and not rollback_required
        reason = item.reason if safe else "protected_or_unproven"
        print(
            "CANDIDATE "
            f"RELEASE={item.path} SHA={item.sha} AGE_DAYS={_age_days(item.mtime)} "
            f"CURRENT={'YES' if current else 'NO'} "
            f"PREVIOUS={'YES' if previous else 'NO'} "
            f"ROLLBACK_REQUIRED={'YES' if rollback_required else 'NO'} "
            f"REFERENCED_BY_RUNNING_CONTAINER={'YES' if referenced else 'NO'} "
            f"SAFE_TO_REMOVE={'YES' if safe else 'NO'} "
            f"BYTES={item.size} REASON={reason}"
        )
    print("MODE=DRY_RUN")
    print("STATUS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
