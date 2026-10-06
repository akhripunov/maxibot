"""Fail-fast checks for the assigned MAXIBOT self-hosted release runner."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

from prepared_artifact_cache import prune_reclaimable_release_archives

REPOSITORY = "akhripunov/maxibot"
DEPLOY_WORKFLOW = "maxibot-deploy.yml"
EXPECTED_RUNNER = "maxibot-owner-mac"
MIN_FREE_BYTES = 5 * 1024**3


class RunnerReadinessError(RuntimeError):
    pass


def ensure_free_disk(runner_temp: Path, reclaim_cache, measure=shutil.disk_usage) -> int:
    """Reclaim only reproducible project release ZIPs, and only when disk is below the hard gate."""
    free_bytes = measure(runner_temp).free
    if free_bytes < MIN_FREE_BYTES:
        reclaim_cache()
        free_bytes = measure(runner_temp).free
    if free_bytes < MIN_FREE_BYTES:
        raise RunnerReadinessError("Insufficient free disk for release bundle/runtime work")
    return free_bytes


def inspect_docker_runtime(run_command=subprocess.run, timeout: float = 15) -> tuple[str, str]:
    """Classify Docker without starting services or touching the release cache."""
    info_command = ["docker", "info", "--format", "{{.OSType}}"]
    try:
        result = run_command(info_command, check=False, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return "", "unavailable"
    output = result.stdout.strip()
    if result.returncode == 0:
        return output, "ready" if output == "linux" else "wrong_platform"
    error = result.stderr.strip().lower()
    if "is the docker daemon running" in error or "cannot connect to the docker daemon" in error:
        return "", "stopped"
    return "", "unavailable"


def resolve_docker_os(run_command=subprocess.run, sleep=time.sleep, monotonic=time.monotonic,
                      startup_timeout_seconds: int = 90,
                      initial_probe: tuple[str, str] | None = None) -> str:
    """Use a running Linux Docker engine, or start installed Desktop once and verify it boundedly."""
    probe = initial_probe or inspect_docker_runtime(run_command=run_command)
    docker_os, state = probe
    if state == "ready":
        return docker_os
    if state != "stopped":
        return ""

    try:
        started = run_command(["docker", "desktop", "start"], check=False,
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return ""
    if started.returncode != 0:
        return ""

    deadline = monotonic() + max(0, startup_timeout_seconds)
    while monotonic() < deadline:
        remaining = max(0, deadline - monotonic())
        docker_os, state = inspect_docker_runtime(run_command=run_command, timeout=min(5, remaining))
        if state == "ready":
            return docker_os
        if state != "stopped":
            return ""
        remaining = max(0, deadline - monotonic())
        if remaining <= 0:
            break
        sleep(min(2, remaining))
    return ""


def validate_pre_docker_start(*, runner_name: str, runner_os: str, runner_arch: str,
                              main_sha: str, candidate_sha: str, active_conflict: bool,
                              paths_ready: bool, tools_ready: bool, network_ready: bool) -> dict[str, str]:
    """Reject an ineligible run before starting or otherwise touching Docker Desktop."""
    if runner_name != EXPECTED_RUNNER or runner_os != "macOS" or runner_arch != "ARM64":
        raise RunnerReadinessError("Assigned runner identity/OS/architecture does not match MAXIBOT lane")
    if not paths_ready:
        raise RunnerReadinessError("MAXIBOT cache/runtime paths are unavailable or unsafe")
    if not tools_ready:
        raise RunnerReadinessError("Required release command-line tools are unavailable")
    if not network_ready:
        raise RunnerReadinessError("Canonical deployment SSH network is unavailable")
    if not candidate_sha or main_sha != candidate_sha:
        raise RunnerReadinessError("Candidate is no longer exact current main")
    if active_conflict:
        raise RunnerReadinessError("Conflicting MAXIBOT production mutation is active or queued")
    return {"runner": runner_name, "runner_os": runner_os, "runner_arch": runner_arch,
            "candidate_sha": candidate_sha, "conflicting_production_mutation": "NO"}


def validate_runtime(*, runner_name: str, runner_os: str, runner_arch: str,
                     free_bytes: int, docker_available: bool, docker_os: str,
                     main_sha: str, candidate_sha: str, active_conflict: bool,
                     paths_ready: bool, tools_ready: bool, network_ready: bool) -> dict[str, str]:
    preflight = validate_pre_docker_start(
        runner_name=runner_name, runner_os=runner_os, runner_arch=runner_arch,
        main_sha=main_sha, candidate_sha=candidate_sha, active_conflict=active_conflict,
        paths_ready=paths_ready, tools_ready=tools_ready, network_ready=network_ready)
    if free_bytes < MIN_FREE_BYTES:
        raise RunnerReadinessError("Insufficient free disk for release bundle/runtime work")
    if not docker_available or docker_os != "linux":
        raise RunnerReadinessError("Required Docker runtime is unavailable")
    return {"runner": preflight["runner"], "runner_os": runner_os, "runner_arch": runner_arch,
            "free_bytes": str(free_bytes), "docker": "READY", "network": "READY",
            "project_paths": "READY", "conflicting_production_mutation": "NO"}


def probe_deployment_ssh(*, host: str, key: str, run_command=subprocess.run) -> bool:
    if host != "135.106.166.46" or not key or not os.path.isfile(key) or not os.access(key, os.R_OK):
        return False
    try:
        result = run_command(
            ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             "-o", "ConnectionAttempts=1", f"root@{host}", "printf MAXIBOT_DEPLOYMENT_NETWORK_READY"],
            check=False, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "MAXIBOT_DEPLOYMENT_NETWORK_READY"


def _request(path: str, token: str) -> dict:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RunnerReadinessError(f"GitHub network/API readiness failed ({type(error).__name__})") from None


def _safe_writable_path(path: Path, home: Path) -> bool:
    try:
        resolved_home = home.resolve(strict=True)
        path.mkdir(parents=True, exist_ok=True)
        resolved = path.resolve(strict=True)
        if resolved != resolved_home and resolved_home not in resolved.parents:
            return False
        with tempfile.NamedTemporaryFile(prefix=".release-readiness-", dir=resolved, delete=True):
            return True
    except OSError:
        return False


def release_artifact_cache_path(home: Path) -> Path:
    """Resolve the canonical cache path while rejecting symlinks in every component."""
    try:
        current = home.resolve(strict=True)
        for component in (".cache", "maxibot-release", "artifacts"):
            current = current / component
            try:
                current.lstat()
            except FileNotFoundError:
                return current
            if current.is_symlink() or not current.is_dir():
                raise RunnerReadinessError("MAXIBOT release cache path contains an unsafe symlink or non-directory")
        return current
    except OSError as error:
        raise RunnerReadinessError("MAXIBOT release cache path is unavailable") from error


def run() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RunnerReadinessError("Existing workflow GITHUB_TOKEN is unavailable")
    home = Path.home()
    runner_temp = Path(os.environ.get("RUNNER_TEMP", ""))
    cache = home / ".cache" / "maxibot-release"
    artifact_cache = release_artifact_cache_path(home)
    paths_ready = bool(runner_temp) and _safe_writable_path(runner_temp, home) and _safe_writable_path(cache, home)
    tools_ready = all(shutil.which(binary) for binary in ("git", "bash", "ssh", "scp"))
    production_host = os.environ.get("PRODUCTION_HOST", os.environ.get("MAXIBOT_PRODUCTION_HOST", "135.106.166.46"))
    ssh_key = os.environ.get("PRODUCTION_SSH_KEY", os.environ.get(
        "MAXIBOT_PRODUCTION_SSH_KEY", "/Users/akhripuniov/.ssh/maxibot_selectel_ed25519"))
    network_ready = probe_deployment_ssh(host=production_host, key=ssh_key)
    main = _request("git/ref/heads/main", token).get("object", {}).get("sha", "")
    active = _request(f"actions/workflows/{DEPLOY_WORKFLOW}/runs?branch=main&per_page=100", token)
    this_run = os.environ.get("GITHUB_RUN_ID", "")
    conflict = any(str(run.get("id")) != this_run and run.get("status") in
                   {"queued", "in_progress", "waiting", "pending"}
                   for run in active.get("workflow_runs", []))
    candidate = os.environ.get("CANDIDATE_SHA", "")
    pre_docker_args = dict(
        runner_name=os.environ.get("RUNNER_NAME", ""), runner_os=os.environ.get("RUNNER_OS", ""),
        runner_arch=os.environ.get("RUNNER_ARCH", ""), main_sha=main, candidate_sha=candidate,
        active_conflict=conflict, paths_ready=paths_ready, tools_ready=tools_ready,
        network_ready=network_ready)
    validate_pre_docker_start(**pre_docker_args)
    docker_probe = inspect_docker_runtime()
    if docker_probe[1] not in {"ready", "stopped"}:
        raise RunnerReadinessError("Required Docker runtime is unavailable")
    # Reclaim only our known release artifacts after identity/scope/conflict gates pass,
    # and verify Docker is usable or its installed Desktop is stopped before reclaiming.
    ensure_free_disk(runner_temp, lambda: prune_reclaimable_release_archives(artifact_cache))
    docker = resolve_docker_os(initial_probe=docker_probe)
    readiness_args = dict(
        runner_name=pre_docker_args["runner_name"], runner_os=pre_docker_args["runner_os"],
        runner_arch=pre_docker_args["runner_arch"],
        docker_available=bool(docker), docker_os=docker, main_sha=main,
        candidate_sha=candidate, active_conflict=conflict,
        paths_ready=paths_ready, tools_ready=tools_ready, network_ready=network_ready)
    # Validate runner identity, dependencies, exact candidate and conflicts
    # before touching even the bounded, reproducible release cache.
    validate_runtime(free_bytes=MIN_FREE_BYTES, **readiness_args)
    free_bytes = ensure_free_disk(
        runner_temp, lambda: prune_reclaimable_release_archives(artifact_cache))
    facts = validate_runtime(free_bytes=free_bytes, **readiness_args)
    print("RELEASE_RUNNER_READINESS=PASS " + " ".join(f"{key.upper()}={value}" for key, value in facts.items()))
    return facts


if __name__ == "__main__":
    run()
