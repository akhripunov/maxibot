#!/usr/bin/env python3
"""Fail-closed validation for a manually requested MAXIBOT release run."""

from __future__ import annotations

import json
import math
import os
import re
import socket
import ssl
import sys
import tempfile
from pathlib import Path
import urllib.error
import urllib.request
from typing import Any

from release_network_retry import NetworkUnavailable, RetryBudget, retry_network_read
from release_assets import PREPARE_ASSET_NAME, ReleaseAssetError, release_tag, resolve_asset

REPO = "akhripunov/maxibot"
WORKFLOW = "maxibot-prepare.yml"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MIGRATION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
NETWORK_BUDGET = RetryBudget()


def _retryable_github_error(error: BaseException) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code in {408, 425, 429, 500, 502, 503, 504}
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, ssl.SSLCertVerificationError):
        return False
    if isinstance(reason, ssl.SSLError):
        return not any(marker in str(reason).lower() for marker in (
            "certificate_verify_failed", "wrong_version_number", "protocol_version"))
    return isinstance(reason, (TimeoutError, socket.timeout, socket.gaierror, ConnectionError))


class ValidationError(RuntimeError):
    pass


def github_get(path: str, token: str, opener=urllib.request.urlopen, *,
               budget: RetryBudget | None = None, retry_options: dict[str, Any] | None = None) -> Any:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    def fetch() -> Any:
        with opener(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    try:
        return retry_network_read(fetch, "release_provenance", _retryable_github_error,
                                  budget=budget or NETWORK_BUDGET, **(retry_options or {}))
    except NetworkUnavailable:
        raise ValidationError("NETWORK_UNAVAILABLE stage=release_provenance") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValidationError("GitHub release provenance could not be verified") from exc
    except urllib.error.HTTPError as exc:
        raise ValidationError(f"GitHub release provenance request failed (HTTP {exc.code})") from None


def validate(values: dict[str, str], api_get=github_get,
             evidence_reader=None) -> dict[str, str]:
    sha = values.get("CANDIDATE_SHA", "")
    mode = values.get("RELEASE_MODE", "").strip()
    migration = values.get("TARGET_MIGRATION", "")
    approve = values.get("APPROVE_PRODUCTION", "false").strip().lower()
    token = values.get("GITHUB_TOKEN", "")
    expected_prepare_run_id = values.get("PREPARE_RUN_ID", "").strip()
    expected_prepare_run_attempt = values.get("PREPARE_RUN_ATTEMPT", "").strip()
    orchestrator_run_id = values.get("ORCHESTRATOR_RUN_ID", "").strip()
    if orchestrator_run_id and not orchestrator_run_id.isdigit():
        raise ValidationError("Automatic orchestrator run ID must be an integer")
    if bool(expected_prepare_run_id) != bool(expected_prepare_run_attempt):
        raise ValidationError("Expected Prepare run ID and attempt must be supplied together")
    if expected_prepare_run_id and (not expected_prepare_run_id.isdigit()
                                    or not expected_prepare_run_attempt.isdigit()):
        raise ValidationError("Expected Prepare run ID and attempt must be positive integers")
    if not SHA_RE.fullmatch(sha):
        raise ValidationError("A full 40-character lowercase candidate SHA is required")
    if mode not in {"preflight", "production", "publication-recovery"}:
        raise ValidationError("An explicit preflight, production, or publication-recovery mode is required")
    if not MIGRATION_RE.fullmatch(migration):
        raise ValidationError("An explicit Alembic target revision is required")
    if approve not in {"true", "false"}:
        raise ValidationError("Production approval input is invalid")
    if mode == "production" and approve != "true":
        raise ValidationError("Production mode requires explicit owner approval")
    if mode == "preflight" and approve == "true":
        raise ValidationError("Preflight must not carry production approval")
    recovery_run_id = values.get("RECONCILES_DEPLOY_RUN_ID", "").strip()
    recovery_attempt = values.get("RECONCILES_DEPLOY_RUN_ATTEMPT", "").strip()
    if mode == "publication-recovery":
        if (not recovery_run_id.isdigit() or not recovery_attempt.isdigit() or approve != "false"
                or values.get("APPROVE_WORKER_BOOTSTRAP", "false").lower() != "false"):
            raise ValidationError("Publication recovery requires an exact original Deploy run/attempt and no production approval")
    elif recovery_run_id or recovery_attempt:
        raise ValidationError("Original Deploy identity is only valid for publication recovery")
    if values.get("APPROVE_WORKER_BOOTSTRAP", "false").lower() not in {"true", "false"}:
        raise ValidationError("Worker bootstrap approval input is invalid")
    if not token:
        raise ValidationError("GitHub token is required for release provenance")

    branch = api_get("git/ref/heads/main", token)
    main_sha = (branch.get("object") or {}).get("sha", "")
    if main_sha != sha:
        raise ValidationError("Candidate must still be the exact current origin/main SHA")

    runs = api_get(f"actions/workflows/{WORKFLOW}/runs?branch=main&head_sha={sha}&per_page=100", token)
    matching = [run for run in runs.get("workflow_runs", []) if run.get("head_sha") == sha]
    if not matching:
        raise ValidationError("No Prepare run exists for this exact candidate")
    latest = max(matching, key=lambda run: (run.get("run_number", 0), run.get("run_attempt", 0)))
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise ValidationError("The latest Prepare run for this candidate is not successful")
    if latest.get("event") not in {"push", "workflow_dispatch"} or latest.get("head_branch") != "main":
        raise ValidationError("Prepare run did not come from trusted main")
    latest_run_id = str(latest.get("id", ""))
    latest_run_attempt = str(latest.get("run_attempt", 1))
    if expected_prepare_run_id and (
        expected_prepare_run_id != latest_run_id
        or expected_prepare_run_attempt != latest_run_attempt
    ):
        raise ValidationError(
            "Latest successful Prepare run/attempt changed after artifact selection; restart Deploy"
        )
    attempt = latest.get("run_attempt", 1)
    tag = release_tag("prepare", sha, latest_run_id, attempt)
    try:
        asset = resolve_asset(
            "prepare", sha, latest_run_id, attempt, PREPARE_ASSET_NAME,
            lambda route: api_get(route, token),
        )
    except (ReleaseAssetError, ValidationError) as exc:
        raise ValidationError(f"Exact immutable Prepare Release is not valid: {exc}") from exc

    preflight = None
    if mode == "production":
        preflight = _verify_ready_preflight(
            sha, migration, latest_run_id, latest_run_attempt, token, api_get,
            evidence_reader or _read_preflight_evidence,
        )

    if mode == "publication-recovery":
        prior = api_get(f"actions/runs/{recovery_run_id}", token)
        if (str(prior.get("id")) != recovery_run_id
                or prior.get("head_sha") != sha
                or prior.get("head_branch") != "main"
                or prior.get("path") != ".github/workflows/maxibot-deploy.yml"
                or str(prior.get("run_attempt", "1")) != recovery_attempt
                or prior.get("event") != "workflow_dispatch"
                or prior.get("status") != "completed"
                or prior.get("conclusion") != "failure"
                or not str(prior.get("display_title", "")).startswith(f"MAXIBOT production {sha} by ")):
            raise ValidationError("Publication recovery does not match a failed prior trusted production Deploy for this exact SHA/attempt")

    return {
        "candidate": sha,
        "target_migration": migration,
        "mode": mode,
        "prepare_release_tag": tag,
        "prepare_release_asset_id": str(asset["id"]),
        "prepare_release_asset_sha256": asset["digest"][7:],
        "prepare_run_id": latest_run_id,
        "prepare_run_attempt": latest_run_attempt,
        "prepare_url": str(latest.get("html_url", "")),
        "reconciles_deploy_run_id": recovery_run_id,
        "reconciles_deploy_run_attempt": recovery_attempt,
        "ready_preflight_run_id": preflight["run_id"] if preflight else "",
        "ready_preflight_run_attempt": preflight["run_attempt"] if preflight else "",
        "orchestrator_run_id": orchestrator_run_id,
    }


def _read_preflight_evidence(sha: str, run_id: str, attempt: str, token: str,
                             api_get) -> tuple[dict, dict]:
    """Read and verify the immutable evidence for one completed Deploy run."""
    try:
        from release_assets import read_evidence
        with tempfile.TemporaryDirectory(prefix="maxibot-ready-preflight-") as directory:
            manifest = read_evidence(sha, run_id, attempt, Path(directory),
                                     lambda route: api_get(route, token), token)
            result = json.loads((Path(directory) / "result.json").read_text(encoding="utf-8"))
            return manifest, result
    except (ReleaseAssetError, OSError, ValueError, KeyError) as exc:
        raise ValidationError("Immutable READY preflight evidence is missing or invalid") from exc


def _verify_ready_preflight(sha: str, migration: str, prepare_run_id: str,
                            prepare_attempt: str, token: str, api_get,
                            evidence_reader) -> dict[str, str]:
    """Require the latest exact-SHA Deploy preflight to be the published READY state."""
    runs = api_get(
        f"actions/workflows/maxibot-deploy.yml/runs?branch=main&head_sha={sha}&per_page=100",
        token,
    )
    candidates = [run for run in runs.get("workflow_runs", [])
                  if run.get("head_sha") == sha and run.get("head_branch") == "main"
                  and run.get("path") == ".github/workflows/maxibot-deploy.yml"
                  and run.get("event") == "workflow_dispatch"
                  and str(run.get("display_title", "")).startswith(f"MAXIBOT preflight {sha} prepare/")]
    if not candidates:
        raise ValidationError("Production requires a successful READY_FOR_OWNER preflight for this exact SHA")
    latest = max(candidates, key=lambda run: (run.get("run_number", 0), run.get("run_attempt", 0)))
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise ValidationError("Latest exact-SHA Deploy preflight is not successful")
    run_id, attempt = str(latest.get("id", "")), str(latest.get("run_attempt", 1))
    if not run_id.isdigit() or not attempt.isdigit():
        raise ValidationError("READY preflight run identity is invalid")
    manifest, result = evidence_reader(sha, run_id, attempt, token, api_get)
    if (manifest.get("candidate_sha") != sha
            or str(manifest.get("deploy_run_id")) != run_id
            or str(manifest.get("deploy_run_attempt")) != attempt
            or str(manifest.get("prepare_run_id")) != prepare_run_id
            or str(manifest.get("prepare_run_attempt")) != prepare_attempt):
        raise ValidationError("READY preflight evidence does not match the selected candidate/Prepare run")
    if (result.get("mode") != "preflight"
            or result.get("status") != "PLANNED"
            or result.get("release_state") != "READY_FOR_OWNER"
            or result.get("preflight") != "PASS"
            or result.get("production_changed") is not False
            or result.get("candidate_sha") != sha
            or result.get("target_migration") != migration
            or str(result.get("prepare_run_id")) != prepare_run_id
            or str(result.get("prepare_run_attempt")) != prepare_attempt):
        raise ValidationError("Immutable Deploy evidence does not prove READY_FOR_OWNER for this exact release")
    return {"run_id": run_id, "run_attempt": attempt}


def main() -> int:
    try:
        result = validate(dict(os.environ))
        output_path = os.environ.get("GITHUB_OUTPUT")
        if not output_path:
            raise ValidationError("GITHUB_OUTPUT is required")
        with open(output_path, "a", encoding="utf-8") as output:
            for key, value in result.items():
                if "\n" in value or "\r" in value:
                    raise ValidationError("GitHub output contains invalid line breaks")
                output.write(f"{key}={value}\n")
            output.write(f"network_retry_budget_used_seconds={math.ceil(NETWORK_BUDGET.spent)}\n")
        print(f"RELEASE_PROVENANCE=PASS SHA={result['candidate']} PREPARE_RUN={result['prepare_run_id']} IMMUTABLE_RELEASE={result['prepare_release_tag']}")
        return 0
    except (ValidationError, OSError) as exc:
        print(f"RELEASE_PROVENANCE=BLOCKED REASON={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
