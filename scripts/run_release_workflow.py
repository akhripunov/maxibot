#!/usr/bin/env python3
"""Run the canonical MAXIBOT dry-run and, with explicit approval, apply."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
import time
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.release_network_retry import (NetworkUnavailable, RetryBudget,
                                               is_retryable_network_error, retry_network_read)
except ModuleNotFoundError:  # Direct script execution sets sys.path to scripts/.
    from release_network_retry import (NetworkUnavailable, RetryBudget,
                                       is_retryable_network_error, retry_network_read)

try:
    from scripts.ghcr_release_artifacts import IMAGE_REPOSITORY, validate_prepare_receipt
except ModuleNotFoundError:  # Direct script execution sets sys.path to scripts/.
    from ghcr_release_artifacts import IMAGE_REPOSITORY, validate_prepare_receipt

from release_authorization import (AuthorizationError, authorization_state_ref,
                                   candidate_scope_diff, record_path,
                                   validate_task_authorization, assert_task_scoped_no_migration, validate_claim_candidate)

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MIGRATION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
EVIDENCE_KEYS = {
    "TARGET_SHA", "TARGET_MIGRATION", "TARGET_DOWN_REVISION", "IMAGE_READY",
    "IMAGE_PLATFORM", "IMAGE_SOURCE_SHA", "IMAGE_CONFIG_DIGEST", "IMAGE_DIGEST", "IMAGE_REPOSITORY",
    "API_WORKER_IMAGE_PARITY", "SSH_REMOTE_PREFLIGHT", "PROD_DB_HEAD", "PROD_READY",
    "PROD_API_IMAGE", "PROD_WORKER_IMAGE", "PROD_RELEASE_SHA", "PROD_WORKER_HEALTH", "PROD_WRITE_FLAGS",
    "ROLLBACK_CONFIG", "MIGRATION_REHEARSAL", "ROLLBACK_READY", "PRODUCTION_REHEARSAL",
    "SAFE_TO_RELEASE", "RELEASE_READY", "DRY_RUN_RESULT", "BACKUP", "BACKUP_VALIDATION",
    "BACKUP_DISK_PREFLIGHT", "BACKUP_PATH", "CURRENT_PRODUCTION_SHA",
    "CURRENT_PRODUCTION_DB_HEAD", "PRE_APPLY_API_IMAGE", "PRE_APPLY_WORKER_IMAGE",
    "PRE_APPLY_IMAGE_PARITY", "MIGRATION_ALLOWED", "POSTDEPLOY_WORKER_HEALTH",
    "FINAL_RUNTIME_SHA", "FINAL_MIGRATION", "POLLING_ROLES", "RELEASE_STATUS",
}


def safe_evidence(output: str) -> dict[str, str]:
    """Keep a strict allowlist of canonical release facts; never store raw logs/env."""
    facts = {}
    for line in output.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in EVIDENCE_KEYS and "\n" not in value and "\r" not in value:
            facts[key] = value[:500]
    return facts


def run(command: list[str], env: dict[str, str]) -> tuple[int, str]:
    stage = "PREFLIGHT" if "--dry-run" in command else "APPLY"
    started = time.monotonic()
    print(f"RELEASE_STAGE={stage}_START", flush=True)
    chunks = []
    with subprocess.Popen(command, env=env, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, bufsize=1) as process:
        for line in process.stdout:
            print(line, end="", flush=True)
            chunks.append(line)
        code = process.wait()
    print(f"STAGE_SECONDS_{stage}={time.monotonic() - started:.3f}", flush=True)
    return code, ''.join(chunks)


def write_result(path: str, result: dict[str, object]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(target)


def deployment_api_retryable(error: BaseException) -> bool:
    return isinstance(error, NetworkUnavailable) or is_retryable_network_error(error)


def deployment_api_json(request: urllib.request.Request, budget: RetryBudget) -> object:
    remaining = budget.deadline - time.monotonic()
    if remaining <= 0:
        raise NetworkUnavailable("deployment_network_budget")
    with urllib.request.urlopen(request, timeout=min(30, remaining)) as response:
        completed = threading.Event()
        result: list[bytes] = []
        failure: list[BaseException] = []

        def read() -> None:
            try:
                result.append(response.read(1_048_577))
            except BaseException as error:
                failure.append(error)
            finally:
                completed.set()

        threading.Thread(target=read, name="maxibot-deployment-response-read", daemon=True).start()
        if not completed.wait(max(0.0, budget.deadline - time.monotonic())):
            try:
                response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
            except (AttributeError, OSError):
                pass
            try:
                response.close()
            except OSError:
                pass
            raise NetworkUnavailable("deployment_network_budget")
        if time.monotonic() >= budget.deadline:
            raise NetworkUnavailable("deployment_network_budget")
        if failure:
            raise failure[0]
        raw = result[0]
        if len(raw) > 1_048_576:
            raise ValueError("GitHub Deployment metadata response exceeded size limit")
        return json.loads(raw.decode("utf-8"))


def deployment_status_matches(deployment_id: int, state: str, description: str,
                              token: str, budget: RetryBudget) -> bool:
    request = urllib.request.Request(
        f"https://api.github.com/repos/akhripunov/maxibot/deployments/{deployment_id}/statuses?per_page=100",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"})
    statuses = deployment_api_json(request, budget)
    if not isinstance(statuses, list) or not statuses or not isinstance(statuses[0], dict):
        return False
    latest = statuses[0]
    return latest.get("state") == state and latest.get("description") == description[:140]


def update_github_deployment(deployment_id: int, state: str, description: str, token: str) -> None:
    if state not in {"in_progress", "success", "failure", "error"} or not token:
        raise ValueError("Invalid GitHub Deployment status request")
    request = urllib.request.Request(
        f"https://api.github.com/repos/akhripunov/maxibot/deployments/{deployment_id}/statuses",
        data=json.dumps({"state": state, "description": description[:140],
                         "environment_url": "https://maxibot.ru"}).encode("utf-8"),
        method="POST", headers={"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json"})
    budget = RetryBudget()
    started = time.monotonic()
    retries = 0
    try:
        while True:
            remaining = budget.deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("GitHub Deployment status write was not confirmed within the shared network budget")
            ambiguous = False
            try:
                with urllib.request.urlopen(request, timeout=min(30, remaining)) as response:
                    if response.status not in {200, 201}:
                        raise ValueError("GitHub Deployment status write was not confirmed")
                return
            except urllib.error.HTTPError as exc:
                if not deployment_api_retryable(exc):
                    raise ValueError(f"GitHub Deployment status write failed (HTTP {exc.code})") from None
                ambiguous = True
            except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as exc:
                if not deployment_api_retryable(exc):
                    raise ValueError("GitHub Deployment status write failed") from None
                ambiguous = True

            if ambiguous:
                try:
                    confirmed = retry_network_read(
                        lambda: deployment_status_matches(deployment_id, state, description, token, budget),
                        "deployment_status_readback", deployment_api_retryable, budget=budget)
                except urllib.error.HTTPError as exc:
                    raise ValueError(f"GitHub Deployment status readback failed (HTTP {exc.code})") from None
                except (NetworkUnavailable, urllib.error.URLError, TimeoutError,
                        socket.timeout, ConnectionError, OSError) as exc:
                    if not deployment_api_retryable(exc):
                        raise ValueError("GitHub Deployment status readback failed") from None
                    confirmed = False
                if confirmed:
                    return
                remaining = budget.deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("GitHub Deployment status write was not confirmed within the shared network budget")
                delay = min(60, 2 ** min(retries, 6), remaining)
                time.sleep(delay)
                retries += 1
    finally:
        budget.persist_interval(started, time.monotonic())


def find_deployment_after_ambiguous_create(sha: str, migration: str, mode: str,
                                          run_id: str, attempt: str,
                                          prepare_digest: str, token: str) -> int:
    url = ("https://api.github.com/repos/akhripunov/maxibot/deployments?sha="
           f"{sha}&environment=maxibot-production&per_page=100")
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            deployments = json.loads(response.read().decode("utf-8"))
    except Exception:
        raise ValueError("GitHub Deployment readback failed after an ambiguous create response") from None

    matches = []
    if isinstance(deployments, list):
        for deployment in deployments:
            payload = deployment.get("payload") if isinstance(deployment, dict) else None
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    continue
            deployment_id = deployment.get("id") if isinstance(deployment, dict) else None
            if (isinstance(payload, dict)
                    and type(deployment_id) is int and deployment_id > 0
                    and deployment.get("ref") == sha
                    and deployment.get("task") == "maxibot-production-deploy"
                    and deployment.get("environment") == "maxibot-production"
                    and payload.get("candidate_sha") == sha
                    and payload.get("target_migration") == migration
                    and payload.get("mode") == mode
                    and str(payload.get("workflow_run_id")) == run_id
                    and str(payload.get("workflow_run_attempt")) == attempt
                    and payload.get("prepare_receipt_digest") == prepare_digest):
                matches.append(deployment_id)
    if len(matches) != 1:
        raise ValueError("GitHub Deployment readback cannot confirm one exact Deployment")
    return matches[0]


def create_github_deployment(sha: str, migration: str, mode: str, token: str,
                             run_id: str, prepare_digest: str) -> int:
    # The caller independently verified the exact successful Prepare run,
    # immutable receipt/image digests and candidate SHA. Prepare is an Actions
    # Check Run, not a Commit Status context; V5 provenance remains required
    # before this request, while GitHub Deployment status checks are unused.
    body = {"ref": sha, "task": "maxibot-production-deploy",
        "environment": "maxibot-production", "auto_merge": False,
        "required_contexts": [],
        "description": f"MAXIBOT {mode} {sha[:12]}",
        "payload": {"candidate_sha": sha, "target_migration": migration, "mode": mode,
                    "workflow_run_id": run_id,
                    "workflow_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
                    "prepare_receipt_digest": prepare_digest}}
    request = urllib.request.Request(
        "https://api.github.com/repos/akhripunov/maxibot/deployments",
        data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Keep public Actions logs useful without copying a possibly private
        # GitHub API response body or headers into release evidence.
        raise ValueError(
            f"GitHub Deployment creation failed (HTTP {exc.code}); production apply was not started"
        ) from None
    except Exception:
        # POST is not retryable. A lost response may follow a successful create,
        # so reconcile the exact SHA/environment/run/attempt/receipt once by GET.
        return find_deployment_after_ambiguous_create(
            sha, migration, mode, run_id, os.environ.get("GITHUB_RUN_ATTEMPT", ""),
            prepare_digest, token)
    deployment_id = result.get("id") if isinstance(result, dict) else None
    if type(deployment_id) is not int or deployment_id <= 0:
        raise ValueError("GitHub Deployment creation returned no valid ID")
    return deployment_id


def start_github_deployment(sha: str, migration: str, mode: str, token: str,
                            run_id: str, prepare_digest: str,
                            result: dict[str, object]) -> int:
    deployment_id = create_github_deployment(sha, migration, mode, token, run_id, prepare_digest)
    result["github_deployment_id"] = deployment_id
    try:
        update_github_deployment(deployment_id, "in_progress", "MAXIBOT production apply started", token)
    except ValueError:
        result["github_deployment_state"] = "PUBLICATION_FAILED"
        raise
    result["github_deployment_state"] = "in_progress"
    return deployment_id


def find_recoverable_deployment(sha: str, run_id: str, attempt: str, token: str) -> int:
    budget = RetryBudget()
    url = ("https://api.github.com/repos/akhripunov/maxibot/deployments?sha="
           f"{sha}&environment=maxibot-production&per_page=100")
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    try:
        deployments = retry_network_read(
            lambda: deployment_api_json(request, budget), "deployment_recovery_read",
            deployment_api_retryable, budget=budget)
    except Exception as exc:
        raise ValueError("GitHub Deployment read failed during metadata-only recovery") from exc
    matches = []
    if isinstance(deployments, list):
        for deployment in deployments:
            payload = deployment.get("payload") if isinstance(deployment, dict) else None
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    continue
            if (isinstance(payload, dict) and deployment.get("ref") == sha
                    and deployment.get("environment") == "maxibot-production"
                    and str(payload.get("workflow_run_id")) == run_id
                    and str(payload.get("workflow_run_attempt")) == attempt
                    and isinstance(deployment.get("id"), int)):
                matches.append(deployment["id"])
    if len(matches) != 1:
        raise ValueError("Metadata-only recovery requires one exact original GitHub Deployment")
    status_url = f"https://api.github.com/repos/akhripunov/maxibot/deployments/{matches[0]}/statuses?per_page=100"
    request = urllib.request.Request(status_url, headers={"Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    try:
        statuses = retry_network_read(
            lambda: deployment_api_json(request, budget), "deployment_recovery_status_read",
            deployment_api_retryable, budget=budget)
    except Exception as exc:
        raise ValueError("Original GitHub Deployment status read failed") from exc
    if (not isinstance(statuses, list) or not statuses
            or statuses[0].get("state") not in {"failure", "error", "in_progress", "success"}):
        raise ValueError("Original GitHub Deployment is not in a recoverable status")
    return matches[0]


def verify_task_authorization(authorization_id: str, candidate_sha: str,
                              production_sha: str, *, require_claim: bool) -> dict[str, str]:
    from validate_release_dispatch import ValidationError, github_get

    record = json.loads(Path(record_path(authorization_id)).read_text(encoding="utf-8"))
    main_sha = (github_get("git/ref/heads/main", os.environ["GITHUB_TOKEN"])
                .get("object", {}).get("sha", ""))
    changed_paths = candidate_scope_diff(Path(__file__).resolve().parents[1],
                                         authorization_id,
                                         record.get("production_baseline_sha", ""),
                                         candidate_sha)
    proof = validate_task_authorization(
        record=record, project="maxibot", authorization_id=authorization_id,
        candidate_sha=candidate_sha, current_main_sha=main_sha,
        production_sha=production_sha, changed_paths=changed_paths)
    for state in ("revoked", "consumed"):
        ref = authorization_state_ref("maxibot", authorization_id, state).removeprefix("refs/tags/")
        try:
            github_get(f"git/ref/tags/{ref}", os.environ["GITHUB_TOKEN"])
        except ValidationError as error:
            if "HTTP 404" in str(error):
                continue
            raise
        raise ValueError(f"Task authorization is already {state}")
    claim_ref = authorization_state_ref("maxibot", authorization_id, "claimed").removeprefix("refs/tags/")
    try:
        claim = github_get(f"git/ref/tags/{claim_ref}", os.environ["GITHUB_TOKEN"])
    except ValidationError as error:
        if "HTTP 404" not in str(error):
            raise
        claim = None
    if require_claim:
        validate_claim_candidate(Path(__file__).resolve().parents[1], authorization_id, record, claim, candidate_sha)
    if not require_claim and claim is not None:
        raise ValueError("Task authorization already has a production claim")
    proof["source_reference"] = record.get("owner_task_reference", record.get("source_reference", ""))
    proof["_record"] = record
    proof["_changed_paths_list"] = changed_paths
    return proof


def consume_task_authorization(authorization_id: str, candidate_sha: str) -> None:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise ValueError("Existing workflow GITHUB_TOKEN is unavailable for durable consumption record")
    ref = authorization_state_ref("maxibot", authorization_id, "consumed")
    request = urllib.request.Request(
        "https://api.github.com/repos/akhripunov/maxibot/git/refs",
        data=json.dumps({"ref": ref, "sha": candidate_sha}).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
    from validate_release_dispatch import ValidationError, github_get
    budget = RetryBudget()
    for attempt in range(3):
        remaining = budget.deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("TASK_SCOPED_AUTO consumption network budget exhausted")
        retryable = False
        try:
            with urllib.request.urlopen(request, timeout=min(20, remaining)) as response:
                if response.status != 201:
                    raise ValueError("Durable TASK_SCOPED_AUTO consumption record was not created")
            return
        except urllib.error.HTTPError as error:
            if error.code != 422 and not is_retryable_network_error(error):
                raise ValueError(f"TASK_SCOPED_AUTO consumption write failed (HTTP {error.code})") from None
            retryable = error.code != 422
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            retryable = is_retryable_network_error(error)
        # The ref is immutable and its request bytes never change. A new write
        # is allowed only after an exact read proves it is still absent.
        try:
            existing = github_get("git/ref/tags/" + ref.removeprefix("refs/tags/"), token)
        except ValidationError as error:
            if "HTTP 404" not in str(error) or not retryable:
                raise ValueError("TASK_SCOPED_AUTO consumption marker readback unconfirmed") from None
            if attempt == 2:
                raise ValueError("TASK_SCOPED_AUTO consumption marker absent after bounded reconciliation") from None
            delay = min(2 ** attempt, max(0, budget.deadline - time.monotonic()))
            time.sleep(delay)
            continue
        except Exception:
            raise ValueError("TASK_SCOPED_AUTO consumption marker readback unconfirmed") from None
        if existing.get("object", {}).get("sha") != candidate_sha:
            raise ValueError("TASK_SCOPED_AUTO consumption marker conflicts with candidate") from None
        return


def main() -> int:
    sha = os.environ.get("CANDIDATE_SHA", "")
    migration = os.environ.get("TARGET_MIGRATION", "")
    mode = os.environ.get("RELEASE_MODE", "")
    approved = os.environ.get("APPROVE_PRODUCTION", "false").lower() == "true"
    result_path = os.environ.get("RELEASE_RESULT", "")
    result: dict[str, object] = {
        "schema": "MAXIBOT_RELEASE_WORKFLOW_RESULT_V1",
        "candidate_sha": sha,
        "target_migration": migration,
        "mode": mode,
        "status": "FAILED",
        "release_state": "PREFLIGHTING",
        "preflight": "BLOCKED",
        "authorization_mode": "TASK_SCOPED_AUTO" if mode == "task-scoped-auto" else "EXACT_SHA",
        "task_authorization_id": os.environ.get("TASK_AUTHORIZATION_ID", ""),
        "authorization_consumed": False,
        "authorization_source": os.environ.get("GITHUB_ACTOR", ""),
        "production_changed": False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "github_run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "reconciles_deploy_run_id": os.environ.get("RECONCILES_DEPLOY_RUN_ID", ""),
        "reconciles_deploy_run_attempt": os.environ.get("RECONCILES_DEPLOY_RUN_ATTEMPT", ""),
        "github_actor": os.environ.get("GITHUB_ACTOR", ""),
        "prepare_run_id": os.environ.get("PREPARE_RUN_ID", ""),
        "prepare_run_attempt": os.environ.get("PREPARE_RUN_ATTEMPT", ""),
        "prepare_receipt_digest": os.environ.get("PREPARE_RECEIPT_DIGEST", ""),
        "cache_prevented_download": os.environ.get("CACHE_PREVENTED_DOWNLOAD", "NO"),
        "bundle_download_seconds": os.environ.get("BUNDLE_DOWNLOAD_SECONDS", "0"),
    }
    deployment_id = None
    try:
        if not SHA_RE.fullmatch(sha):
            raise ValueError("A full 40-character candidate SHA is required")
        if not MIGRATION_RE.fullmatch(migration):
            raise ValueError("An explicit target Alembic revision is required")
        if mode not in {"preflight", "production", "task-scoped-auto", "publication-recovery"}:
            raise ValueError("An explicit preflight, production, or publication-recovery mode is required")
        if mode == "production" and not approved:
            raise ValueError("Production requires explicit owner approval")
        if mode == "task-scoped-auto" and (approved or not os.environ.get("TASK_AUTHORIZATION_ID", "").strip()):
            raise ValueError("TASK_SCOPED_AUTO requires a durable authorization ID and no exact-SHA approval input")
        if mode == "publication-recovery" and (
                approved or os.environ.get("APPROVE_WORKER_BOOTSTRAP", "false").lower() == "true"):
            raise ValueError("Publication recovery is read-only and cannot carry production/bootstrap approval")
        if not result_path:
            raise ValueError("RELEASE_RESULT path is required")

        checkout = Path(__file__).resolve().parents[1]
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=checkout, text=True).strip()
        if head != sha or dirty:
            raise ValueError("Release checkout must be clean and match the exact candidate SHA")

        image = f"maxibot-vps-api:{sha}"
        manifest_path = Path(os.environ.get("IMAGE_MANIFEST", ""))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        image_result_path = Path(os.environ.get("IMAGE_RESULT", ""))
        rehearsal = json.loads(image_result_path.read_text(encoding="utf-8")) if image_result_path.is_file() else {}
        prepare_attempt = os.environ.get("PREPARE_RUN_ATTEMPT", "")
        required = {"schema": "MAXIBOT_PREPARE_RECEIPT_V2", "candidate_sha": sha,
                    "github_run_id": os.environ.get("PREPARE_RUN_ID", ""),
                    "github_run_attempt": prepare_attempt, "status": "READY", "tests": "success",
                    "frozen_path_guard": "success", "linux_amd64_image": "success",
                    "ghcr_image_publish": "success",
                    "private_docker_rehearsal": "PASS", "private_docker_version": "29.1.3",
                    "private_store": "containerd", "private_snapshotter": "overlayfs",
                    "private_network_isolated": "YES", "rehearsal_candidate_sha": sha,
                    "rehearsal_prepare_run_id": os.environ.get("PREPARE_RUN_ID", ""),
                    "rehearsal_archive_sha256": rehearsal.get("rehearsal_archive_sha256")}
        if (any(rehearsal.get(key) != value for key, value in required.items())
                or str(manifest.get("prepare_run_attempt")) != prepare_attempt):
            raise RuntimeError("Prepare private Docker rehearsal receipt is missing or failed")
        # The Prepare receipt is immutable, but a UI-only selection must
        # prove unchanged backend sources again in the Deploy checkout.
        test_scope = rehearsal.get("test_scope", "FULL")  # Old V2 receipts mean FULL.
        if test_scope == "UI_ONLY":
            from maxibot_impact_v3 import verify_plan
            impact = rehearsal.get("impact_v3")
            if not isinstance(impact, dict):
                raise RuntimeError("UI_ONLY release missing immutable V3 impact evidence")
            try:
                assert verify_plan(impact, checkout, sha) == "UI_ONLY"
            except (ValueError, OSError, AssertionError) as exc:
                raise RuntimeError("V3 targeted Prepare scope was not independently verified") from exc
        elif test_scope != "FULL":
            raise RuntimeError("Unrecognized V3 regression coverage mode")
        result["test_scope"] = test_scope
        if (manifest.get("candidate_sha") != sha
                or manifest.get("prepare_run_id") != os.environ.get("PREPARE_RUN_ID")
                or str(manifest.get("prepare_run_attempt")) != prepare_attempt):
            raise RuntimeError("Prepared image provenance does not match selected candidate/Prepare run")
        receipt = validate_prepare_receipt(rehearsal, sha, os.environ.get("PREPARE_RUN_ID", ""), prepare_attempt)
        image_record = receipt["image"]
        if (manifest.get("image") != image_record or image_record.get("repository") != IMAGE_REPOSITORY
                or image_record.get("platform") != "linux/amd64"):
            raise RuntimeError("Prepare receipt image digest/config/platform proof is inconsistent")
        image_id = image_record["config_digest"]
        image_digest = image_record["digest"]
        if (image_digest != os.environ.get("RELEASE_IMAGE_DIGEST")
                or image_record.get("repository") != os.environ.get("RELEASE_IMAGE_REPOSITORY")
                or image_id != os.environ.get("RELEASE_IMAGE_ID")):
            raise RuntimeError("Selected GHCR image identity differs from exact Prepare receipt")
        env = os.environ.copy()
        env["API_CANDIDATE_IMAGE"] = image
        env["WORKER_CANDIDATE_IMAGE"] = image
        env["RELEASE_IMAGE_REPOSITORY"] = image_record["repository"]
        env["RELEASE_IMAGE_DIGEST"] = image_digest
        env["RELEASE_IMAGE_ID"] = image_id
        env["MAXIBOT_IMAGE_TRANSPORT"] = "GHCR_VPS_DIRECT"
        env["RELEASE_PREPARE_RECEIPT"] = os.environ.get("IMAGE_RESULT", "")
        result["image_platform"] = "linux/amd64"
        result["image_source_sha"] = sha
        result["image_config_digest"] = image_id
        result["image_repository"] = image_record["repository"]
        result["image_digest"] = image_digest
        result["image_transport"] = "ghcr-oci"

        bootstrap = os.environ.get("APPROVE_WORKER_BOOTSTRAP", "false").lower()
        if bootstrap not in {"true", "false"}:
            raise ValueError("Worker bootstrap approval input is invalid")
        env["WORKER_BOOTSTRAP_APPROVED"] = "1" if bootstrap == "true" else "0"
        result["worker_bootstrap_approved"] = bootstrap == "true"
        env["TARGET_MIGRATION"] = migration
        preflight_started = time.monotonic()
        dry_code, dry_output = run(["bash", "scripts/release_prod.sh", sha, "--dry-run"], env)
        result["preflight_evidence"] = safe_evidence(dry_output)
        if test_scope == "UI_ONLY":
            # Another release won after Prepare: do not enter production apply.
            baseline = impact["production_sha"]
            current = result["preflight_evidence"].get("CURRENT_PRODUCTION_SHA")
            if current != baseline:
                raise RuntimeError("V3 production baseline moved after targeted Prepare")
        result["stage_seconds"] = {"PREFLIGHT": round(time.monotonic() - preflight_started, 3)}
        if dry_code != 0 or "SAFE_TO_RELEASE=YES" not in dry_output or "DRY_RUN_RESULT=PASS" not in dry_output:
            result["preflight"] = "FAILED"
            if "NETWORK_UNAVAILABLE" in dry_output:
                result["network_status"] = "NETWORK_UNAVAILABLE"
                raise RuntimeError("NETWORK_UNAVAILABLE during canonical production preflight")
            raise RuntimeError("Canonical production preflight did not report SAFE_TO_RELEASE=YES")
        result["preflight"] = "PASS"
        result["production_changed"] = False
        result["status"] = "FAILED_PRE_SWITCH"
        result["release_state"] = "PRE_APPLY_VALIDATION"
        result["production_baseline_sha"] = result["preflight_evidence"].get("PROD_RELEASE_SHA", "")
        if not result["production_baseline_sha"]:
            result["production_baseline_sha"] = result["preflight_evidence"].get("CURRENT_PRODUCTION_SHA", "")
        result["db_migration_baseline"] = result["preflight_evidence"].get("PROD_DB_HEAD", "")
        if mode == "preflight" and os.environ.get("TASK_AUTHORIZATION_ID", "").strip():
            linked = verify_task_authorization(os.environ["TASK_AUTHORIZATION_ID"], sha,
                                               result["production_baseline_sha"], require_claim=False)
            assert_task_scoped_no_migration(migration, result["preflight_evidence"],
                                            record=linked["_record"],
                                            changed_paths=linked["_changed_paths_list"])
            result["task_authorization_record_sha256"] = linked["record_sha256"]
            result["task_authorization_source"] = linked["source_reference"]
            result["task_authorization_changed_paths"] = linked["changed_paths"]

        if mode == "preflight":
            result["status"] = "PLANNED"
            result["release_state"] = "READY_FOR_OWNER"
        elif mode == "publication-recovery":
            if (not os.environ.get("RECONCILES_DEPLOY_RUN_ID", "").isdigit()
                    or not os.environ.get("RECONCILES_DEPLOY_RUN_ATTEMPT", "").isdigit()):
                raise ValueError("Publication recovery requires the original Deploy run and attempt")
            evidence = result["preflight_evidence"]
            expected_image = f"maxibot-vps-api:{sha}"
            observed_flags = set(evidence.get("PROD_WRITE_FLAGS", "").split(","))
            expected_flags = {"ADS_AUTOPILOT_ENABLED=false", "ADS_WRITE_ACTIONS_ENABLED=false"}
            if (evidence.get("PROD_READY") != "ready"
                    or evidence.get("PROD_DB_HEAD") != migration
                    or evidence.get("PROD_API_IMAGE") != expected_image
                    or evidence.get("PROD_WORKER_IMAGE") != expected_image
                    or observed_flags != expected_flags
                    or evidence.get("PROD_WORKER_HEALTH") in (None, "ERROR")
                    or not evidence.get("ROLLBACK_CONFIG")
                    or evidence.get("ROLLBACK_READY") != "PASS"):
                raise RuntimeError("Read-only production reconciliation did not prove exact completed runtime state")
            result["status"] = "COMPLETE"
            result["production_changed"] = False
            result["final_runtime_sha"] = sha
            result["final_migration"] = migration
            result["recovery"] = "READ_ONLY_RECONCILIATION_PASS"
            original_run = os.environ["RECONCILES_DEPLOY_RUN_ID"]
            original_attempt = os.environ["RECONCILES_DEPLOY_RUN_ATTEMPT"]
            prior_deployment_id = find_recoverable_deployment(sha, original_run, original_attempt,
                                                              os.environ.get("GITHUB_TOKEN", ""))
            result["github_deployment_id"] = prior_deployment_id
            result["github_deployment_state"] = "success"
        else:
            task_authorization = None
            if mode == "task-scoped-auto":
                task_authorization = verify_task_authorization(
                    os.environ["TASK_AUTHORIZATION_ID"], sha, result["production_baseline_sha"],
                    require_claim=True)
                assert_task_scoped_no_migration(migration, result["preflight_evidence"],
                                                record=task_authorization["_record"],
                                                changed_paths=task_authorization["_changed_paths_list"])
                result["task_authorization_record_sha256"] = task_authorization["record_sha256"]
                result["task_authorization_source"] = task_authorization["source_reference"]
                result["task_authorization_changed_paths"] = task_authorization["changed_paths"]
            result["release_state"] = "APPROVED"
            if mode in {"production", "task-scoped-auto"}:
                deployment_id = start_github_deployment(
                    sha, migration, mode, os.environ.get("GITHUB_TOKEN", ""),
                    os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("PREPARE_RECEIPT_DIGEST", ""),
                    result)
            env["EXPECTED_PRODUCTION_SHA"] = result["production_baseline_sha"]
            env["EXPECTED_PRODUCTION_DB_HEAD"] = result["db_migration_baseline"]
            env["OWNER_APPROVED"] = "1"
            # Deploy workflow concurrency and release_prod's atomic Apply lock remain
            # authoritative. Re-read environment history here: Controller's earlier
            # task-ID check cannot authorize retry after an intervening failed Apply.
            from release_orchestrator import GitHubAPI, assert_production_history
            assert_production_history(GitHubAPI(os.environ.get("GITHUB_TOKEN", "")),
                checkout, result["production_baseline_sha"],
                current_run_id=os.environ.get("GITHUB_RUN_ID", ""),
                current_attempt=os.environ.get("GITHUB_RUN_ATTEMPT", ""),
                current_sha=sha, current_deployment_id=deployment_id)
            # Once execution can have started, missing process output is not proof
            # of a pre-switch failure. Only a returned canonical result can narrow it.
            result["status"] = "RECOVERY_REQUIRED"
            result["release_state"] = "RECOVERY_REQUIRED"
            result["production_changed"] = "POSSIBLE"
            apply_started = time.monotonic()
            apply_code, apply_output = run(["bash", "scripts/release_prod.sh", sha, "--apply"], env)
            result["production_evidence"] = safe_evidence(apply_output)
            result["stage_seconds"]["APPLY"] = round(time.monotonic() - apply_started, 3)
            for name, seconds in re.findall(r"STAGE_SECONDS_([A-Z_]+)=([0-9.]+)", apply_output):
                result["stage_seconds"][name] = float(seconds)
            if "MIGRATION_ALLOWED=YES" not in apply_output:
                result["status"] = "FAILED_PRE_SWITCH"
                result["production_changed"] = False
            else:
                result["status"] = "RECOVERY_REQUIRED"
                result["release_state"] = "RECOVERY_REQUIRED"
                result["production_changed"] = "POSSIBLE"
            if apply_code != 0 or "RELEASE_STATUS=PASS" not in apply_output:
                if "NETWORK_UNAVAILABLE" in apply_output and result["status"] == "FAILED_PRE_SWITCH":
                    result["network_status"] = "NETWORK_UNAVAILABLE"
                raise RuntimeError("Canonical apply did not report RELEASE_STATUS=PASS; reconcile live production")
            result["status"] = "COMPLETE"
            result["release_state"] = "COMPLETE"
            result["production_changed"] = True
            result["final_runtime_sha"] = sha
            result["final_migration"] = migration
            if mode == "task-scoped-auto":
                try:
                    consume_task_authorization(os.environ["TASK_AUTHORIZATION_ID"], sha)
                    result["task_authorization_state"] = "CONSUMED"
                    result["authorization_consumed"] = True
                except ValueError as error:
                    http_status = re.fullmatch(
                        r"TASK_SCOPED_AUTO consumption write failed \(HTTP ([0-9]{3})\)", str(error))
                    error_code = "HTTP_" + http_status.group(1) if http_status else "CONSUMPTION_UNCONFIRMED"
                    result["task_authorization_error_code"] = error_code
                    print(f"RELEASE_FINALIZATION_ERROR={error_code}", flush=True)
                    result["status"] = "RECOVERY_REQUIRED"
                    result["release_state"] = "RECOVERY_REQUIRED"
                    result["task_authorization_state"] = "CONSUMPTION_PENDING"
                    raise RuntimeError("Production completed but durable TASK_SCOPED_AUTO consumption needs reconciliation") from None
        return_code = 0
    except (OSError, subprocess.CalledProcessError, ValueError, RuntimeError, KeyError) as exc:
        result["error"] = str(exc)
        if result.get("release_state") not in {"RECOVERY_REQUIRED", "COMPLETE"}:
            result["release_state"] = "FAILED_PRE_SWITCH"
        return_code = 1
    finally:
        deployment_id = deployment_id or result.get("github_deployment_id")
        if deployment_id is not None:
            deployment_state = "success" if result.get("status") == "COMPLETE" else "failure"
            try:
                update_github_deployment(deployment_id, deployment_state,
                    f"MAXIBOT release {result.get('status', 'FAILED')}", os.environ.get("GITHUB_TOKEN", ""))
                result["github_deployment_state"] = deployment_state
            except ValueError:
                return_code = 1
                result["github_deployment_state"] = "PUBLICATION_FAILED"
                if result.get("status") == "COMPLETE":
                    result["status"] = "RECOVERY_REQUIRED"
                    result["release_state"] = "RECOVERY_REQUIRED"
                    result["error"] = "Production completed but GitHub Deployment status needs metadata-only reconciliation"
        if result_path:
            write_result(result_path, result)
        output_path = os.environ.get("GITHUB_OUTPUT", "")
        if output_path:
            try:
                with open(output_path, "a", encoding="utf-8") as output:
                    output.write(f"status={result['status']}\n")
                    output.write(f"preflight={result.get('preflight', 'FAILED')}\n")
                    output.write(f"production_changed={result['production_changed']}\n")
            except OSError:
                pass
        print(
            f"MAXIBOT_RELEASE_RESULT={result['status']} SHA={sha or 'INVALID'} "
            f"MODE={mode or 'INVALID'} PRODUCTION_CHANGED={result['production_changed']}"
        )
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
