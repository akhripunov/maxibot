"""Fail-fast checks for the assigned MAXIBOT self-hosted release runner."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request

from prepared_artifact_cache import prune_reclaimable_release_archives
from release_network_retry import NetworkUnavailable, RetryBudget, is_retryable_network_error, retry_network_read

REPOSITORY = "akhripunov/maxibot"
DEPLOY_WORKFLOW = "maxibot-deploy.yml"
EXPECTED_RUNNER = "maxibot-owner-mac"
MIN_FREE_BYTES = 5 * 1024**3
NETWORK_BUDGET = RetryBudget()


class RunnerReadinessError(RuntimeError):
    pass


def ensure_free_disk(runner_temp: Path, reclaim_cache, measure=shutil.disk_usage) -> int:
    """Reclaim only reproducible project release ZIPs, and only when disk is below the hard gate."""
    free_bytes = measure(runner_temp).free
    if free_bytes < MIN_FREE_BYTES:
        reclaim_cache()
        free_bytes = measure(runner_temp).free
    if free_bytes < MIN_FREE_BYTES:
        raise RunnerReadinessError(
            f"Insufficient free disk for release bundle/runtime work "
            f"free_bytes={free_bytes} required_bytes={MIN_FREE_BYTES}")
    return free_bytes


def validate_runner_identity_and_scope(*, runner_name: str, runner_os: str, runner_arch: str,
                                        main_sha: str, candidate_sha: str, active_conflict: bool,
                                        paths_ready: bool, tools_ready: bool,
                                        network_ready: bool) -> dict[str, str]:
    """Reject an ineligible release runner before cache or production checks."""
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


def validate_release_host(*, runner_name: str, runner_os: str, runner_arch: str,
                          free_bytes: int, main_sha: str, candidate_sha: str,
                          active_conflict: bool, paths_ready: bool,
                          tools_ready: bool, network_ready: bool) -> dict[str, str]:
    preflight = validate_runner_identity_and_scope(
        runner_name=runner_name, runner_os=runner_os, runner_arch=runner_arch,
        main_sha=main_sha, candidate_sha=candidate_sha, active_conflict=active_conflict,
        paths_ready=paths_ready, tools_ready=tools_ready, network_ready=network_ready)
    if free_bytes < MIN_FREE_BYTES:
        raise RunnerReadinessError("Insufficient free disk for release bundle/runtime work")
    return {"runner": preflight["runner"], "runner_os": runner_os, "runner_arch": runner_arch,
            "free_bytes": str(free_bytes), "deployment_runtime": "VPS_VIA_SSH", "network": "READY",
            "project_paths": "READY", "conflicting_production_mutation": "NO"}


def probe_deployment_ssh(*, host: str, key: str, run_command=subprocess.run,
                         budget: RetryBudget | None = None,
                         retry_options: dict | None = None) -> bool:
    return _probe_deployment_ssh(host=host, key=key, run_command=run_command,
                                  budget=budget, retry_options=retry_options)


class _TransientSSHNetworkError(RuntimeError):
    pass


def _probe_deployment_ssh(*, host: str, key: str, run_command,
                          budget: RetryBudget | None = None,
                          retry_options: dict | None = None) -> bool:
    if host != "135.106.166.46" or not key or not os.path.isfile(key) or not os.access(key, os.R_OK):
        return False

    transient_markers = (
        "could not resolve", "temporary failure in name resolution", "eai_again",
        "connection timed out", "operation timed out", "connection reset",
        "connection closed", "connection refused", "network is unreachable",
        "no route to host", "banner exchange", "end of file",
    )

    def probe() -> bool:
        try:
            result = run_command(
                ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                 "-o", "ConnectionAttempts=1", f"root@{host}", "printf MAXIBOT_DEPLOYMENT_NETWORK_READY"],
                check=False, capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            raise _TransientSSHNetworkError() from None
        except OSError as error:
            if is_retryable_network_error(error):
                raise _TransientSSHNetworkError() from None
            return False
        if result.returncode == 0:
            return result.stdout.strip() == "MAXIBOT_DEPLOYMENT_NETWORK_READY"
        if result.returncode == 255 and any(marker in (result.stderr or "").lower()
                                            for marker in transient_markers):
            raise _TransientSSHNetworkError() from None
        return False

    try:
        return retry_network_read(probe, "runner_readiness_deployment_ssh",
            lambda error: isinstance(error, _TransientSSHNetworkError),
            budget=budget or NETWORK_BUDGET, **(retry_options or {}))
    except NetworkUnavailable:
        raise RunnerReadinessError("NETWORK_UNAVAILABLE stage=runner_readiness_deployment_ssh") from None


def _request(path: str, token: str, *, opener=urllib.request.urlopen,
             budget: RetryBudget | None = None, retry_options: dict | None = None) -> dict:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    def fetch() -> dict:
        with opener(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise RunnerReadinessError("GitHub API returned invalid JSON")
        return payload

    try:
        return retry_network_read(fetch, "runner_readiness_github_api",
            is_retryable_network_error, budget=budget or NETWORK_BUDGET,
            **(retry_options or {}))
    except NetworkUnavailable:
        raise RunnerReadinessError("NETWORK_UNAVAILABLE stage=runner_readiness_github_api") from None
    except urllib.error.HTTPError as error:
        raise RunnerReadinessError(f"GitHub API request failed (HTTP {error.code})") from None
    except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, ssl.SSLError) as error:
        reason = error.reason if isinstance(error, urllib.error.URLError) else error
        raise RunnerReadinessError(
            f"GitHub API request failed ({type(reason).__name__})") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise RunnerReadinessError("GitHub API returned invalid JSON") from None


def has_conflicting_production_run(workflow_runs, current_run_id: str) -> bool:
    if not isinstance(workflow_runs, list) or any(not isinstance(run, dict) for run in workflow_runs):
        raise RunnerReadinessError("GitHub API returned invalid production workflow data")
    valid_statuses = {"queued", "in_progress", "completed", "waiting", "pending", "requested", "action_required"}
    if any(not str(run.get("id", "")).isdigit() or run.get("status") not in valid_statuses
           for run in workflow_runs):
        raise RunnerReadinessError("GitHub API returned invalid production workflow data")
    return any(str(run.get("id")) != current_run_id and run.get("status") in
               {"queued", "in_progress", "waiting", "pending", "requested"} for run in workflow_runs)


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
    conflict = has_conflicting_production_run(active.get("workflow_runs"), this_run)
    candidate = os.environ.get("CANDIDATE_SHA", "")
    runner_scope_args = dict(
        runner_name=os.environ.get("RUNNER_NAME", ""), runner_os=os.environ.get("RUNNER_OS", ""),
        runner_arch=os.environ.get("RUNNER_ARCH", ""), main_sha=main, candidate_sha=candidate,
        active_conflict=conflict, paths_ready=paths_ready, tools_ready=tools_ready,
        network_ready=network_ready)
    validate_runner_identity_and_scope(**runner_scope_args)
    # Reclaim only our known release artifacts after identity/scope/conflict gates pass.
    free_bytes = ensure_free_disk(
        runner_temp, lambda: prune_reclaimable_release_archives(artifact_cache))
    facts = validate_release_host(free_bytes=free_bytes, **runner_scope_args)
    print("RELEASE_RUNNER_READINESS=PASS " + " ".join(f"{key.upper()}={value}" for key, value in facts.items()))
    return facts


if __name__ == "__main__":
    run()
