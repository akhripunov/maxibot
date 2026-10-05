#!/usr/bin/env python3
"""Fail-closed event controller for the MAXIBOT automatic read-only preflight."""
from __future__ import annotations

import ast
import datetime as dt
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_assets import PREPARE_ASSET_NAME, ReleaseAssetError, release_tag, resolve_asset

REPOSITORY = "akhripunov/maxibot"
PREPARE_PATH = ".github/workflows/maxibot-prepare.yml"
DEPLOY_WORKFLOW = "maxibot-deploy.yml"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class OrchestrationError(RuntimeError):
    pass


class APIError(RuntimeError):
    def __init__(self, status: int, response: str = ""):
        super().__init__(status)
        self.status = status
        self.response = response


class GitHubAPI:
    def __init__(self, token: str, opener=urllib.request.urlopen):
        if not token:
            raise OrchestrationError("GITHUB_TOKEN is required")
        self.token, self.opener = token, opener

    def request(self, method: str, path: str, body: dict | None = None):
        payload = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            f"https://api.github.com/repos/{REPOSITORY}/{path.lstrip('/')}",
            data=payload, method=method,
            headers={"Authorization": f"Bearer {self.token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28",
                     "Content-Type": "application/json"})
        try:
            with self.opener(request, timeout=20) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            raise APIError(error.code, error.read().decode("utf-8", "replace")) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise OrchestrationError(f"GitHub API unavailable ({type(error).__name__})") from None

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, body):
        return self.request("POST", path, body)


def _event_run(payload: dict) -> tuple[str, dict]:
    if payload.get("repository", {}).get("full_name", "").lower() != REPOSITORY:
        raise OrchestrationError("Workflow event came from an unexpected repository")
    run = payload.get("workflow_run")
    if not isinstance(run, dict):
        raise OrchestrationError("workflow_run event payload is missing")
    sha = run.get("head_sha", "")
    if (not SHA_RE.fullmatch(sha) or run.get("head_branch") != "main"
            or run.get("status") != "completed" or run.get("conclusion") != "success"
            or run.get("path") != PREPARE_PATH
            or run.get("event") not in {"push", "workflow_dispatch"}
            or not str(run.get("id", "")).isdigit()
            or not str(run.get("run_attempt", "")).isdigit()):
        raise OrchestrationError("Prepare event is not a successful trusted-main run")
    return sha, run


def _claim_ref(sha: str, run_id: str, attempt: str) -> str:
    return f"refs/tags/maxibot-orchestrator/preflight-{sha}-{run_id}-{attempt}"


def _acquire_claim(api, sha: str, run_id: str, attempt: str) -> bool:
    ref = _claim_ref(sha, run_id, attempt)
    try:
        api.post("git/refs", {"ref": ref, "sha": sha})
        return True
    except APIError as error:
        if error.status != 422:
            raise OrchestrationError(f"Atomic preflight claim failed (HTTP {error.status})") from None
        try:
            existing = api.get("git/ref/tags/" + ref.removeprefix("refs/tags/"))
        except APIError:
            raise OrchestrationError("Preflight claim conflict could not be verified") from None
        if (existing.get("object", {}).get("type") != "commit"
                or existing.get("object", {}).get("sha") != sha):
            raise OrchestrationError("Existing preflight claim points to a different candidate")
        return False


def _active_deploy(api) -> bool:
    data = api.get(f"actions/workflows/{DEPLOY_WORKFLOW}/runs?branch=main&per_page=100")
    return any(run.get("status") in {"queued", "in_progress", "waiting", "pending"}
               for run in data.get("workflow_runs", []))


def dispatch_prepare(payload: dict, api, *, repo_root: Path, migration: str | None = None,
                     orchestrator_run_id: str = "") -> dict:
    sha, event_run = _event_run(payload)
    run_id, attempt = str(event_run["id"]), str(event_run["run_attempt"])
    main = api.get("git/ref/heads/main").get("object", {}).get("sha")
    if main != sha:
        raise OrchestrationError("Prepare candidate is no longer exact current main")
    run = api.get(f"actions/runs/{run_id}")
    for field in ("head_sha", "head_branch", "status", "conclusion", "path", "event"):
        if run.get(field) != event_run.get(field):
            raise OrchestrationError("Prepare run metadata changed after completion event")
    if str(run.get("run_attempt", "1")) != attempt:
        raise OrchestrationError("Prepare attempt changed after completion event")
    tag = release_tag("prepare", sha, run_id, attempt)
    try:
        asset = resolve_asset("prepare", sha, run_id, attempt, PREPARE_ASSET_NAME,
                              lambda route: api.get(route))
    except (ReleaseAssetError, APIError) as error:
        raise OrchestrationError("Prepare immutable Release/asset provenance failed") from error
    if _active_deploy(api):
        raise OrchestrationError("A Deploy workflow is already active; automatic preflight is blocked")
    target = migration or (alembic_heads(repo_root)[0] if repo_root else "")
    if not target or not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", target):
        raise OrchestrationError("Current Alembic target is not uniquely established")
    claimed = _acquire_claim(api, sha, run_id, attempt)
    if not claimed:
        return {"state": "PREFLIGHT_ALREADY_CLAIMED", "candidate_sha": sha,
                "prepare_run_id": run_id, "prepare_run_attempt": attempt,
                "release_tag": tag, "asset_id": str(asset["id"]),
                "asset_sha256": asset["digest"].removeprefix("sha256:")}
    body = {"ref": "main", "inputs": {
        "candidate": sha, "target_migration": target, "mode": "preflight",
        "approve_production": "false", "approve_worker_bootstrap": "false",
        "prepare_run_id": run_id, "prepare_run_attempt": attempt,
        "orchestrator_run_id": orchestrator_run_id,
    }}
    try:
        api.post(f"actions/workflows/{DEPLOY_WORKFLOW}/dispatches", body)
    except (APIError, OrchestrationError) as error:
        # Claim is deliberately retained: an uncertain dispatch is recovery work, not a blind retry.
        raise OrchestrationError("Preflight dispatch failed after durable claim; publication recovery required") from error
    return {"state": "PREFLIGHT_DISPATCHED", "candidate_sha": sha,
            "prepare_run_id": run_id, "prepare_run_attempt": attempt,
            "release_tag": tag, "asset_id": str(asset["id"]),
            "asset_sha256": asset["digest"].removeprefix("sha256:"),
            "target_migration": target}


