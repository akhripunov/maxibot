#!/usr/bin/env python3
"""Bind Prepare's frozen-path check to verified production and OWNER authority."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_authorization import AuthorizationError, resolve_prepare_authorization


REPOSITORY = "akhripunov/maxibot"


def github_state_exists(token: str, authorization_id: str, state: str) -> bool:
    url = (f"https://api.github.com/repos/{REPOSITORY}/git/ref/tags/"
           f"maxibot-task-auth/{authorization_id}/{state}")
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "maxibot-prepare-authorization",
    })
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read(1)
            return True
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise RuntimeError("GitHub task-authorization state read failed") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError("GitHub task-authorization state read failed") from None


def main() -> int:
    repo = Path.cwd()
    candidate = os.environ["CANDIDATE_SHA"]
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    proof = json.loads(Path(os.environ["IMPACT_PLAN"]).read_text(encoding="utf-8"))
    baseline = proof.get("production_sha")
    verified_baseline = (proof.get("candidate_sha") == candidate
                         and proof.get("baseline_source") == "maxibot-immutable-production-evidence"
                         and isinstance(baseline, str) and len(baseline) == 40)
    if not verified_baseline:
        # Keep the canonical full-suite fallback. Without immutable evidence,
        # only the existing one-commit frozen-path check is available; a
        # durable task record cannot be activated against an unknown baseline.
        baseline = subprocess.check_output(
            ["git", "rev-parse", f"{candidate}^"], text=True).strip()

    def state_exists(authorization_id: str, state: str) -> bool:
        return github_state_exists(os.environ["GITHUB_TOKEN"], authorization_id, state)

    resolved = (resolve_prepare_authorization(repo, candidate, candidate, state_exists)
                if verified_baseline else None)
    task = ""
    paths: list[str] = []
    if resolved and resolved.get("ambiguous"):
        raise AuthorizationError("Multiple durable task authorizations match this candidate")
    if resolved:
        if resolved["production_baseline_sha"] != baseline:
            raise AuthorizationError("Durable task baseline differs from immutable production evidence")
        task = resolved["authorization_id"]
        paths = resolved["changed_paths"]
    else:
        # The workflow_dispatch identity is supplied by GitHub, not by the
        # caller's text inputs. This supports a one-off exact-SHA release.
        actor = os.environ.get("RELEASE_TRIGGER_ACTOR", "")
        owner = os.environ.get("RELEASE_REPOSITORY_OWNER", "")
        requested_task = os.environ.get("OWNER_APPROVED_TASK", "").strip()
        requested_paths = [p.strip() for p in os.environ.get("OWNER_APPROVED_PATHS", "").split(",") if p.strip()]
        if requested_task or requested_paths:
            if event_name != "workflow_dispatch" or not actor or actor.casefold() != owner.casefold():
                raise AuthorizationError("Manual frozen-path approval requires repository-owner workflow_dispatch")
            if not requested_task or not requested_paths or any("*" in p or "?" in p for p in requested_paths):
                raise AuthorizationError("Manual frozen-path approval requires task ID and exact literal paths")
            task, paths = requested_task, requested_paths

    command = ["bash", "scripts/frozen_path_guard.sh", "--base", baseline,
               "--head", candidate]
    if task:
        command += ["--owner-approved-task", task]
        for path in paths:
            command += ["--owner-approved-path", path]
    subprocess.run(command, check=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AuthorizationError, RuntimeError, KeyError, ValueError) as error:
        print(f"FROZEN_PATH_AUTHORIZATION=BLOCK REASON={error}", file=sys.stderr)
        raise SystemExit(1)
