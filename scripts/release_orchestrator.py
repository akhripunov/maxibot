#!/usr/bin/env python3
"""Fail-closed Prepare-completion controller for the existing MAXIBOT Deploy workflow."""
from __future__ import annotations

import ast
import datetime as dt
import json
import os
from pathlib import Path
import random
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ghcr_release_artifacts import (PREPARE_ARTIFACT_TYPE, PREPARE_LAYER_MEDIA_TYPE,
    PREPARE_REPOSITORY, EVIDENCE_ARTIFACT_TYPE, EVIDENCE_LAYER_MEDIA_TYPE,
    EVIDENCE_REPOSITORY, ArtifactError, _registry_fetch, artifact_tag,
    resolve_oci_artifact, validate_prepare_receipt)
from release_network_retry import (NETWORK_RETRY_BUDGET_SECONDS, NetworkUnavailable,
    RetryBudget, RETRYABLE_HTTP_STATUS, is_retryable_network_error, retry_network_read)
from release_authorization import (AuthorizationError, authorization_state_ref,
                                   candidate_scope_diff, record_path,
                                   validate_claim_candidate,
                                   validate_task_authorization)

REPOSITORY = "akhripunov/maxibot"
PREPARE_PATH = ".github/workflows/maxibot-prepare.yml"
DEPLOY_WORKFLOW = "maxibot-deploy.yml"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
AUTH_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
CONTROLLER_RETRY_BUDGET_SECONDS = min(90, NETWORK_RETRY_BUDGET_SECONDS)
NETWORK_BUDGET = RetryBudget(seconds=CONTROLLER_RETRY_BUDGET_SECONDS)
retry_jitter = random.uniform


class OrchestrationError(RuntimeError):
    pass


class APIError(RuntimeError):
    def __init__(self, status: int, response: str = "", path: str = ""):
        super().__init__(status)
        self.status = status
        self.response = response
        self.path = path


def _retryable_github_read(error: BaseException) -> bool:
    if isinstance(error, APIError):
        return error.status in RETRYABLE_HTTP_STATUS
    return is_retryable_network_error(error)


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
            with self.opener(request, timeout=10) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            raise APIError(error.code, error.read().decode("utf-8", "replace"), path) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if method == "GET":
                raise
            raise OrchestrationError(f"GitHub API unavailable ({type(error).__name__})") from None

    def get(self, path):
        try:
            return retry_network_read(
                lambda: self.request("GET", path), "auto_release_controller_read",
                _retryable_github_read, budget=NETWORK_BUDGET, sleep=time.sleep,
                jitter=retry_jitter, emit=lambda message: print(message, file=sys.stderr))
        except NetworkUnavailable:
            raise OrchestrationError("GitHub API read unavailable within the controller retry budget") from None

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


def _task_auto_claim_ref(authorization_id: str) -> str:
    if not AUTH_ID_RE.fullmatch(authorization_id):
        raise OrchestrationError("Invalid task-scoped Deploy claim identity")
    # One durable claim binds the OWNER authorization across safe candidate retries.
    return authorization_state_ref("maxibot", authorization_id, "claimed")


def _task_dispatch_ref(authorization_id: str, prepare_run_id: str, prepare_attempt: str) -> str:
    if (not AUTH_ID_RE.fullmatch(authorization_id)
            or not re.fullmatch(r"[1-9][0-9]{0,19}", prepare_run_id)
            or not re.fullmatch(r"[1-9][0-9]{0,5}", prepare_attempt)):
        raise OrchestrationError("Invalid task-scoped dispatch identity")
    return (f"refs/tags/maxibot-task-auth/{authorization_id}/dispatches/"
            f"prepare-{prepare_run_id}-{prepare_attempt}")


