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
import urllib.error
import urllib.request
from typing import Any

from release_network_retry import NetworkUnavailable, RetryBudget, retry_network_read

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


def validate(values: dict[str, str], api_get=github_get) -> dict[str, str]:
    sha = values.get("CANDIDATE_SHA", "")
    mode = values.get("RELEASE_MODE", "").strip()
    migration = values.get("TARGET_MIGRATION", "")
    approve = values.get("APPROVE_PRODUCTION", "false").strip().lower()
    token = values.get("GITHUB_TOKEN", "")
    expected_prepare_run_id = values.get("PREPARE_RUN_ID", "").strip()
    expected_prepare_run_attempt = values.get("PREPARE_RUN_ATTEMPT", "").strip()
    if bool(expected_prepare_run_id) != bool(expected_prepare_run_attempt):
        raise ValidationError("Expected Prepare run ID and attempt must be supplied together")
    if not SHA_RE.fullmatch(sha):
        raise ValidationError("A full 40-character lowercase candidate SHA is required")
    if mode not in {"preflight", "production"}:
        raise ValidationError("An explicit preflight or production mode is required")
    if not MIGRATION_RE.fullmatch(migration):
        raise ValidationError("An explicit Alembic target revision is required")
    if approve not in {"true", "false"}:
        raise ValidationError("Production approval input is invalid")
    if mode == "production" and approve != "true":
        raise ValidationError("Production mode requires explicit owner approval")
    if mode == "preflight" and approve == "true":
        raise ValidationError("Preflight must not carry production approval")
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
    artifact_name = f"maxibot-image-{sha}-{latest['id']}-{latest.get('run_attempt', 1)}"
    artifacts = api_get(f"actions/runs/{latest['id']}/artifacts", token)
    matches = [item for item in artifacts.get("artifacts", []) if item.get("name") == artifact_name and not item.get("expired")]
    if len(matches) != 1 or not isinstance(matches[0].get("id"), int):
        raise ValidationError("Exact prepared linux/amd64 image artifact is missing or expired")

    return {
        "candidate": sha,
        "target_migration": migration,
        "mode": mode,
        "prepare_artifact_id": str(matches[0]["id"]),
        "prepare_run_id": latest_run_id,
        "prepare_run_attempt": latest_run_attempt,
        "prepare_url": str(latest.get("html_url", "")),
    }


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
        print(f"RELEASE_PROVENANCE=PASS SHA={result['candidate']} PREPARE_RUN={result['prepare_run_id']}")
        return 0
    except (ValidationError, OSError) as exc:
        print(f"RELEASE_PROVENANCE=BLOCKED REASON={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
