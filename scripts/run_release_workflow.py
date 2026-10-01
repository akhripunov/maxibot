#!/usr/bin/env python3
"""Run the canonical MAXIBOT dry-run and, with explicit approval, apply."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MIGRATION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")


def run(command: list[str], env: dict[str, str]) -> tuple[int, str]:
    result = subprocess.run(command, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(result.stdout, end="", flush=True)
    return result.returncode, result.stdout


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
        build = [
            "docker", "build", "--platform", "linux/amd64",
            "--label", f"org.opencontainers.image.revision={sha}",
            "--label", f"org.opencontainers.image.source-sha={sha}",
            "--label", f"org.maxibot.release.sha={sha}",
            "--tag", image, ".",
        ]
        build_code, _ = run(build, os.environ.copy())
        if build_code != 0:
            raise RuntimeError("Candidate linux/amd64 image build failed")
        inspect = subprocess.check_output(
            ["docker", "image", "inspect", image, "--format", "{{.Os}}/{{.Architecture}}|{{index .Config.Labels \"org.opencontainers.image.revision\"}}|{{index .Config.Labels \"org.opencontainers.image.source-sha\"}}|{{index .Config.Labels \"org.maxibot.release.sha\"}}"],
            text=True,
        ).strip()
        if inspect != f"linux/amd64|{sha}|{sha}|{sha}":
            raise RuntimeError("Candidate image platform/source SHA verification failed")
        result["image_platform"] = "linux/amd64"
        result["image_source_sha"] = sha

        env = os.environ.copy()
        env["TARGET_MIGRATION"] = migration
        dry_code, dry_output = run(["bash", "scripts/release_prod.sh", sha, "--dry-run"], env)
        if dry_code != 0 or "SAFE_TO_RELEASE=YES" not in dry_output or "DRY_RUN_RESULT=PASS" not in dry_output:
            raise RuntimeError("Canonical production preflight did not report SAFE_TO_RELEASE=YES")
        result["preflight"] = "PASS"
        result["production_changed"] = False

        if mode == "preflight":
            result["status"] = "PLANNED"
        else:
            env["OWNER_APPROVED"] = "1"
            apply_code, apply_output = run(["bash", "scripts/release_prod.sh", sha, "--apply"], env)
            if "MIGRATION_ALLOWED=YES" not in apply_output:
                result["status"] = "FAILED_PRE_SWITCH"
                result["production_changed"] = False
            else:
                result["status"] = "RECOVERY_REQUIRED"
                result["production_changed"] = "POSSIBLE"
            if apply_code != 0 or "RELEASE_STATUS=PASS" not in apply_output:
                raise RuntimeError("Canonical apply did not report RELEASE_STATUS=PASS; reconcile live production")
            result["status"] = "COMPLETE"
            result["production_changed"] = True
            result["final_runtime_sha"] = sha
            result["final_migration"] = migration
        return_code = 0
    except (OSError, subprocess.CalledProcessError, ValueError, RuntimeError) as exc:
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