def _deploy_runs(api) -> list[dict]:
    runs, total = [], None
    for page in range(1, 11):
        data = api.get(f"actions/workflows/{DEPLOY_WORKFLOW}/runs?branch=main&per_page=100&page={page}")
        page_runs, page_total = data.get("workflow_runs"), data.get("total_count")
        if (not isinstance(page_runs, list) or not isinstance(page_total, int)
                or page_total < 0 or (total is not None and page_total != total)):
            raise OrchestrationError("Deploy run inventory is incomplete")
        total = page_total
        runs.extend(page_runs)
        if len(runs) >= total:
            return runs[:total]
        if len(page_runs) < 100:
            break
    raise OrchestrationError("Deploy run inventory exceeds the bounded controller limit")


def _active_deploy(runs: list[dict]) -> bool:
    return any(run.get("status") in {"queued", "in_progress", "waiting", "pending"}
               for run in runs)


def _task_dispatch_refs(api, authorization_id: str) -> list[dict]:
    prefix = f"maxibot-task-auth/{authorization_id}/dispatches/"
    refs = api.get("git/matching-refs/tags/" + prefix)
    if not isinstance(refs, list):
        raise OrchestrationError("Task-scoped dispatch inventory is invalid")
    result = []
    for item in refs:
        name = item.get("ref", "") if isinstance(item, dict) else ""
        obj = item.get("object", {}) if isinstance(item, dict) else {}
        if (not name.startswith("refs/tags/" + prefix)
                or not SHA_RE.fullmatch(obj.get("sha", ""))
                or obj.get("type", "commit") != "commit"):
            raise OrchestrationError("Task-scoped dispatch marker is invalid")
        result.append({"ref": name.removeprefix("refs/tags/"), "sha": obj["sha"]})
    return result


