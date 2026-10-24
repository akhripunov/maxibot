#!/usr/bin/env python3
"""Read and reconcile one exact MAXIBOT V5 Deploy attempt without mutation."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ghcr_release_artifacts import (DIGEST, SHA40, ArtifactError,
    read_deploy_evidence)
from release_authorization import AuthorizationError, authorization_state_ref
from release_network_retry import RetryBudget
from validate_release_dispatch import ValidationError, github_get

REPOSITORY = "akhripunov/maxibot"
DEPLOY_WORKFLOW_PATH = ".github/workflows/maxibot-deploy.yml"
ENVIRONMENT = "maxibot-production"
MIGRATION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")


class DiagnosticError(RuntimeError):
    """Historical release evidence is missing, ambiguous, or inconsistent."""


def _exact_ref_state(authorization_id: str, state: str, sha: str,
                     token: str, budget: RetryBudget) -> dict:
    ref = authorization_state_ref("maxibot", authorization_id, state).removeprefix("refs/")
    try:
        value = github_get(f"git/ref/{ref}", token, budget=budget)
    except ValidationError as exc:
        if "HTTP 404" in str(exc):
            return {"present": False, "sha": None}
        raise DiagnosticError(f"Authorization {state} ref could not be read") from None
    obj = value.get("object") if isinstance(value, dict) else None
    target = obj.get("sha") if isinstance(obj, dict) else None
    if not isinstance(target, str) or not SHA40.fullmatch(target):
        raise DiagnosticError(f"Authorization {state} ref has no exact commit target")
    return {"present": True, "sha": target, "matches_candidate": target == sha}


def _deployment_payload(value: object) -> dict | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _deployment_matches(item: object, *, sha: str, run_id: str, attempt: str,
                        prepare_digest: str, target_migration: str,
                        mode: str) -> bool:
    if not isinstance(item, dict):
        return False
    payload = _deployment_payload(item.get("payload"))
    return (item.get("ref") == sha and item.get("sha") == sha
            and item.get("environment") == ENVIRONMENT
            and item.get("task") == "maxibot-production-deploy"
            and isinstance(payload, dict)
            and payload.get("candidate_sha") == sha
            and payload.get("target_migration") == target_migration
            and payload.get("mode") == mode
            and str(payload.get("workflow_run_id")) == run_id
            and str(payload.get("workflow_run_attempt")) == attempt
            and payload.get("prepare_receipt_digest") == prepare_digest)


def _all_deployments(sha: str, token: str, budget: RetryBudget, api_get) -> list[dict]:
    result: list[dict] = []
    for page in range(1, 11):
        query = urlencode({"sha": sha, "environment": ENVIRONMENT,
                           "per_page": "100", "page": str(page)})
        items = api_get(f"deployments?{query}", token, budget=budget)
        if not isinstance(items, list):
            raise DiagnosticError("GitHub Deployment GET returned an invalid response")
        result.extend(item for item in items if isinstance(item, dict))
        if len(items) < 100:
            return result
    raise DiagnosticError("GitHub Deployment pagination exceeded the diagnostic limit")


def diagnose(*, sha: str, run_id: str, attempt: str, evidence_digest: str,
             prepare_digest: str, authorization_id: str, token: str,
             actor: str, destination: Path, api_get=github_get) -> dict:
    if (not SHA40.fullmatch(sha) or not DIGEST.fullmatch(evidence_digest)
            or not DIGEST.fullmatch(prepare_digest)
            or not re.fullmatch(r"[1-9][0-9]{0,19}", run_id)
            or not re.fullmatch(r"[1-9][0-9]{0,5}", attempt)
            or not token or not actor):
        raise DiagnosticError("Explicit SHA/run/attempt/digests and GitHub read credentials are required")
    budget = RetryBudget()
    run = api_get(f"actions/runs/{run_id}/attempts/{attempt}", token, budget=budget)
    if (not isinstance(run, dict) or str(run.get("id")) != run_id
            or str(run.get("run_attempt")) != attempt or run.get("head_sha") != sha
            or run.get("head_branch") != "main" or run.get("event") != "workflow_dispatch"
            or run.get("path") != DEPLOY_WORKFLOW_PATH):
        raise DiagnosticError("Original GitHub Deploy run identity does not match explicit inputs")

    evidence = read_deploy_evidence(sha, run_id, attempt, evidence_digest,
        destination, actor, token)
    if (str(evidence.get("github_run_id")) != run_id
            or str(evidence.get("github_run_attempt")) != attempt):
        raise DiagnosticError("Original release evidence run/attempt differs from explicit inputs")
    mode = evidence.get("mode")
    target_migration = evidence.get("target_migration")
    if (mode not in {"production", "task-scoped-auto", "publication-recovery"}
            or not isinstance(target_migration, str)
            or not MIGRATION_RE.fullmatch(target_migration)):
        raise DiagnosticError("Original release evidence has an invalid mode or migration identity")

    deployments = _all_deployments(sha, token, budget, api_get)
    matches = [item for item in deployments if _deployment_matches(item, sha=sha,
        run_id=run_id, attempt=attempt, prepare_digest=prepare_digest,
        target_migration=target_migration, mode=mode)]
    evidence_id = evidence.get("github_deployment_id")
    evidence_state = evidence.get("github_deployment_state")
    if len(matches) > 1:
        raise DiagnosticError("Multiple GitHub Deployments match the exact original attempt")
    if not matches:
        if evidence_id is not None or evidence_state is not None:
            raise DiagnosticError("Evidence names a GitHub Deployment that does not match the original attempt")
        deployment_id = None
        latest_state = None
        deployment_api_state = "NOT_CREATED"
    else:
        deployment = matches[0]
        deployment_id = deployment.get("id")
        if type(deployment_id) is not int or deployment_id <= 0 or evidence_id != deployment_id:
            raise DiagnosticError("Deployment ID does not match immutable evidence")
        statuses = api_get(f"deployments/{deployment_id}/statuses?per_page=100", token, budget=budget)
        if not isinstance(statuses, list):
            raise DiagnosticError("GitHub Deployment status GET returned an invalid response")
        latest_state = statuses[0].get("state") if statuses and isinstance(statuses[0], dict) else None
        if (latest_state not in {"queued", "in_progress", "success", "failure", "error", "inactive"}
                or evidence_state != latest_state):
            raise DiagnosticError("Original Deployment status is absent or differs from immutable evidence")
        deployment_api_state = latest_state

    claim = _exact_ref_state(authorization_id, "claimed", sha, token, budget)
    consumed = _exact_ref_state(authorization_id, "consumed", sha, token, budget)
    revoked = _exact_ref_state(authorization_id, "revoked", sha, token, budget)
    if revoked["present"] or (claim["present"] and not claim["matches_candidate"]):
        raise DiagnosticError("Authorization refs are revoked or point to a different candidate")

    preflight_evidence = evidence.get("preflight_evidence")
    if not isinstance(preflight_evidence, dict):
        preflight_evidence = {}
    return {
        "diagnostic": "PASS",
        "candidate_sha": sha,
        "github_run_id": run_id,
        "github_run_attempt": attempt,
        "evidence_digest": evidence_digest,
        "error": evidence.get("error"),
        "release_state": evidence.get("release_state"),
        "preflight": evidence.get("preflight"),
        "production_changed": evidence.get("production_changed"),
        "github_deployment_id": evidence_id,
        "github_deployment_state": evidence_state,
        "github_deployment_api_state": latest_state,
        "github_deployment_api_match": deployment_api_state,
        "authorization_id": authorization_id,
        "authorization_claim": claim,
        "authorization_consumed": evidence.get("authorization_consumed"),
        "authorization_consumed_ref": consumed,
        "authorization_revoked_ref": revoked,
        "production_sha_before_apply": (preflight_evidence.get("CURRENT_PRODUCTION_SHA")
            or preflight_evidence.get("PROD_RELEASE_SHA")),
        "production_db_head_before_apply": (preflight_evidence.get("CURRENT_PRODUCTION_DB_HEAD")
            or preflight_evidence.get("PROD_DB_HEAD")),
        "preflight_evidence": {key: preflight_evidence.get(key) for key in (
            "CURRENT_PRODUCTION_SHA", "CURRENT_PRODUCTION_DB_HEAD", "PROD_RELEASE_SHA",
            "PROD_DB_HEAD", "PROD_READY", "SAFE_TO_RELEASE", "DRY_RUN_RESULT")},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--evidence-digest", required=True)
    parser.add_argument("--prepare-receipt-digest", required=True)
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = diagnose(sha=args.candidate_sha, run_id=args.run_id,
            attempt=args.run_attempt, evidence_digest=args.evidence_digest,
            prepare_digest=args.prepare_receipt_digest,
            authorization_id=args.authorization_id,
            token=os.environ.get("GITHUB_TOKEN", ""),
            actor=os.environ.get("GITHUB_ACTOR", ""), destination=args.output)
        print(json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (ArtifactError, AuthorizationError, DiagnosticError, OSError, ValidationError) as exc:
        print(f"MAXIBOT_RELEASE_DIAGNOSTICS=BLOCKED REASON={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
