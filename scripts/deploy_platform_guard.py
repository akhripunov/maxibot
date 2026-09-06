#!/usr/bin/env python3
"""Fail-closed pre-replacement guard for canonical MAXIBOT deploys."""

from __future__ import annotations

import subprocess
import sys


def normalize_architecture(arch: str) -> str:
    return {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(arch.strip().lower(), arch.strip().lower())


def validate(vps_arch: str, image_arch: str, source_sha: str, expected_sha: str) -> None:
    values = {
        "VPS_ARCH": vps_arch,
        "IMAGE_ARCH": image_arch,
        "SOURCE_SHA": source_sha,
        "EXPECTED_SHA": expected_sha,
    }
    if any(not value or value.upper() in {"UNKNOWN", "UNVERIFIED"} for value in values.values()):
        raise RuntimeError("DEPLOY_PLATFORM_GUARD_BLOCKED=unknown platform or source SHA")
    if normalize_architecture(vps_arch) != normalize_architecture(image_arch):
        raise RuntimeError("DEPLOY_PLATFORM_GUARD_BLOCKED=platform mismatch")
    if source_sha != expected_sha:
        raise RuntimeError("DEPLOY_PLATFORM_GUARD_BLOCKED=source SHA mismatch")


def _run(*args: str) -> str:
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"DEPLOY_PLATFORM_GUARD_BLOCKED=inspect failure: {args[0]}") from exc


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: deploy_platform_guard.py EXPECTED_SHA IMAGE", file=sys.stderr)
        return 2
    expected_sha, image = argv[1:]
    try:
        vps_arch = _run("docker", "info", "--format", "{{.Architecture}}")
        image_arch = _run("docker", "image", "inspect", image, "--format", "{{.Architecture}}")
        source_sha = _run("git", "rev-parse", "HEAD")
        validate(vps_arch, image_arch, source_sha, expected_sha)
        if normalize_architecture(vps_arch) != "amd64":
            raise RuntimeError("DEPLOY_PLATFORM_GUARD_BLOCKED=canonical platform must be amd64")
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"DEPLOY_PLATFORM_GUARD_PASS=amd64 SHA={expected_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
