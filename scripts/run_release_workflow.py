#!/usr/bin/env python3
"""Run the canonical MAXIBOT dry-run and, with explicit approval, apply."""

from __future__ import annotations

import json
import os
import re
try:
    from scripts.prepared_artifact_cache import checksum
except ModuleNotFoundError:
    from prepared_artifact_cache import checksum
import subprocess
import time
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.prepared_image_archive import inspect_prepared_archive
except ModuleNotFoundError:  # Direct script execution sets sys.path to scripts/.
    from prepared_image_archive import inspect_prepared_archive

from release_authorization import (AuthorizationError, authorization_state_ref,
                                   candidate_scope_diff, record_path,
                                   validate_task_authorization, assert_task_scoped_no_migration)

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MIGRATION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
EVIDENCE_KEYS = {
    "TARGET_SHA", "TARGET_MIGRATION", "TARGET_DOWN_REVISION", "IMAGE_READY",
    "IMAGE_PLATFORM", "IMAGE_SOURCE_SHA", "IMAGE_CONFIG_DIGEST", "IMAGE_ARCHIVE_SHA256",
    "API_WORKER_IMAGE_PARITY", "SSH_REMOTE_PREFLIGHT", "PROD_DB_HEAD", "PROD_READY",
    "PROD_API_IMAGE", "PROD_WORKER_IMAGE", "PROD_WORKER_HEALTH", "PROD_WRITE_FLAGS",
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
    if require_claim and (claim is None or claim.get("object", {}).get("sha") != candidate_sha):
        raise ValueError("Task authorization has no exact-candidate atomic Deploy claim")
    if not require_claim and claim is not None:
        raise ValueError("Task authorization already has a production claim")
    proof["source_reference"] = record["source_reference"]
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
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status != 201:
                raise ValueError("Durable TASK_SCOPED_AUTO consumption record was not created")
    except urllib.error.HTTPError as error:
        if error.code != 422:
            raise ValueError(f"TASK_SCOPED_AUTO consumption write failed (HTTP {error.code})") from None
        from validate_release_dispatch import github_get
        existing = github_get("git/ref/tags/" + ref.removeprefix("refs/tags/"), token)
        if existing.get("object", {}).get("sha") != candidate_sha:
            raise ValueError("TASK_SCOPED_AUTO consumption marker conflicts with candidate") from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        try:
            from validate_release_dispatch import github_get
            existing = github_get("git/ref/tags/" + ref.removeprefix("refs/tags/"), token)
        except Exception:
            raise ValueError("TASK_SCOPED_AUTO consumption response was lost and marker readback failed") from None
        if existing.get("object", {}).get("sha") != candidate_sha:
            raise ValueError(f"TASK_SCOPED_AUTO consumption write failed ({type(error).__name__})") from None


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
        "prepare_release_tag": os.environ.get("PREPARE_RELEASE_TAG", ""),
        "prepare_release_asset_id": os.environ.get("PREPARE_RELEASE_ASSET_ID", ""),
        "prepare_release_asset_sha256": os.environ.get("PREPARE_RELEASE_ASSET_SHA256", ""),
        "cache_prevented_download": os.environ.get("CACHE_PREVENTED_DOWNLOAD", "NO"),
        "bundle_download_seconds": os.environ.get("BUNDLE_DOWNLOAD_SECONDS", "0"),
    }
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
        archive = Path(os.environ.get("IMAGE_ARCHIVE", ""))
        manifest_path = Path(os.environ.get("IMAGE_MANIFEST", ""))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        image_result_path = Path(os.environ.get("IMAGE_RESULT", ""))
        rehearsal = json.loads(image_result_path.read_text(encoding="utf-8")) if image_result_path.is_file() else {}
        prepare_attempt = os.environ.get("PREPARE_RUN_ATTEMPT", "")
        required = {"schema": "MAXIBOT_PREPARE_RECEIPT_V1", "candidate_sha": sha,
                    "github_run_id": os.environ.get("PREPARE_RUN_ID", ""),
                    "github_run_attempt": prepare_attempt, "status": "READY", "tests": "success",
                    "frozen_path_guard": "success", "linux_amd64_image": "success",
                    "private_docker_rehearsal": "PASS", "private_docker_version": "29.1.3",
                    "private_store": "containerd", "private_snapshotter": "overlayfs",
                    "private_network_isolated": "YES", "rehearsal_candidate_sha": sha,
                    "rehearsal_prepare_run_id": os.environ.get("PREPARE_RUN_ID", ""),
                    "rehearsal_archive_sha256": manifest.get("archive_sha256")}
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
        archive_sha256 = checksum(archive)
        if (manifest.get("candidate_sha") != sha
                or manifest.get("prepare_run_id") != os.environ.get("PREPARE_RUN_ID")
                or str(manifest.get("prepare_run_attempt")) != prepare_attempt):
            raise RuntimeError("Prepared image provenance does not match selected candidate/Prepare run")
        if manifest.get("archive_sha256") != archive_sha256 or manifest.get("platform") != "linux/amd64":
            raise RuntimeError("Prepared image archive digest/platform verification failed")
        image_id, platform = inspect_prepared_archive(archive, image, sha)
        if image_id != manifest.get("image_id") or platform != "linux/amd64":
            raise RuntimeError("Prepared image archive config digest/platform/source SHA verification failed")
        env = os.environ.copy()
        env["API_CANDIDATE_IMAGE"] = image
        env["WORKER_CANDIDATE_IMAGE"] = image
        env["RELEASE_IMAGE_ARCHIVE"] = str(archive)
        env["RELEASE_IMAGE_ID"] = manifest["image_id"]
        env["RELEASE_IMAGE_ARCHIVE_SHA256"] = archive_sha256
        result["image_platform"] = "linux/amd64"
        result["image_source_sha"] = sha
        result["image_config_digest"] = manifest["image_id"]
        result["image_archive_sha256"] = archive_sha256

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
            env["OWNER_APPROVED"] = "1"
            result["release_state"] = "DEPLOYING"
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
                except ValueError:
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
