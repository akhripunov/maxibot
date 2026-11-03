#!/usr/bin/env python3
"""MAXIBOT Release Orchestrator V3: conservative impact-based CI.

Only immutable verified production results may establish the baseline. All
unknown/unsafe/mixed changes force FULL. Production guards never get skipped.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SHA_RE = re.compile(r"^[a-f0-9]{40}$")
VERSION = 1
SOURCE = "maxibot-immutable-production-evidence"
UI_FILES = frozenset({
    "miniapp/index.html", "miniapp/owner.css",
    "miniapp/style.css", "miniapp/tuning.css",
})
UI_TESTS = (
    "tests/test_maxibot_impact_v3.py",
    "tests/test_owner_miniapp.py",
    "tests/test_landing_ui_contract.py",
    "tests/test_public_site.py",
    "tests/test_web_admin_ui.py",
    "tests/test_web_admin_passkey_contract.py",
    "tests/test_frozen_path_guard.py",
    "tests/test_release_prod_tooling.py",
    "tests/test_release_assets.py",
    "tests/test_prepared_artifact_cache.py",
    "tests/test_verify_prepared_image.py",
    "tests/test_verify_loaded_prepared_image.py",
    "tests/test_migration_rehearsal.py",
    "tests/test_worker_health_preflight.py",
    "tests/test_runtime_entrypoints.py",
    "tests/test_deploy_platform_guard.py",
)


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo)


def changed_paths(repo, baseline, candidate):
    if not SHA_RE.fullmatch(baseline or "") or not SHA_RE.fullmatch(candidate or ""):
        raise ValueError("Exact SHA required")
    subprocess.run(["git", "merge-base", "--is-ancestor", baseline, candidate],
                   cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    paths = sorted({p.decode() for p in git(repo, "diff", "--no-renames", "--name-only",
                                           "-z", baseline, candidate, "--").split(b"\x00") if p})
    if any(not p or p.startswith("/") or p.startswith("../") or "/../" in p or
           "/./" in p for p in paths):
        raise ValueError("Unsafe source path")
    return paths


def plan(candidate, baseline=None, paths=None, run_id=None, attempt=None, reason="unknown"):
    if not SHA_RE.fullmatch(candidate or ""):
        raise ValueError("Invalid candidate SHA")
    files = sorted(set(paths or []))
    proof = (reason == "immutable-production-verified"
             and bool(SHA_RE.fullmatch(baseline or ""))
             and baseline != candidate and bool(files)
             and str(run_id or "").isdigit() and str(attempt or "").isdigit())
    ui = bool(proof and all(p in UI_FILES for p in files))
    return {
        "schema": "MAXIBOT_IMPACT_PLAN_V3",
        "version": VERSION,
        "candidate_sha": candidate,
        "classification": "UI_ONLY" if ui else "FULL",
        "reason": "verified-unchanged-python" if ui else
                  ("non-ui-or-mixed-change" if proof else reason),
        "baseline_source": SOURCE if proof else None,
        "production_sha": baseline if proof else None,
        "production_run_id": str(run_id) if proof else None,
        "production_run_attempt": str(attempt) if proof else None,
        "changed_paths": files if proof else [],
        "test_scope": "UI_ONLY" if ui else "FULL",
        "required_invariants": [
            "frozen_paid_traffic_guard", "exact_sha_build", "image_sha_labels",
            "private_docker_rehearsal", "immutable_prepare_receipt",
            "target_alembic", "ads_write_flags_off", "worker_memory_health",
            "production_preflight", "owner_approval", "postdeploy_evidence",
        ],
    }


def latest_verified_production():
    # Reuse canonical MAXIBOT publisher's *immutable evidence* verifier.
    # It validates the Release tag/ref, ZIP manifest, SHA, asset digests and
    # COMPLETE result; do not trust workflow success or a Linear string alone.
    from publish_release_to_linear import github_json, latest_runs, read_result_artifact
    runs = latest_runs("maxibot-deploy.yml")
    applicable = sorted(
        [r for r in runs if re.search(
            r"(?:^|\s)(?:production|publication-recovery)\s",
            r.get("name") or r.get("display_title") or "", re.I)],
        key=lambda r: (r.get("created_at", ""), int(r.get("id", 0))), reverse=True,
    )
    if not applicable:
        raise ValueError("No provenance-bound production evidence")
    newest = applicable[0]
    if newest.get("status") != "completed" or newest.get("conclusion") != "success":
        raise ValueError("Unresolved or failed latest production attempt")
    sha = newest.get("head_sha")
    if not SHA_RE.fullmatch(sha or ""):
        raise ValueError("Production run missing exact SHA")
    run_id = str(newest.get("id", ""))
    attempt = str(newest.get("run_attempt", ""))
    if not run_id.isdigit() or not attempt.isdigit():
        raise ValueError("Invalid production run identity")
    from release_assets import release_tag
    release = github_json(
        f"repos/akhripunov/maxibot/releases/tags/{release_tag('evidence',sha,run_id,attempt)}")
    if release.get("immutable") is not True or release.get("draft") is not False:
        raise ValueError("Latest production lacks immutable evidence")
    result = read_result_artifact(newest)
    if (result.get("status") != "COMPLETE"
            or result.get("candidate_sha") != sha
            or result.get("final_runtime_sha") != sha
            or result.get("github_run_id") != run_id
            or str(result.get("github_run_attempt")) != attempt
            or result.get("mode") not in ("production", "publication-recovery")
            or (result.get("mode") == "publication-recovery"
                and result.get("recovery") != "READ_ONLY_RECONCILIATION_PASS")):
        raise ValueError("Production immutable result does not prove current runtime")
    return sha, run_id, attempt


def build_plan(repo, candidate, ref, token):
    if ref != "refs/heads/main":
        return plan(candidate, reason="non-production-branch")
    try:
        if not token or git(repo, "rev-parse", "HEAD").decode().strip() != candidate:
            raise ValueError("Invalid checkout identity/token")
        if git(repo, "status", "--porcelain").strip():
            raise ValueError("Dirty checkout")
        baseline, run_id, attempt = latest_verified_production()
        paths = changed_paths(repo, baseline, candidate)
        return plan(candidate, baseline, paths, run_id, attempt,
                    reason="immutable-production-verified")
    except Exception as exc:
        print("MAXIBOT_V3_PROVENANCE_UNAVAILABLE=" + type(exc).__name__, flush=True)
        return plan(candidate, reason="canonical-production-proof-unavailable")


def verify_plan(proof, repo, candidate):
    if proof.get("schema") != "MAXIBOT_IMPACT_PLAN_V3" or proof.get("version") != VERSION:
        raise ValueError("V3 plan schema mismatch")
    if proof.get("candidate_sha") != candidate:
        raise ValueError("V3 candidate SHA mismatch")
    if proof.get("classification") == "FULL":
        if proof.get("test_scope") != "FULL":
            raise ValueError("FULL cannot use partial test scope")
        return "FULL"
    if proof.get("classification") != "UI_ONLY":
        raise ValueError("Unknown impact classification")
    base = proof.get("production_sha")
    if not SHA_RE.fullmatch(base or ""):
        raise ValueError("Missing production proof")
    try:
        files = changed_paths(repo, base, candidate)
    except (OSError, ValueError, subprocess.CalledProcessError):
        raise ValueError("V3 production diff cannot be independently verified") from None
    expected = plan(candidate, base, files, proof.get("production_run_id"),
                    proof.get("production_run_attempt"), reason="immutable-production-verified")
    if proof != expected or expected["classification"] != "UI_ONLY":
        raise ValueError("V3 immutable impact claim does not match exact source")
    return "UI_ONLY"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    options = parser.add_mutually_exclusive_group(required=True)
    options.add_argument("--output")
    options.add_argument("--run-tests")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    if args.output:
        evidence = build_plan(repo, args.candidate, os.getenv("GITHUB_REF"), os.getenv("GITHUB_TOKEN"))
        out = Path(args.output).resolve()
        if os.path.commonpath([str(repo), str(out)]) == str(repo):
            raise ValueError("Write impact evidence outside Git checkout")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        print(f"MAXIBOT_V3_IMPACT={evidence['classification']} REASON={evidence['reason']}", flush=True)
        print(f"MAXIBOT_V3_DIFF_COUNT={len(evidence['changed_paths'])}", flush=True)
        return 0
    evidence = json.loads(Path(args.run_tests).read_text())
    scope = verify_plan(evidence, repo, args.candidate)
    cmd = [sys.executable, "-m", "pytest", "-q"]
    if scope == "UI_ONLY":
        for target in UI_TESTS:
            if not (repo / target).is_file():
                raise ValueError(f"Required UI/security test missing: {target}")
        cmd.extend(UI_TESTS)
    print(f"MAXIBOT_V3_TEST_SELECTION={scope}", flush=True)
    return subprocess.call(cmd, cwd=repo)


if __name__ == "__main__":
    sys.exit(main())
