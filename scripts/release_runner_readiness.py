#!/usr/bin/env python3
"""Fail-fast readiness gate for the dedicated MAXIBOT Mac release runner."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable, Mapping

GIB = 1024 ** 3
MIN_FREE_BYTES = 10 * GIB
EXPECTED_RUNNER_NAME = "maxibot-owner-mac"


class ReadinessError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str


def _run(args, *, timeout=15):
    result = subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
    return CommandResult(result.returncode, result.stdout.strip())


def check_readiness(env: Mapping[str, str] | None = None, *,
                    free_bytes: int | None = None,
                    command: Callable[..., CommandResult] = _run) -> dict[str, str]:
    env = env or os.environ
    runner = env.get("RUNNER_NAME", "")
    if runner != EXPECTED_RUNNER_NAME:
        raise ReadinessError("runner_name_mismatch")
    if env.get("RUNNER_OS") != "macOS":
        raise ReadinessError("runner_os_mismatch")
    if env.get("RUNNER_ARCH") != "ARM64":
        raise ReadinessError("runner_arch_mismatch")

    try:
        result = command(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        raise ReadinessError("docker_unavailable") from None
    if result.returncode != 0 or not result.stdout:
        raise ReadinessError("docker_unavailable")

    home = Path(env.get("HOME", "")).expanduser()
    if not home.is_absolute() or not str(home):
        raise ReadinessError("home_unavailable")
    cache = home / ".cache" / "maxibot-release" / "artifacts"
    try:
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        resolved_home, resolved_cache = home.resolve(strict=True), cache.resolve(strict=True)
        if resolved_home not in resolved_cache.parents:
            raise ReadinessError("cache_path_escapes_home")
        with tempfile.NamedTemporaryFile(prefix=".readiness-", dir=resolved_cache) as probe:
            probe.write(b"ready")
            probe.flush()
        disk = free_bytes if free_bytes is not None else shutil.disk_usage(resolved_cache).free
    except ReadinessError:
        raise
    except OSError:
        raise ReadinessError("cache_unavailable") from None
    if disk < MIN_FREE_BYTES:
        raise ReadinessError("disk_below_hard_minimum")
    return {"RUNNER_READY": "YES", "DOCKER_READY": "YES", "DISK_READY": "YES",
            "RUNNER_NAME": runner, "RUNNER_ARCH": env["RUNNER_ARCH"],
            "DOCKER_VERSION": result.stdout, "FREE_BYTES": str(disk),
            "CACHE_PATH": str(cache)}


def main() -> int:
    try:
        result = check_readiness()
    except ReadinessError as error:
        print(f"RUNNER_READY=NO REASON={error}")
        return 1
    print(" ".join(f"{key}={value}" for key, value in result.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