def _prove_safe_prior_dispatch(api, dispatch_ref: dict, runs: list[dict],
                               authorization_id: str, production_baseline_sha: str) -> None:
    marker = dispatch_ref["ref"].rsplit("/", 1)[-1]
    match = re.fullmatch(r"prepare-([1-9][0-9]{0,19})-([1-9][0-9]{0,5})", marker)
    if not match:
        raise OrchestrationError("Task-scoped dispatch marker has an invalid Prepare identity")
    prepare_run_id, prepare_attempt = match.groups()
    candidate = dispatch_ref["sha"]
    candidates = [run for run in runs if run.get("head_sha") == candidate
                  and run.get("path") == ".github/workflows/maxibot-deploy.yml"
                  and run.get("event") == "workflow_dispatch"]
    matching = []
    for run in candidates:
        run_id, attempt = str(run.get("id", "")), str(run.get("run_attempt", ""))
        if (not re.fullmatch(r"[1-9][0-9]{0,19}", run_id)
                or not re.fullmatch(r"[1-9][0-9]{0,5}", attempt)):
            continue
        tag = artifact_tag("evidence", candidate, run_id, attempt)
        try:
            _digest, payload = resolve_oci_artifact(
                EVIDENCE_REPOSITORY, tag, EVIDENCE_ARTIFACT_TYPE,
                EVIDENCE_LAYER_MEDIA_TYPE,
                _registry_fetch(EVIDENCE_REPOSITORY, os.environ.get("GITHUB_ACTOR", ""),
                                api.token, budget=NETWORK_BUDGET))
            result = json.loads(payload.decode("utf-8"))
        except (ArtifactError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if (result.get("candidate_sha") == candidate
                and str(result.get("github_run_id", "")) == run_id
                and str(result.get("github_run_attempt", "")) == attempt
                and str(result.get("prepare_run_id", "")) == prepare_run_id
                and str(result.get("prepare_run_attempt", "")) == prepare_attempt
                and result.get("task_authorization_id") == authorization_id
                and result.get("mode") == "task-scoped-auto"):
            matching.append((run, result))
    if len(matching) != 1:
        raise OrchestrationError("Prior task-scoped Deploy result is unknown; read-only reconciliation required")
    run, result = matching[0]
    if (run.get("status") != "completed" or run.get("conclusion") != "failure"
            or result.get("schema") != "MAXIBOT_RELEASE_WORKFLOW_RESULT_V1"
            or result.get("status") != "FAILED_PRE_SWITCH"
            or result.get("release_state") != "FAILED_PRE_SWITCH"
            or result.get("production_changed") is not False
            or result.get("authorization_consumed") is not False
            or result.get("production_baseline_sha") != production_baseline_sha):
        raise OrchestrationError("Prior task-scoped Deploy was not proven safe to retry")


def _find_task_authorization(repo_root: Path, candidate_sha: str, api) -> dict[str, str]:
    directory = repo_root / ".release-authorizations"
    if not directory.is_dir():
        return {"authorization_id": "", "ambiguous": "false"}
    matches = []
    for path in sorted(directory.glob("*.json")):
        authorization_id = path.stem
        if not AUTH_ID_RE.fullmatch(authorization_id):
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            changed_paths = candidate_scope_diff(repo_root, authorization_id,
                                                 record.get("production_baseline_sha", ""), candidate_sha)
            proof = validate_task_authorization(
                record=record, project="maxibot", authorization_id=authorization_id,
                candidate_sha=candidate_sha, current_main_sha=candidate_sha,
                production_sha=record.get("production_baseline_sha", ""), changed_paths=changed_paths)
            inactive = False
            for state in ("revoked", "consumed"):
                route = "git/ref/tags/" + authorization_state_ref("maxibot", authorization_id, state).removeprefix("refs/tags/")
                try:
                    api.get(route)
                    inactive = True
                except APIError as error:
                    if error.status != 404:
                        raise
            if not inactive:
                matches.append((authorization_id, proof))
        except (OSError, ValueError, AuthorizationError, APIError, OrchestrationError):
            # Invalid/stale records cannot authorize a release; absent authorization remains exact-SHA only.
            continue
    if len(matches) == 1:
        return {"authorization_id": matches[0][0], "ambiguous": "false"}
    return {"authorization_id": "", "ambiguous": str(len(matches) > 1).lower()}


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
    tag = artifact_tag("prepare", sha, run_id, attempt)
    try:
        receipt_digest, receipt_bytes = resolve_oci_artifact(PREPARE_REPOSITORY, tag,
            PREPARE_ARTIFACT_TYPE, PREPARE_LAYER_MEDIA_TYPE,
            _registry_fetch(PREPARE_REPOSITORY, os.environ.get("GITHUB_ACTOR", ""), api.token,
                            budget=NETWORK_BUDGET))
        receipt = json.loads(receipt_bytes.decode("utf-8"))
        validate_prepare_receipt(receipt, sha, run_id, attempt)
    except (ArtifactError, UnicodeDecodeError, json.JSONDecodeError, APIError) as error:
        raise OrchestrationError("Prepare OCI receipt provenance failed") from error
    authorization = _find_task_authorization(repo_root, sha, api)
    if authorization["ambiguous"] == "true":
        return {"state": "TASK_AUTHORIZATION_AMBIGUOUS", "candidate_sha": sha,
                "prepare_run_id": run_id, "prepare_run_attempt": attempt}
    if not authorization["authorization_id"]:
        return {"state": "OWNER_EXACT_SHA_APPROVAL_REQUIRED", "candidate_sha": sha,
                "prepare_run_id": run_id, "prepare_run_attempt": attempt,
                "prepare_receipt_tag": tag, "prepare_receipt_digest": receipt_digest,
                "image_digest": receipt["image"]["digest"]}
    authorization_id = authorization["authorization_id"]
    authorization_file = repo_root / record_path(authorization_id)
    try:
        record = json.loads(authorization_file.read_text(encoding="utf-8"))
        baseline = record["production_baseline_sha"]
    except (OSError, ValueError, KeyError, TypeError):
        raise OrchestrationError("Task-scoped authorization record is unavailable") from None
    if not SHA_RE.fullmatch(str(baseline)):
        raise OrchestrationError("Task-scoped authorization baseline is invalid")
    target = migration or (alembic_heads(repo_root)[0] if repo_root else "")
    if not target or not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", target):
        raise OrchestrationError("Current Alembic target is not uniquely established")
    runs = _deploy_runs(api)
    if _active_deploy(runs):
        raise OrchestrationError("A Deploy workflow is already active")
    claim = _task_auto_claim_ref(authorization_id)
    claim_exists = False
    try:
        api.post("git/refs", {"ref": claim, "sha": sha})
    except APIError as error:
        if error.status != 422:
            raise OrchestrationError("Atomic task-scoped Deploy claim failed") from None
        existing = api.get("git/ref/tags/" + claim.removeprefix("refs/tags/"))
        claim_sha = existing.get("object", {}).get("sha")
        if (existing.get("object", {}).get("type") != "commit"
                or not SHA_RE.fullmatch(str(claim_sha or ""))):
            raise OrchestrationError("Existing task-scoped authorization claim is invalid") from None
        try:
            validate_claim_candidate(repo_root, authorization_id, record, existing, sha)
        except AuthorizationError:
            raise OrchestrationError("Existing task-scoped authorization claim is invalid") from None
        claim_exists = True
    dispatches = _task_dispatch_refs(api, authorization_id)
    event_ref = _task_dispatch_ref(authorization_id, run_id, attempt)
    existing_event = next((item for item in dispatches if "refs/tags/" + item["ref"] == event_ref), None)
    if existing_event:
        if existing_event["sha"] != sha:
            raise OrchestrationError("Prepare event dispatch marker conflicts with candidate")
        return {"state": "TASK_SCOPED_AUTO_EVENT_ALREADY_CLAIMED", "candidate_sha": sha,
                "authorization_id": authorization_id}
    if claim_exists and not dispatches:
        raise OrchestrationError("Task-scoped claim has no dispatch marker; reconciliation required")
    for previous in dispatches:
        _prove_safe_prior_dispatch(api, previous, runs, authorization_id, baseline)
    try:
        api.post("git/refs", {"ref": event_ref, "sha": sha})
    except APIError as error:
        if error.status != 422:
            raise OrchestrationError("Task-scoped dispatch marker creation failed") from None
        marker = api.get("git/ref/tags/" + event_ref.removeprefix("refs/tags/"))
        if marker.get("object", {}).get("sha") != sha:
            raise OrchestrationError("Task-scoped dispatch marker conflict is unverified") from None
        raise OrchestrationError("Task-scoped dispatch marker already exists; reconciliation required") from None
    body = {"ref": "main", "inputs": {
        "candidate": sha, "target_migration": target, "mode": "task-scoped-auto",
        "approve_production": "false", "approve_worker_bootstrap": "false",
        "prepare_run_id": run_id, "prepare_run_attempt": attempt,
        "orchestrator_run_id": orchestrator_run_id,
        "prepare_receipt_digest": receipt_digest,
        "task_authorization_id": authorization_id,
    }}
    try:
        api.post(f"actions/workflows/{DEPLOY_WORKFLOW}/dispatches", body)
    except (APIError, OrchestrationError) as error:
        # An uncertain dispatch keeps its durable claim; reconcile before retry.
        raise OrchestrationError("Task-scoped Deploy dispatch failed after durable claim; reconcile before retry") from error
    return {"state": "TASK_SCOPED_AUTO_DISPATCHED", "candidate_sha": sha,
            "prepare_run_id": run_id, "prepare_run_attempt": attempt,
            "prepare_receipt_tag": tag, "prepare_receipt_digest": receipt_digest,
            "image_digest": receipt["image"]["digest"],
            "target_migration": target, "authorization_id": authorization["authorization_id"]}


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


def safe_failure_detail(error: Exception) -> str:
    """Return a bounded diagnostic category without API bodies or credentials."""
    if isinstance(error, APIError):
        route = error.path.split("?", 1)[0].strip("/")
        route_labels = {
            "git/refs": "claim_create",
            "actions/workflows/maxibot-deploy.yml/dispatches": "task_auto_dispatch",
            "git/ref/heads/main": "main_ref_read",
        }
        if route.startswith("actions/runs/"):
            route = "prepare_run_read"
        elif route.startswith("releases/tags/") or route.startswith("repos/"):
            route = "release_metadata_read"
        else:
            route = route_labels.get(route, "github_api_request")
        return f"github_api_{route}_http_{error.status}"
    if isinstance(error, OrchestrationError):
        if str(error) == "Task-scoped Deploy dispatch failed after durable claim; reconcile before retry":
            cause = error.__cause__
            if isinstance(cause, APIError):
                return f"dispatch_after_claim_{safe_failure_detail(cause)}"
            return "dispatch_after_claim_failed_reconciliation_required"
        if str(error) == "Prepare OCI receipt provenance failed":
            return "prepare_oci_receipt_provenance_failed"
        if str(error).startswith("Atomic task-scoped Deploy claim failed"):
            status = re.search(r"HTTP (\d{3})", str(error))
            return f"claim_create_http_{status.group(1)}" if status else "claim_create_failed"
        if str(error).startswith("GitHub API unavailable ("):
            kind = re.search(r"\(([A-Za-z]+)\)", str(error))
            return f"github_api_unavailable_{kind.group(1).lower()}" if kind else "github_api_unavailable"
        known = {
            "GITHUB_TOKEN is required": "github_token_missing",
            "Workflow event came from an unexpected repository": "event_repository_untrusted",
            "workflow_run event payload is missing": "workflow_run_event_missing",
            "Prepare event is not a successful trusted-main run": "prepare_event_untrusted_or_incomplete",
            "Existing task-scoped authorization claim is invalid": "claim_conflict_unverified",
            "Prepare candidate is no longer exact current main": "prepare_sha_not_current_main",
            "Prepare run metadata changed after completion event": "prepare_run_metadata_changed",
            "Prepare attempt changed after completion event": "prepare_attempt_changed",
            "A Deploy workflow is already active": "deploy_already_active",
            "Current Alembic target is not uniquely established": "alembic_target_not_unique",
            "GitHub API read unavailable within the controller retry budget": "github_api_read_network_unavailable",
        }
        return known.get(str(error), "orchestration_guard_failed")
    if isinstance(error, KeyError):
        return "required_event_or_environment_field_missing"
    if isinstance(error, ValueError):
        return "input_json_or_metadata_invalid"
    if isinstance(error, OSError):
        return "local_io_failed"
    return "unexpected_failure"


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
            output.write("## MAXIBOT automatic release orchestration\n\n")
            output.write(f"- State: `{result['state']}`\n- Candidate: `{result['candidate_sha']}`\n")
            if result["state"] == "OWNER_EXACT_SHA_APPROVAL_REQUIRED":
                output.write("- Automatic Deploy: `NOT_AUTHORIZED`; exact-SHA OWNER approval starts the existing Deploy workflow.\n")
                output.write(f"- Prepare run/attempt: `{result['prepare_run_id']}/{result['prepare_run_attempt']}`\n")
                output.write(f"- Prepare OCI receipt: `{result['prepare_receipt_tag']}` / `{result['prepare_receipt_digest']}`\n")
                output.write(f"- Image OCI digest: `{result['image_digest']}`\n")
            elif result["state"] == "TASK_AUTHORIZATION_AMBIGUOUS":
                output.write("- Automatic Deploy: `BLOCKED`; multiple valid authorizations match.\n")
            else:
                output.write(f"- Authorization: `{result.get('authorization_id', '')}`\n")
            output.write(f"- Controller job: `SELF_HOSTED_REQUIRED`, trusted project runner; measured controller seconds: `{elapsed}`\n")
            output.write(f"- `SELF_HOSTED_JOB_COUNT=1`; `SELF_HOSTED_RUNNER_SECONDS={elapsed}` (controller only)\n")
            output.write("- `DUPLICATE_HOSTED_WORK_REMOVED=YES`; `CACHE_PREVENTED_DOWNLOAD=UNKNOWN` until Deploy verifies the artifact cache\n")
        print(json.dumps(result, sort_keys=True))
        return 0
    except (KeyError, ValueError, OrchestrationError, APIError, OSError) as error:
        detail = safe_failure_detail(error)
        print(f"MAXIBOT_AUTO_RELEASE=BLOCKED TYPE={type(error).__name__} REASON={detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
