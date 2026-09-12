#!/usr/bin/env python3
"""Verify prebuilt release images without starting or replacing containers."""

from __future__ import annotations

import subprocess
import sys


def inspect_image(image: str) -> tuple[str, str, str]:
    fmt = (
        "{{.Os}}/{{.Architecture}}|"
        "{{index .Config.Labels \"org.opencontainers.image.revision\"}}|"
        "{{index .Config.Labels \"org.opencontainers.image.source-sha\"}}|"
        "{{index .Config.Labels \"org.maxibot.release.sha\"}}"
    )
    raw = subprocess.check_output(
        ["docker", "image", "inspect", image, "--format", fmt],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()
    platform, revision, source_sha, release_sha = raw.split("|", 3)
    source = source_sha or release_sha or revision
    return platform, source, raw


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: release_image_preflight.py EXPECTED_SHA API_IMAGE WORKER_IMAGE", file=sys.stderr)
        return 2
    expected, api_image, worker_image = argv[1:]
    try:
        api_platform, api_sha, _ = inspect_image(api_image)
        worker_platform, worker_sha, _ = inspect_image(worker_image)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"IMAGE_PREFLIGHT=NO\nIMAGE_PREFLIGHT_REASON=image_inspect_failed:{exc}", file=sys.stderr)
        return 1
    if api_platform != "linux/amd64" or worker_platform != "linux/amd64":
        print("IMAGE_PREFLIGHT=NO\nIMAGE_PREFLIGHT_REASON=wrong_platform", file=sys.stderr)
        return 1
    if api_sha != expected or worker_sha != expected:
        print("IMAGE_PREFLIGHT=NO\nIMAGE_PREFLIGHT_REASON=source_sha_mismatch", file=sys.stderr)
        return 1
    print(f"IMAGE_READY=YES\nAPI_IMAGE_SHA={api_sha}\nWORKER_IMAGE_SHA={worker_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
