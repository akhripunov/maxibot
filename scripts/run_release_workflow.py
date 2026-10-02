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
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.prepared_image_archive import inspect_prepared_archive
except ModuleNotFoundError:  # Direct script execution sets sys.path to scripts/.
    from prepared_image_archive import inspect_prepared_archive

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MIGRATION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")


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
        "production_changed": False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "github_run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "github_actor": os.environ.get("GITHUB_ACTOR", ""),
    }
    try:
        if not SHA_RE.fullmatch(sha):
            raise ValueError("A full 40-character candidate SHA is required")
        if not MIGRATION_RE.fullmatch(migration):
            raise ValueError("An explicit target Alembic revision is required")
        if mode not in {"preflight", "production"}:
            raise ValueError("An explicit preflight or production mode is required")
        if mode == "production" and not approved:
            raise ValueError("Production requires explicit owner approval")
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
        required = {"private_docker_rehearsal": "PASS", "private_docker_version": "29.1.3", "private_store": "containerd", "private_snapshotter": "overlayfs", "private_network_isolated": "YES", "rehearsal_candidate_sha": sha, "rehearsal_prepare_run_id": os.environ.get("PREPARE_RUN_ID", ""), "rehearsal_archive_sha256": manifest.get("archive_sha256")}
        if any(rehearsal.get(key) != value for key, value in required.items()):
            raise RuntimeError("Prepare private Docker rehearsal receipt is missing or failed")
        archive_sha256 = checksum(archive)
        if manifest.get("candidate_sha") != sha or manifest.get("prepare_run_id") != os.environ.get("PREPARE_RUN_ID"):
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
        result["stage_seconds"] = {"PREFLIGHT": round(time.monotonic() - preflight_started, 3)}
        if dry_code != 0 or "SAFE_TO_RELEASE=YES" not in dry_output or "DRY_RUN_RESULT=PASS" not in dry_output:
            if "NETWORK_UNAVAILABLE" in dry_output:
                result["network_status"] = "NETWORK_UNAVAILABLE"
                raise RuntimeError("NETWORK_UNAVAILABLE during canonical production preflight")
            raise RuntimeError("Canonical production preflight did not report SAFE_TO_RELEASE=YES")
        result["preflight"] = "PASS"
        result["production_changed"] = False

        if mode == "preflight":
            result["status"] = "PLANNED"
        else:
            env["OWNER_APPROVED"] = "1"
            apply_started = time.monotonic()
            apply_code, apply_output = run(["bash", "scripts/release_prod.sh", sha, "--apply"], env)
            result["stage_seconds"]["APPLY"] = round(time.monotonic() - apply_started, 3)
            for name, seconds in re.findall(r"STAGE_SECONDS_([A-Z_]+)=([0-9.]+)", apply_output):
                result["stage_seconds"][name] = float(seconds)
            if "MIGRATION_ALLOWED=YES" not in apply_output:
                result["status"] = "FAILED_PRE_SWITCH"
                result["production_changed"] = False
            else:
                result["status"] = "RECOVERY_REQUIRED"
                result["production_changed"] = "POSSIBLE"
            if apply_code != 0 or "RELEASE_STATUS=PASS" not in apply_output:
                if "NETWORK_UNAVAILABLE" in apply_output and result["status"] == "FAILED_PRE_SWITCH":
                    result["network_status"] = "NETWORK_UNAVAILABLE"
                raise RuntimeError("Canonical apply did not report RELEASE_STATUS=PASS; reconcile live production")
            result["status"] = "COMPLETE"
            result["production_changed"] = True
            result["final_runtime_sha"] = sha
            result["final_migration"] = migration
        return_code = 0
    except (OSError, subprocess.CalledProcessError, ValueError, RuntimeError, KeyError) as exc:
        result["error"] = str(exc)
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
