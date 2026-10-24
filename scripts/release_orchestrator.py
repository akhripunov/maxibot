#!/usr/bin/env python3
"""Fail-closed Prepare-completion controller for the existing MAXIBOT Deploy workflow."""
from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import random
import re
import sys
import subprocess
import time
import urllib.error
import urllib.parse
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
                                   resolve_prepare_authorization,
                                   validate_claim_candidate,
                                   validate_task_authorization)

REPOSITORY = "akhripunov/maxibot"
PREPARE_PATH = ".github/workflows/maxibot-prepare.yml"
DEPLOY_WORKFLOW = "maxibot-deploy.yml"
EVIDENCE_PUBLISH_STEP = "Publish immutable Deploy evidence to GHCR and verify digest readback"
PREPARE_PUBLISH_STEP = "Publish immutable Prepare receipt to GHCR and verify digest readback"
DEPLOY_VALIDATE_STEP = "Verify approval, current main and latest successful Prepare"
DEPLOY_VALIDATE_JOB_STEPS = (
    ("Set up job", "success"),
    ("Initialize shared retry budget", "success"),
    ("Checkout trusted main workflow source", "success"),
    (DEPLOY_VALIDATE_STEP, "failure"),
)
DEPLOY_VALIDATE_POST_STEPS = (
    ("Post Checkout trusted main workflow source", "success"),
    ("Complete job", "success"),
)
HISTORICAL_GUARD_SHA = "b5f972d8fae7ecdaaef8cd0d1512512207e4c4d7"
HISTORICAL_GUARD_SOURCE_SHA256 = {
    ".github/workflows/maxibot-deploy.yml": "e3b6b4cf210c34102add9b0c8b267f5b509151da52949a919fa142f7461b764b",
    "scripts/run_release_workflow.py": "b0cef2ee9e1ba8cc0d20c1518422d3fa9d1ddd051c2b449c06cf1283c7668f20",
    "scripts/release_prod.sh": "52eb4ab5bd97dabe632f07daa21ed1ce64ef9a0e868124d48f627f85ca8c175c",
}
HISTORICAL_GUARD_DEPLOY_STEPS = (
    ("Set up job", "success"),
    ("Set result evidence path", "success"),
    ("Configure writable persistent tool cache", "success"),
    ("Checkout the approved candidate", "success"),
    ("Verify assigned runner before tools, bundle, or production checks", "success"),
    ("Install uv", "success"),
    ("Install portable Python 3.12.10", "success"),
    ("Verify Python runtime", "success"),
    ("Recheck current main and Prepare provenance on the Mac", "success"),
    ("Resolve and verify exact Prepare receipt from GHCR", "success"),
    ("Verify exact candidate checkout is still clean", "success"),
    ("Run production preflight and requested release mode", "failure"),
    ("Set up ORAS for immutable Deploy evidence", "success"),
    ("Publish immutable Deploy evidence to GHCR and verify digest readback", "failure"),
    ("Post Install uv", "skipped"),
    ("Post Checkout the approved candidate", "success"),
    ("Complete job", "success"),
)
HISTORICAL_GUARD_PUBLISH_STEPS = (
    ("Set up job", "success"),
    ("Set publication evidence path", "success"),
    ("Run actions/checkout@v4", "success"),
    ("Measure release runner usage", "success"),
    ("Mark release telemetry unknown", "skipped"),
    ("Read immutable GHCR evidence by exact digest", "failure"),
    ("Publish preflight result to MAXIBOT Linear", "success"),
    ("Publish production result to MAXIBOT Linear", "success"),
    ("Post Run actions/checkout@v4", "success"),
    ("Complete job", "success"),
)
HISTORICAL_GUARD_VALIDATOR_STEPS = (
    ("Set up job", "success"),
    ("Initialize shared retry budget", "success"),
    ("Checkout trusted main workflow source", "success"),
    (DEPLOY_VALIDATE_STEP, "success"),
    ("Post Checkout trusted main workflow source", "success"),
    ("Complete job", "success"),
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
AUTH_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
CONTROLLER_RETRY_BUDGET_SECONDS = NETWORK_RETRY_BUDGET_SECONDS
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


class _NoAuthGitHubLogRedirect(urllib.request.HTTPRedirectHandler):
    """Allow GitHub's signed log redirect without forwarding API credentials."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        host = (parsed.hostname or "").lower()
        allowed = (host in {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
                   or host.endswith(".blob.core.windows.net"))
        if (parsed.scheme != "https" or not allowed or parsed.username or parsed.password
                or parsed.port not in {None, 443}):
            return None
        return urllib.request.Request(newurl, headers={"User-Agent": "maxibot-release-controller"})


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

    def get_job_log_archive(self, job_id: str) -> str:
        if not re.fullmatch(r"[1-9][0-9]{0,19}", job_id):
            raise OrchestrationError("Deploy evidence job identity is invalid")
        path = f"actions/jobs/{job_id}/logs"
        request = urllib.request.Request(
            f"https://api.github.com/repos/{REPOSITORY}/{path}",
            headers={"Authorization": f"Bearer {self.token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"})
        opener = urllib.request.build_opener(_NoAuthGitHubLogRedirect())

        def download():
            try:
                with opener.open(request, timeout=10) as response:
                    body = response.read(20 * 1024 * 1024 + 1)
                    if len(body) > 20 * 1024 * 1024:
                        raise OrchestrationError("Deploy evidence job logs exceed the size limit")
                    return body.decode("utf-8", "replace")
            except urllib.error.HTTPError as error:
                raise APIError(error.code, "", path) from None

        try:
            return retry_network_read(download, "auto_release_deploy_log_read",
                _retryable_github_read, budget=NETWORK_BUDGET, sleep=time.sleep,
                jitter=retry_jitter, emit=lambda message: print(message, file=sys.stderr))
        except NetworkUnavailable:
            raise OrchestrationError("Deploy evidence job logs unavailable within the controller retry budget") from None

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


def _verified_deploy_evidence(api, run: dict) -> tuple[str, dict] | None:
    run_id, attempt = str(run.get("id", "")), str(run.get("run_attempt", ""))
    if (not re.fullmatch(r"[1-9][0-9]{0,19}", run_id)
            or not re.fullmatch(r"[1-9][0-9]{0,5}", attempt)):
        return None
    digest = publisher_logged_digest(api, run_id, attempt, EVIDENCE_PUBLISH_STEP,
                                     "MAXIBOT_GHCR_DEPLOY_EVIDENCE=PASS DIGEST=")
    candidate = run.get("head_sha", "")
    tag = artifact_tag("evidence", candidate, run_id, attempt)
    try:
        resolved_digest, payload = resolve_oci_artifact(
            EVIDENCE_REPOSITORY, tag, EVIDENCE_ARTIFACT_TYPE, EVIDENCE_LAYER_MEDIA_TYPE,
            _registry_fetch(EVIDENCE_REPOSITORY, os.environ.get("GITHUB_ACTOR", ""),
                            api.token, budget=NETWORK_BUDGET))
        result = json.loads(payload.decode("utf-8"))
    except (ArtifactError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if resolved_digest != digest:
        return None
    if (result.get("schema") != "MAXIBOT_RELEASE_WORKFLOW_RESULT_V1"
            or result.get("candidate_sha") != candidate
            or str(result.get("github_run_id", "")) != run_id
            or str(result.get("github_run_attempt", "")) != attempt):
        return None
    return digest, result


def _log_content(line: str) -> str:
    # Preserve indentation after GitHub's ISO timestamp prefix. Untimestamped
    # lines intentionally remain unchanged and cannot prove runner env values.
    return re.sub(r"^\d{4}-\d{2}-\d{2}T[0-9:.+-]+Z ", "", line.removeprefix("\ufeff"))


def _has_runner_timestamp(line: str) -> bool:
    return re.match(r"^\d{4}-\d{2}-\d{2}T[0-9:.+-]+Z ", line.removeprefix("\ufeff")) is not None


def _validation_log_inputs(text: str) -> dict[str, str] | None:
    lines = text.splitlines()
    command = "##[group]Run python3 scripts/validate_release_dispatch.py"
    starts = [i for i, line in enumerate(lines) if _log_content(line) == command]
    if len(starts) != 1 or not _has_runner_timestamp(lines[starts[0]]):
        return None
    start = starts[0]
    end = next((i for i in range(start + 1, len(lines))
                if _log_content(lines[i]) == "##[endgroup]"), None)
    if end is None or any(_log_content(lines[i]).startswith("##[group]")
                          for i in range(start + 1, end)):
        return None
    body = lines[start + 1:end]
    if not all(_has_runner_timestamp(line) for line in lines[start:end + 1]):
        return None
    env_sections = [i for i, line in enumerate(body) if _log_content(line) == "env:"]
    if len(env_sections) != 1:
        return None
    values: dict[str, str] = {}
    for line in body[env_sections[0] + 1:]:
        content = _log_content(line)
        if not content.strip():
            continue
        match = re.fullmatch(r"\s{2,}([A-Z][A-Z0-9_]*):\s?(.*)", content)
        if not match or match.group(1) in values:
            return None
        values[match.group(1)] = match.group(2)
    return values


def _prove_safe_predeploy_failure(api, run: dict, candidate: str, prepare_run_id: str,
                                  prepare_attempt: str, authorization_id: str) -> bool:
    run_id, run_attempt = str(run.get("id", "")), str(run.get("run_attempt", ""))
    if (not SHA_RE.fullmatch(candidate)
            or not re.fullmatch(r"[1-9][0-9]{0,19}", run_id)
            or not re.fullmatch(r"[1-9][0-9]{0,5}", run_attempt)
            or run.get("head_sha") != candidate or run.get("head_branch") != "main"
            or run.get("path") != ".github/workflows/maxibot-deploy.yml"
            or run.get("event") != "workflow_dispatch"
            or run.get("status") != "completed" or run.get("conclusion") != "failure"):
        return False
    try:
        response = api.get(f"actions/runs/{run_id}/attempts/{run_attempt}/jobs?per_page=100")
    except Exception:
        return False
    jobs, total = response.get("jobs"), response.get("total_count")
    if not isinstance(jobs, list) or total != 3 or len(jobs) != 3:
        return False
    job_ids = []
    for job in jobs:
        if (not isinstance(job, dict)
                or str(job.get("run_id", "")) != run_id
                or str(job.get("run_attempt", "")) != run_attempt
                or (job.get("head_sha") is not None and job.get("head_sha") != candidate)
                or not re.fullmatch(r"[1-9][0-9]{0,19}", str(job.get("id", "")))):
            return False
        job_ids.append(str(job["id"]))
    if len(set(job_ids)) != len(job_ids):
        return False
    by_name: dict[str, list[dict]] = {}
    for job in jobs:
        by_name.setdefault(job.get("name", ""), []).append(job)
    if set(by_name) != {"validate", "deploy", "publish"} or any(len(v) != 1 for v in by_name.values()):
        return False
    validate_job, deploy_job, publish_job = (by_name[name][0] for name in ("validate", "deploy", "publish"))
    validate_steps = validate_job.get("steps")
    deploy_steps = deploy_job.get("steps")
    validate_step_results = ([(step.get("name"), step.get("status"), step.get("conclusion"))
                              for step in validate_steps]
                             if isinstance(validate_steps, list)
                             and all(isinstance(step, dict) for step in validate_steps) else [])
    expected_validate_prefix = [(name, "completed", conclusion)
                                for name, conclusion in DEPLOY_VALIDATE_JOB_STEPS]
    allowed_post = [(name, "completed", conclusion)
                    for name, conclusion in DEPLOY_VALIDATE_POST_STEPS]
    if (len(validate_step_results) < len(expected_validate_prefix)
            or validate_step_results[:len(expected_validate_prefix)] != expected_validate_prefix):
        return False
    tail = validate_step_results[len(expected_validate_prefix):]
    # GitHub may report checkout's post action and its synthetic completion
    # step. Permit only this ordered suffix after the failed validator.
    if (len(tail) > len(allowed_post)
            or tail != allowed_post[:len(tail)]
            or (tail and tail[-1][0] == "Complete job" and tail != allowed_post)):
        return False
    if (validate_job.get("status") != "completed" or validate_job.get("conclusion") != "failure"
            or not isinstance(validate_steps, list)
            or len(validate_step_results) != len(validate_steps)):
        return False
    if (deploy_job.get("status") != "completed" or deploy_job.get("conclusion") != "skipped"
            or deploy_steps != []):
        return False
    if (publish_job.get("status") != "completed"
            or publish_job.get("conclusion") not in {"success", "failure"}):
        return False
    try:
        log = api.get_job_log_archive(str(validate_job.get("id", "")))
    except Exception:
        return False
    env = _validation_log_inputs(log) if isinstance(log, str) else None
    if env is None:
        return False
    expected = {
        "CANDIDATE_SHA": candidate,
        "RELEASE_MODE": "task-scoped-auto",
        "PREPARE_RUN_ID": prepare_run_id,
        "PREPARE_RUN_ATTEMPT": prepare_attempt,
        "TASK_AUTHORIZATION_ID": authorization_id,
        "APPROVE_PRODUCTION": "false",
        "APPROVE_WORKER_BOOTSTRAP": "false",
    }
    return all(env.get(key) == value for key, value in expected.items())


def _historical_guard_source_is_pinned(repo_root: Path, candidate: str) -> bool:
    if candidate != HISTORICAL_GUARD_SHA:
        return False
    try:
        for path, expected in HISTORICAL_GUARD_SOURCE_SHA256.items():
            blob = subprocess.check_output(
                ["git", "show", f"{candidate}:{path}"], cwd=repo_root,
                stderr=subprocess.DEVNULL, timeout=10)
            if hashlib.sha256(blob).hexdigest() != expected:
                return False
        release_script = subprocess.check_output(
            ["git", "show", f"{candidate}:scripts/release_prod.sh"], cwd=repo_root,
            stderr=subprocess.DEVNULL, timeout=10).decode("utf-8")
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        return False
    guard = '[[ -n "${GITHUB_TOKEN:-}" && "${GITHUB_ACTOR:-}" =~ ^[A-Za-z0-9-]{1,39}$ ]] || die "A workflow token is required to mint a scoped GHCR pull token"'
    return (release_script.count(guard) == 1
            and release_script.index(guard) < release_script.find("ssh_preflight"))


def _historical_guard_log_is_exact(api, job: dict, candidate: str, prepare_run_id: str,
                                   prepare_attempt: str, authorization_id: str) -> bool:
    try:
        log = api.get_job_log_archive(str(job["id"]))
    except Exception:
        return False
    if not isinstance(log, str):
        return False
    lines = log.splitlines()
    command = '##[group]Run "$MAXIBOT_PYTHON" scripts/run_release_workflow.py'
    starts = [i for i, line in enumerate(lines) if _log_content(line) == command]
    if len(starts) != 1 or not _has_runner_timestamp(lines[starts[0]]):
        return False
    start = starts[0]
    ends = [i for i in range(start + 1, len(lines))
            if _log_content(lines[i]) == "##[endgroup]"]
    if (not ends or any(_log_content(line).startswith("##[group]")
                        for line in lines[start + 1:ends[0]])
            or not all(_has_runner_timestamp(line) for line in lines[start:ends[0] + 1])):
        return False
    body = lines[start + 1:ends[0]]
    env_positions = [i for i, line in enumerate(body) if _log_content(line) == "env:"]
    if len(env_positions) != 1:
        return False
    env: dict[str, str] = {}
    for line in body[env_positions[0] + 1:]:
        content = _log_content(line)
        if not content.strip():
            continue
        match = re.fullmatch(r"\s{2,}([A-Z][A-Z0-9_]*):\s?(.*)", content)
        if not match or match.group(1) in env:
            return False
        env[match.group(1)] = match.group(2)
    required = {
        "CANDIDATE_SHA": candidate, "RELEASE_MODE": "task-scoped-auto",
        "PREPARE_RUN_ID": prepare_run_id, "PREPARE_RUN_ATTEMPT": prepare_attempt,
        "TASK_AUTHORIZATION_ID": authorization_id, "APPROVE_PRODUCTION": "false",
        "APPROVE_WORKER_BOOTSTRAP": "false", "GITHUB_TOKEN": "***",
    }
    if any(env.get(key) != value for key, value in required.items()):
        return False
    # The guard is proven only after a successful read-only preflight and the
    # second invocation of the pinned release entrypoint, before any apply marker.
    content = [_log_content(line) for line in lines[ends[0] + 1:]]
    entrypoint = "RELEASE_ENTRYPOINT=scripts/release_prod.sh"
    guard = "RELEASE_BLOCKED=A workflow token is required to mint a scoped GHCR pull token"
    apply_start = "RELEASE_STAGE=APPLY_START"
    preflight_start = "RELEASE_STAGE=PREFLIGHT_START"
    target = f"TARGET_SHA={candidate}"
    result = (f"MAXIBOT_RELEASE_RESULT=FAILED_PRE_SWITCH SHA={candidate} "
              "MODE=task-scoped-auto PRODUCTION_CHANGED=False")
    if (content.count(entrypoint) != 2 or content.count(guard) != 1
            or content.count("SAFE_TO_RELEASE=YES") != 1
            or content.count("DRY_RUN_RESULT=PASS") != 1
            or content.count(preflight_start) != 1 or content.count(apply_start) != 1
            or content.count(target) != 2 or content.count("MIGRATION_ALLOWED=NO") != 1
            or content.count(result) != 1):
        return False
    preflight = content.index(preflight_start)
    first_entry = content.index(entrypoint)
    safe = content.index("SAFE_TO_RELEASE=YES")
    dry_run = content.index("DRY_RUN_RESULT=PASS")
    migration = content.index("MIGRATION_ALLOWED=NO")
    apply = content.index(apply_start)
    second_entry = content.index(entrypoint, first_entry + 1)
    guard_pos = content.index(guard)
    result_pos = content.index(result)
    target_positions = [i for i, line in enumerate(content) if line == target]
    if not (preflight < first_entry < target_positions[0] < migration < safe < dry_run
            < apply < second_entry < target_positions[1] < guard_pos < result_pos):
        return False
    result_lines = [line for line in content if line.startswith("MAXIBOT_RELEASE_RESULT=")]
    if (len(result_lines) != 1 or any(any(marker in line for marker in (
            "MIGRATION_ALLOWED=YES", "PRODUCTION_CHANGED=True", "PRODUCTION_CHANGED=POSSIBLE",
            "RECOVERY_REQUIRED", "RELEASE_STATUS=PASS", "MAXIBOT_RELEASE_RESULT=COMPLETE",
            "MAXIBOT_RELEASE_RESULT=FAILED_AFTER_SWITCH"))
           for line in content)):
        return False
    return all(_has_runner_timestamp(line) for line in lines[ends[0] + 1:]
               if _log_content(line).startswith((entrypoint, guard, "SAFE_TO_RELEASE=",
                                                 "DRY_RUN_RESULT=", "MAXIBOT_RELEASE_RESULT=",
                                                 "RELEASE_STAGE=", "TARGET_SHA=", "MIGRATION_ALLOWED=")))


def _historical_guard_deployment_is_terminal_failure(api, run_id: str, attempt: str,
                                                     candidate: str) -> bool:
    try:
        deployments = api.get(
            f"deployments?sha={candidate}&environment=maxibot-production&per_page=100&page=1")
    except Exception:
        return False
    if not isinstance(deployments, list) or len(deployments) != 1:
        return False
    matches = []
    for deployment in deployments:
        if not isinstance(deployment, dict):
            return False
        payload = deployment.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                return False
        if not isinstance(payload, dict):
            continue
        if (payload.get("candidate_sha") == candidate
                and str(payload.get("workflow_run_id", "")) == run_id
                and str(payload.get("workflow_run_attempt", "")) == attempt
                and payload.get("mode") == "task-scoped-auto"):
            matches.append(deployment)
    if len(matches) != 1:
        return False
    deployment = matches[0]
    if (deployment.get("task") != "maxibot-production-deploy"
            or deployment.get("environment") != "maxibot-production"
            or deployment.get("ref") != candidate or deployment.get("sha") != candidate):
        return False
    deployment_id = str(deployment.get("id", ""))
    if not re.fullmatch(r"[1-9][0-9]{0,19}", deployment_id):
        return False
    try:
        statuses = api.get(f"deployments/{deployment_id}/statuses?per_page=100&page=1")
    except Exception:
        return False
    if not isinstance(statuses, list) or not statuses or len(statuses) >= 100:
        return False
    status_ids = [str(item.get("id", "")) for item in statuses if isinstance(item, dict)]
    if len(status_ids) != len(statuses) or len(set(status_ids)) != len(status_ids):
        return False
    latest = statuses[0]
    expected_url = f"https://api.github.com/repos/{REPOSITORY}/deployments/{deployment_id}"
    if (latest.get("deployment_url") != expected_url or latest.get("state") != "failure"
            or not isinstance(latest.get("created_at"), str)):
        return False
    try:
        latest_time = dt.datetime.fromisoformat(latest["created_at"].replace("Z", "+00:00"))
        for older in statuses[1:]:
            if (not isinstance(older, dict) or older.get("deployment_url") != expected_url
                    or not isinstance(older.get("created_at"), str)
                    or latest_time <= dt.datetime.fromisoformat(older["created_at"].replace("Z", "+00:00"))):
                return False
    except (ValueError, TypeError):
        return False
    return True


def _prove_historical_guard_failure(api, run: dict, candidate: str, prepare_run_id: str,
                                    prepare_attempt: str, authorization_id: str,
                                    repo_root: Path | None) -> bool:
    if (repo_root is None or candidate != HISTORICAL_GUARD_SHA
            or not _historical_guard_source_is_pinned(repo_root, candidate)
            or run.get("actor", {}).get("login") != "github-actions[bot]"
            or run.get("status") != "completed" or run.get("conclusion") != "failure"
            or run.get("head_sha") != candidate or run.get("head_branch") != "main"
            or run.get("path") != ".github/workflows/maxibot-deploy.yml"
            or run.get("event") != "workflow_dispatch"):
        return False
    run_id, attempt = str(run.get("id", "")), str(run.get("run_attempt", ""))
    if (run_id != "39545600520" or attempt != "1"
            or not _historical_guard_deployment_is_terminal_failure(api, run_id, attempt, candidate)):
        return False
    try:
        response = api.get(f"actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
    except Exception:
        return False
    jobs, total = response.get("jobs"), response.get("total_count")
    if not isinstance(jobs, list) or total != 3 or len(jobs) != 3:
        return False
    by_name: dict[str, list[dict]] = {}
    ids = []
    for job in jobs:
        if (not isinstance(job, dict) or str(job.get("run_id")) != run_id
                or str(job.get("run_attempt")) != attempt or job.get("head_sha") != candidate
                or not re.fullmatch(r"[1-9][0-9]{0,19}", str(job.get("id", "")))):
            return False
        ids.append(str(job["id"]))
        by_name.setdefault(job.get("name", ""), []).append(job)
    if len(set(ids)) != len(ids) or set(by_name) != {"validate", "deploy", "publish"}:
        return False
    if any(len(items) != 1 for items in by_name.values()):
        return False
    validate_job, deploy_job, publish_job = (by_name[name][0] for name in ("validate", "deploy", "publish"))
    expected = {
        "validate": ("success", HISTORICAL_GUARD_VALIDATOR_STEPS),
        "deploy": ("failure", HISTORICAL_GUARD_DEPLOY_STEPS),
        "publish": ("failure", HISTORICAL_GUARD_PUBLISH_STEPS),
    }
    for name, job in (("validate", validate_job), ("deploy", deploy_job), ("publish", publish_job)):
        conclusion, expected_steps = expected[name]
        steps = job.get("steps")
        actual = ([(step.get("name"), step.get("status"), step.get("conclusion"))
                  for step in steps if isinstance(step, dict)] if isinstance(steps, list) else [])
        if (job.get("status") != "completed" or job.get("conclusion") != conclusion
                or not isinstance(steps, list) or len(actual) != len(steps)
                or actual != [(step, "completed", state) for step, state in expected_steps]):
            return False
    return (_historical_guard_log_is_exact(api, deploy_job, candidate, prepare_run_id,
                                           prepare_attempt, authorization_id)
            and str(validate_job.get("id")) == "119042955279"
            and str(deploy_job.get("id")) == "119043003895"
            and str(publish_job.get("id")) == "119049527308")


def publisher_logged_digest(api, run_id: str, attempt: str, publisher_step: str,
                            marker_prefix: str) -> str:
    """Bind a publisher's durable artifact digest to one successful run attempt log."""
    if (not re.fullmatch(r"[1-9][0-9]{0,19}", str(run_id))
            or not re.fullmatch(r"[1-9][0-9]{0,5}", str(attempt))
            or not publisher_step or not marker_prefix):
        raise OrchestrationError("Publisher attempt identity is invalid")
    jobs = api.get(f"actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
    job_list = jobs.get("jobs")
    total = jobs.get("total_count")
    if not isinstance(job_list, list) or not isinstance(total, int) or total != len(job_list):
        raise OrchestrationError("Deploy evidence job inventory is incomplete")
    publisher_jobs = []
    for job in job_list:
        if not isinstance(job, dict) or str(job.get("run_id", run_id)) != run_id:
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        publishers = [step for step in steps if isinstance(step, dict)
                      and step.get("name") == publisher_step]
        if len(publishers) > 1:
            raise OrchestrationError("Deploy evidence publisher step is ambiguous")
        if publishers:
            publisher_jobs.append((str(job.get("id", "")), publishers[0].get("conclusion")))
    if len(publisher_jobs) != 1 or publisher_jobs[0][1] != "success":
        raise OrchestrationError("Publisher step is missing, unsuccessful, or ambiguous")
    text = api.get_job_log_archive(publisher_jobs[0][0])
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    prefix = re.escape(marker_prefix)
    timestamp = r"(?:[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.+-]+Z )?"
    digests = re.findall(rf"(?m)^{timestamp}{prefix}(sha256:[0-9a-f]{{64}})\r?$", text)
    if len(digests) != 1:
        raise OrchestrationError("Publisher digest log is missing or ambiguous")
    return digests[0]


def _prove_historical_receipt_failure_before_execute(api, run: dict, candidate: str,
                                                     prepare_run_id: str, prepare_attempt: str,
                                                     authorization_id: str,
                                                     repo_root: Path | None) -> bool:
    """Read-only proof for both known attempts that never entered release_prod.

    This is not a synthetic deployment result or a generic skipped-step bypass.
    The historical source and every attempt are pinned; an additional attempt,
    any execution of the release step, or incomplete evidence remains unknown.
    """
    if (repo_root is None or candidate != "dc7c5acfee1f6978dc11945b1a342e9bee11a75f"
            or str(run.get("id")) != "39570151060" or str(run.get("run_attempt")) != "2"
            or run.get("head_sha") != candidate or run.get("head_branch") != "main"
            or run.get("path") != ".github/workflows/maxibot-deploy.yml"
            or run.get("event") != "workflow_dispatch" or run.get("status") != "completed"
            or run.get("conclusion") != "failure"
            or not isinstance(run.get("actor"), dict)
            or run["actor"].get("login") != "github-actions[bot]"
            or prepare_run_id != "39567592711" or prepare_attempt != "1"):
        return False
    pins = {
        ".github/workflows/maxibot-deploy.yml": "e3b6b4cf210c34102add9b0c8b267f5b509151da52949a919fa142f7461b764b",
        "scripts/ghcr_prepare_cache.py": "e96e1ee59d984dc24390cfb743d1dc21c3cac81c3dcc4c6d874e17be8eb963ab",
        "scripts/run_release_workflow.py": "b0cef2ee9e1ba8cc0d20c1518422d3fa9d1ddd051c2b449c06cf1283c7668f20",
    }
    try:
        for path, expected in pins.items():
            blob = subprocess.check_output(["git", "show", f"{candidate}:{path}"],
                cwd=repo_root, stderr=subprocess.DEVNULL, timeout=10)
            if hashlib.sha256(blob).hexdigest() != expected:
                return False
        deployments = api.get(f"deployments?sha={candidate}&environment=maxibot-production&per_page=100&page=1")
    except Exception:
        return False
    if deployments != []:
        return False
    ids = {1: (119122003592, 119122088345, 119125203086),
           2: (119128556433, 119128555077, 119129985371)}
    required_env = {"CANDIDATE_SHA": candidate, "RELEASE_MODE": "task-scoped-auto",
                    "PREPARE_RUN_ID": prepare_run_id, "PREPARE_RUN_ATTEMPT": prepare_attempt,
                    "TASK_AUTHORIZATION_ID": authorization_id, "APPROVE_PRODUCTION": "false",
                    "APPROVE_WORKER_BOOTSTRAP": "false"}
    receipt_overrides = {"Resolve and verify exact Prepare receipt from GHCR": "failure",
                        "Verify exact candidate checkout is still clean": "skipped",
                        "Run production preflight and requested release mode": "skipped",
                        EVIDENCE_PUBLISH_STEP: "skipped"}
    deploy_steps = tuple((name, receipt_overrides.get(name, state))
                        for name, state in HISTORICAL_GUARD_DEPLOY_STEPS)
    publish_steps = tuple((name, "skipped" if name in {
        "Read immutable GHCR evidence by exact digest", "Publish production result to MAXIBOT Linear"}
        else state) for name, state in HISTORICAL_GUARD_PUBLISH_STEPS)
    for attempt in (1, 2):
        try:
            response = api.get(f"actions/runs/39570151060/attempts/{attempt}/jobs?per_page=100")
        except Exception:
            return False
        if not isinstance(response, dict):
            return False
        jobs = response.get("jobs")
        if not isinstance(jobs, list) or response.get("total_count") != 3 or len(jobs) != 3:
            return False
        by_name = {}
        for job in jobs:
            if (not isinstance(job, dict) or job.get("name") in by_name
                    or str(job.get("run_id")) != "39570151060"
                    or str(job.get("run_attempt")) != str(attempt)
                    or job.get("head_sha") != candidate or job.get("status") != "completed"):
                return False
            by_name[job.get("name")] = job
        if set(by_name) != {"validate", "deploy", "publish"}:
            return False
        if tuple(by_name[name].get("id") for name in ("validate", "deploy", "publish")) != ids[attempt]:
            return False
        expected = {"validate": ("success", HISTORICAL_GUARD_VALIDATOR_STEPS),
                    "publish": ("success", publish_steps)}
        if attempt == 1:
            deploy = by_name["deploy"]
            if (deploy.get("conclusion") != "cancelled" or "steps" not in deploy
                    or deploy["steps"] not in (None, [])):
                return False
        else:
            expected["deploy"] = ("failure", deploy_steps)
        for name, (conclusion, steps) in expected.items():
            job = by_name[name]
            actual = job.get("steps")
            if (job.get("conclusion") != conclusion or not isinstance(actual, list)
                    or not all(isinstance(step, dict) for step in actual)
                    or [(step.get("name"), step.get("status"), step.get("conclusion")) for step in actual]
                    != [(name, "completed", state) for name, state in steps]):
                return False
        try:
            env = _validation_log_inputs(api.get_job_log_archive(str(ids[attempt][0])))
        except Exception:
            return False
        if env is None or any(env.get(key) != value for key, value in required_env.items()):
            return False
    return True


def _prove_safe_prior_dispatch(api, dispatch_ref: dict, runs: list[dict],
                               authorization_id: str, production_baseline_sha: str,
                               repo_root: Path | None = None) -> None:
    marker = dispatch_ref["ref"].rsplit("/", 1)[-1]
    match = re.fullmatch(r"prepare-([1-9][0-9]{0,19})-([1-9][0-9]{0,5})", marker)
    if not match:
        raise OrchestrationError("Task-scoped dispatch marker has an invalid Prepare identity")
    prepare_run_id, prepare_attempt = match.groups()
    candidate = dispatch_ref["sha"]
    candidates = [run for run in runs if run.get("head_sha") == candidate
                  and run.get("path") == ".github/workflows/maxibot-deploy.yml"
                  and run.get("event") == "workflow_dispatch"]
    if not candidates:
        raise OrchestrationError("Prior task-scoped Deploy result is unknown; read-only reconciliation required")
    safe_prior_runs = 0
    for run in candidates:
        if _prove_historical_receipt_failure_before_execute(api, run, candidate, prepare_run_id,
                                                           prepare_attempt, authorization_id, repo_root):
            safe_prior_runs += 1
            continue
        if _prove_historical_guard_failure(api, run, candidate, prepare_run_id,
                                           prepare_attempt, authorization_id, repo_root):
            safe_prior_runs += 1
            continue
        if _prove_safe_predeploy_failure(api, run, candidate, prepare_run_id,
                                         prepare_attempt, authorization_id):
            safe_prior_runs += 1
            continue
        run_id, attempt = str(run.get("id", "")), str(run.get("run_attempt", ""))
        if (not re.fullmatch(r"[1-9][0-9]{0,19}", run_id)
                or not re.fullmatch(r"[1-9][0-9]{0,5}", attempt)):
            raise OrchestrationError("Prior task-scoped Deploy identity is unknown")
        try:
            verified = _verified_deploy_evidence(api, run)
        except OrchestrationError:
            raise OrchestrationError("Prior task-scoped Deploy result is unknown; read-only reconciliation required") from None
        if verified is None:
            raise OrchestrationError("Prior task-scoped Deploy result is unknown; read-only reconciliation required")
        _digest, result = verified
        if (result.get("candidate_sha") == candidate
                and str(result.get("github_run_id", "")) == run_id
                and str(result.get("github_run_attempt", "")) == attempt
                and str(result.get("prepare_run_id", "")) == prepare_run_id
                and str(result.get("prepare_run_attempt", "")) == prepare_attempt
                and result.get("task_authorization_id") == authorization_id
                and result.get("mode") == "task-scoped-auto"):
            if (run.get("status") != "completed" or run.get("conclusion") != "failure"
                    or result.get("schema") != "MAXIBOT_RELEASE_WORKFLOW_RESULT_V1"
                    or result.get("status") != "FAILED_PRE_SWITCH"
                    or result.get("release_state") != "FAILED_PRE_SWITCH"
                    or result.get("production_changed") is not False
                    or result.get("authorization_consumed") is not False
                    or result.get("production_baseline_sha") != production_baseline_sha):
                raise OrchestrationError("Prior task-scoped Deploy was not proven safe to retry")
            safe_prior_runs += 1
            continue
        raise OrchestrationError("Prior task-scoped Deploy result is unknown; read-only reconciliation required")
    if safe_prior_runs != len(candidates):
        raise OrchestrationError("Prior task-scoped Deploy result is unknown; read-only reconciliation required")


def _find_task_authorization(repo_root: Path, candidate_sha: str, api) -> dict[str, str]:
    def state_ref_exists(authorization_id: str, state: str) -> bool:
        route = "git/ref/tags/" + authorization_state_ref(
            "maxibot", authorization_id, state).removeprefix("refs/tags/")
        try:
            api.get(route)
            return True
        except APIError as error:
            if error.status == 404:
                return False
            raise

    try:
        result = resolve_prepare_authorization(
            repo_root, candidate_sha, candidate_sha, state_ref_exists)
    except Exception as error:
        raise OrchestrationError("Task authorization state could not be authoritatively checked") from error
    if result is None:
        return {"authorization_id": "", "ambiguous": "false"}
    return {"authorization_id": result["authorization_id"],
            "ambiguous": str(result["ambiguous"]).lower()}


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
        publisher_digest = publisher_logged_digest(
            api, run_id, attempt, PREPARE_PUBLISH_STEP,
            "MAXIBOT_GHCR_PREPARE_RECEIPT=PASS DIGEST=")
        receipt_digest, receipt_bytes = resolve_oci_artifact(PREPARE_REPOSITORY, tag,
            PREPARE_ARTIFACT_TYPE, PREPARE_LAYER_MEDIA_TYPE,
            _registry_fetch(PREPARE_REPOSITORY, os.environ.get("GITHUB_ACTOR", ""), api.token,
                            budget=NETWORK_BUDGET))
        if receipt_digest != publisher_digest:
            raise OrchestrationError("Prepare receipt digest does not match successful publisher log")
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
        marker_name = existing_event["ref"].rsplit("/", 1)[-1]
        identity = re.fullmatch(r"prepare-([1-9][0-9]{0,19})-([1-9][0-9]{0,5})", marker_name)
        if not identity:
            raise OrchestrationError("Existing Prepare dispatch marker identity is invalid")
        prepare_id, prepare_attempt = identity.groups()
        reconciled = []
        for run in runs:
            if (run.get("head_sha") != sha
                    or run.get("path") != ".github/workflows/maxibot-deploy.yml"
                    or run.get("event") != "workflow_dispatch"
                    or run.get("status") != "completed"):
                continue
            try:
                verified = _verified_deploy_evidence(api, run)
            except OrchestrationError:
                continue
            if verified is None:
                continue
            _digest, result = verified
            if (str(result.get("prepare_run_id", "")) == prepare_id
                    and str(result.get("prepare_run_attempt", "")) == prepare_attempt
                    and result.get("task_authorization_id") == authorization_id
                    and result.get("mode") == "task-scoped-auto"):
                reconciled.append(run)
        if len(reconciled) != 1:
            raise OrchestrationError("Existing Prepare dispatch marker lacks exact Deploy evidence; reconciliation required")
        return {"state": "TASK_SCOPED_AUTO_EVENT_ALREADY_DISPATCHED", "candidate_sha": sha,
                "authorization_id": authorization_id}
    if claim_exists and not dispatches:
        raise OrchestrationError("Task-scoped claim has no dispatch marker; reconciliation required")
    for previous in dispatches:
        _prove_safe_prior_dispatch(api, previous, runs, authorization_id, baseline,
                                   repo_root=repo_root)
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