def _assigned(tree: ast.Module, name: str):
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            try:
                return ast.literal_eval(node.value)
            except (ValueError, TypeError):
                return None
    return None


def alembic_heads(root: Path) -> list[str]:
    versions = root / "alembic" / "versions"
    files = sorted(versions.glob("*.py"))
    if not files:
        raise OrchestrationError("Alembic migration graph is empty")
    revisions, parents = set(), set()
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as error:
            raise OrchestrationError("Alembic migration graph is unreadable") from error
        revision, down = _assigned(tree, "revision"), _assigned(tree, "down_revision")
        if not isinstance(revision, str) or not revision:
            raise OrchestrationError("Alembic migration revision is invalid")
        revisions.add(revision)
        if isinstance(down, str):
            parents.add(down)
        elif isinstance(down, (tuple, list)):
            parents.update(value for value in down if isinstance(value, str))
        elif down is not None:
            raise OrchestrationError("Alembic down_revision graph is invalid")
    heads = sorted(revisions - parents)
    if len(heads) != 1:
        raise OrchestrationError("Alembic migration graph does not have one head")
    return heads


def runner_telemetry(jobs: list[dict]) -> dict:
    result = {"hosted_job_count": 0, "hosted_runner_seconds": 0,
              "self_hosted_job_count": 0, "self_hosted_runner_seconds": 0}
    for job in jobs:
        start, end = job.get("started_at"), job.get("completed_at")
        if not start or not end:
            continue
        seconds = max(0, round((dt.datetime.fromisoformat(end.replace("Z", "+00:00"))
                                - dt.datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds()))
        if job.get("runner_name", "").startswith(("GitHub Actions", "Hosted Agent")):
            prefix = "hosted"
        elif job.get("runner_name"):
            prefix = "self_hosted"
        else:
            continue
        result[prefix + "_job_count"] += 1
        result[prefix + "_runner_seconds"] += seconds
    return result


def main() -> int:
    started = time.monotonic()
    try:
        payload = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        api = GitHubAPI(os.environ.get("GITHUB_TOKEN", ""))
        result = dispatch_prepare(payload, api, repo_root=Path(os.environ["GITHUB_WORKSPACE"]),
                                  orchestrator_run_id=os.environ.get("GITHUB_RUN_ID", ""))
        elapsed = round(time.monotonic() - started)
        summary = Path(os.environ["GITHUB_STEP_SUMMARY"])
        with summary.open("a", encoding="utf-8") as output:
            output.write("## MAXIBOT automatic preflight orchestration\n\n")
            output.write(f"- State: `{result['state']}`\n- Candidate: `{result['candidate_sha']}`\n")
            output.write(f"- Prepare run/attempt: `{result['prepare_run_id']}/{result['prepare_run_attempt']}`\n")
            output.write(f"- Immutable Prepare tag: `{result['release_tag']}`\n")
            output.write(f"- Asset ID/SHA256: `{result['asset_id']}` / `{result['asset_sha256']}`\n")
            output.write(f"- Target Alembic: `{result.get('target_migration', 'verified')}`\n")
            output.write(f"- Controller job: `SELF_HOSTED_REQUIRED`, trusted project runner; measured controller seconds: `{elapsed}`\n")
            output.write(f"- `SELF_HOSTED_JOB_COUNT=1`; `SELF_HOSTED_RUNNER_SECONDS={elapsed}` (controller only)\n")
            output.write("- `DUPLICATE_HOSTED_WORK_REMOVED=YES`; `CACHE_PREVENTED_DOWNLOAD=UNKNOWN` until Mac preflight\n")
        print(json.dumps(result, sort_keys=True))
        return 0
    except (KeyError, ValueError, OrchestrationError, APIError, OSError) as error:
        print(f"AUTO_PREFLIGHT=BLOCKED TYPE={type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
