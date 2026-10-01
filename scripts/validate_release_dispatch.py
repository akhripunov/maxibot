#!/usr/bin/env python3
"""Fail-closed validation for a manually requested MAXIBOT release run."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any

REPO = "akhripunov/maxibot"
WORKFLOW = "maxibot-prepare.yml"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MIGRATION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")


class ValidationError(RuntimeError):
    pass


def github_get(path: str, token: str, opener=urllib.request.urlopen) -> Any:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with opener(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
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
    artifact_name = f"maxibot-image-{sha}-{latest['id']}-{latest.get('run_attempt', 1)}"
    artifacts = api_get(f"actions/runs/{latest['id']}/artifacts", token)
    if not any(item.get("name") == artifact_name and not item.get("expired") for item in artifacts.get("artifacts", [])):
        raise ValidationError("Exact prepared linux/amd64 image artifact is missing or expired")

    return {
        "candidate": sha,
        "target_migration": migration,
        "mode": mode,
        "prepare_run_id": str(latest["id"]),
        "prepare_run_attempt": str(latest.get("run_attempt", 1)),
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
        print(f"RELEASE_PROVENANCE=PASS SHA={result['candidate']} PREPARE_RUN={result['prepare_run_id']}")
        return 0
    except (ValidationError, OSError) as exc:
        print(f"RELEASE_PROVENANCE=BLOCKED REASON={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
