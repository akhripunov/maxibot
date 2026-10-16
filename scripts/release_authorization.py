"""Validate durable, one-shot TASK_SCOPED_AUTO release authorizations."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
AUTH_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
MIGRATION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
REQUIRED_EXCLUSIONS = {
    "database", "migrations", "security", "secrets", "payments",
    "ads", "paid_traffic", "release_infrastructure",
}
OWNER_ONLY_PREFIXES = (
    ".github/", "alembic/", "migrations/", "db/", "deploy/", "docker/",
    "app/db/", "app/models/", "app/schemas/", "app/contracts/", "app/core/",
    "docs/release/", "scripts/release", "scripts/prepare_release",
    "scripts/validate_release", "scripts/publish_release", "app/ads/",
    "app/traffic/", "app/security/", "app/auth/", "app/payments/",
    "app/billing/", "payments/", "billing/", "app/integrations/yandex", "app/integrations/max/",
    "Dockerfile", "docker-compose", ".env", "secrets/",
)


class AuthorizationError(ValueError):
    pass


def _migration_contract(record: dict[str, Any], path: str) -> dict[str, Any]:
    contract = record.get("risk_contracts", {}).get("database_migration")
    gates = {"migration_rehearsal", "backup_before_migration", "rollback_readiness", "postdeploy_health"}
    if (not isinstance(contract, dict) or not re.fullmatch(r"alembic/versions/[^/]+\.py", path)
            or path not in contract.get("allowed_paths", [])
            or not isinstance(contract.get("target_migration"), str)
            or not MIGRATION_RE.fullmatch(contract["target_migration"])
            or not isinstance(contract.get("production_db_head"), str)
            or not MIGRATION_RE.fullmatch(contract["production_db_head"])
            or not isinstance(contract.get("risk_description"), str) or len(contract["risk_description"].strip()) < 20
            or not isinstance(contract.get("backup_plan"), str) or len(contract["backup_plan"].strip()) < 20
            or not isinstance(contract.get("rollback_plan"), str) or len(contract["rollback_plan"].strip()) < 20
            or not isinstance(contract.get("stop_conditions"), list) or len(contract["stop_conditions"]) < 2
            or any(not isinstance(item, str) or len(item.strip()) < 8 for item in contract["stop_conditions"])
            or not isinstance(contract.get("required_gates"), list) or not gates.issubset(contract["required_gates"])):
        raise AuthorizationError("Owner-only migration requires a complete pre-approved database risk contract")
    return contract


def assert_task_scoped_no_migration(target_migration: str, evidence: dict[str, str], *,
                                    record: dict[str, Any] | None = None,
                                    changed_paths: list[str] | None = None) -> dict[str, str]:
    """Allow pending migration only with a path/target-bound contract and canonical evidence."""
    paths = [path for path in (changed_paths or []) if path.startswith("alembic/versions/")]
    live_head = evidence.get("PROD_DB_HEAD", "")
    if not paths and target_migration and live_head == target_migration:
        return {"state": "NO_MIGRATION_CHANGE"}
    if not record or not paths:
        raise AuthorizationError("TASK_SCOPED_AUTO cannot apply database migrations without the exact pre-approved risk contract")
    contracts = [_migration_contract(record, path) for path in paths]
    if any(path not in record.get("allowed_paths", []) for path in paths):
        raise AuthorizationError("Migration changed path is outside the durable authorized scope")
    contract = contracts[0]
    if any(item != contract for item in contracts[1:]):
        raise AuthorizationError("Changed migrations do not share one exact risk contract")
    if (contract["target_migration"] != target_migration
            or contract["production_db_head"] != live_head
            or not str(evidence.get("MIGRATION_REHEARSAL", "")).startswith("PASS")
            or evidence.get("ROLLBACK_READY") != "PASS"
            or evidence.get("PROD_READY") != "ready"):
        raise AuthorizationError("Preflight migration target, production baseline or required safety evidence differs from the pre-approved contract")
    return {"state": "PREAPPROVED_MIGRATION", "target_migration": target_migration}


def authorization_state_ref(project: str, authorization_id: str, state: str) -> str:
    project = project.lower()
    if project not in {"maxibot", "gpoint"} or not AUTH_ID_RE.fullmatch(authorization_id):
        raise AuthorizationError("Invalid project-scoped authorization ID")
    if state not in {"revoked", "consumed", "claimed"}:
        raise AuthorizationError("Invalid authorization state marker")
    return f"refs/tags/{project}-task-auth/{authorization_id}/{state}"


def _in_scope(path: str, allowed_paths: list[str]) -> bool:
    return any(path == scope.rstrip("/") or
               (scope.endswith("/") and path.startswith(scope))
               for scope in allowed_paths)


def _owner_only(path: str) -> bool:
    normalized = path.strip("/").lower()
    return (normalized == "agata" or normalized.startswith("agata/")
            or any(normalized == prefix.rstrip("/").lower()
                   or normalized.startswith(prefix.lower())
                   for prefix in OWNER_ONLY_PREFIXES))


def validate_task_authorization(*, record: dict[str, Any], project: str,
                                authorization_id: str, candidate_sha: str,
                                current_main_sha: str, production_sha: str,
                                changed_paths: list[str]) -> dict[str, str]:
    """Fail closed unless the candidate is the exact active task and scope."""
    if not AUTH_ID_RE.fullmatch(authorization_id):
        raise AuthorizationError("Invalid task authorization ID")
    if not isinstance(record, dict) or record.get("schema_version") != 3:
        raise AuthorizationError("Unsupported task authorization record")
    if record.get("owner_instruction_scope") != "FULL_PRODUCTION_CYCLE":
        raise AuthorizationError("Original owner instruction must explicitly authorize the full production cycle")
    if record.get("authorization_id") != authorization_id:
        raise AuthorizationError("authorization ID does not match durable record")
    if record.get("project") != project or record.get("authorization_mode") != "TASK_SCOPED_AUTO":
        raise AuthorizationError("Authorization project or mode does not match")
    if record.get("state") != "ACTIVE" or record.get("revoked") is not False or record.get("consumed") is not False:
        raise AuthorizationError("Task authorization is revoked, consumed, or inactive")
    if not isinstance(record.get("source_reference"), str) or not re.match(
            r"^(linear:|https://linear\.app/)", record["source_reference"].strip(), re.IGNORECASE):
        raise AuthorizationError("A verifiable Linear owner-instruction reference is required")
    if not isinstance(record.get("task"), str) or not record["task"].strip():
        raise AuthorizationError("Task description is required")
    criteria = record.get("acceptance_criteria")
    if not isinstance(criteria, list) or not criteria or any(not isinstance(x, str) or not x.strip() for x in criteria):
        raise AuthorizationError("Acceptance criteria are required")
    exclusions = record.get("exclusions")
    if not isinstance(exclusions, list) or not REQUIRED_EXCLUSIONS.issubset(set(exclusions)):
        raise AuthorizationError("Required owner-only exclusions are missing")
    allowed = record.get("allowed_paths")
    if not isinstance(allowed, list) or not allowed or any(
            not isinstance(x, str) or not x.strip() or x.startswith("/") or ".." in x.split("/")
            for x in allowed):
        raise AuthorizationError("A safe non-empty allowed-path scope is required")
    baseline = record.get("production_baseline_sha")
    if not isinstance(baseline, str) or not SHA_RE.fullmatch(baseline):
        raise AuthorizationError("Exact production baseline SHA is required")
    if not SHA_RE.fullmatch(candidate_sha) or candidate_sha != current_main_sha:
        raise AuthorizationError("Candidate must be the exact current main SHA")
    if baseline != production_sha:
        raise AuthorizationError("Live production baseline drifted from the task authorization")
    if not isinstance(changed_paths, list) or not changed_paths:
        raise AuthorizationError("Candidate diff from production baseline is empty or unavailable")
    for path in changed_paths:
        if not isinstance(path, str) or not path or path.startswith("/") or ".." in path.split("/"):
            raise AuthorizationError("Candidate diff contains an invalid path")
        if _owner_only(path):
            _migration_contract(record, path)
            if path not in allowed:
                raise AuthorizationError("Migration path is not explicitly allowed")
            continue
        if not _in_scope(path, allowed):
            raise AuthorizationError(f"Candidate path is outside authorized scope: {path}")
    record_digest = hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {"authorization_id": authorization_id, "candidate_sha": candidate_sha,
            "production_baseline_sha": baseline, "record_sha256": record_digest,
            "changed_paths": ",".join(sorted(set(changed_paths)))}


def record_path(authorization_id: str) -> str:
    if not AUTH_ID_RE.fullmatch(authorization_id):
        raise AuthorizationError("Invalid task authorization ID")
    return f".release-authorizations/{authorization_id}.json"


def candidate_scope_diff(repo_root: Path, authorization_id: str,
                         baseline_sha: str, candidate_sha: str) -> list[str]:
    """Require the durable record to predate the first in-scope code change."""
    path = record_path(authorization_id)
    try:
        subprocess.run(["git", "merge-base", "--is-ancestor", baseline_sha, candidate_sha],
                       cwd=repo_root, check=True, capture_output=True, timeout=20)
        commits = subprocess.check_output(
            ["git", "rev-list", "--topo-order", "--reverse", "--ancestry-path", f"{baseline_sha}..{candidate_sha}"],
            cwd=repo_root, text=True, timeout=20).splitlines()
        all_changes: list[tuple[str, bool, str, str]] = []
        for commit in commits:
            parents = subprocess.check_output(["git", "rev-list", "--parents", "-n", "1", commit],
                                              cwd=repo_root, text=True, timeout=20).split()[1:]
            output = subprocess.check_output(
                ["git", "diff-tree", "--root", "--first-parent", "--no-commit-id", "--name-status", "-r", commit],
                cwd=repo_root, text=True, timeout=20)
            for line in output.splitlines():
                status, _, changed = line.partition("\t")
                if status and changed:
                    all_changes.append((commit, len(parents) > 1, status, changed))
        record_adds = [item for item in all_changes if item[3] == path and not item[1] and item[2] == "A"]
        if len(record_adds) != 1 or any(item[3] == path and item[2] != "A" and not item[1] for item in all_changes):
            raise AuthorizationError("Durable authorization record must be added once and remain unchanged")
        authorization_commit = record_adds[0][0]
        authorization_object = subprocess.check_output(
            ["git", "rev-parse", f"{authorization_commit}:{path}"], cwd=repo_root, text=True, timeout=20).strip()
        candidate_object = subprocess.check_output(
            ["git", "rev-parse", f"{candidate_sha}:{path}"], cwd=repo_root, text=True, timeout=20).strip()
        if authorization_object != candidate_object:
            raise AuthorizationError("Durable authorization record changed after it was added")
        scope_changes = [item for item in all_changes if item[3] != path]
        for commit, _merge, _status, _changed in scope_changes:
            if commit == authorization_commit:
                raise AuthorizationError("Durable authorization record must precede every implementation change")
            subprocess.run(["git", "merge-base", "--is-ancestor", authorization_commit, commit],
                           cwd=repo_root, check=True, capture_output=True, timeout=20)
        if not scope_changes:
            raise AuthorizationError("Candidate has no implementation changes beyond the authorization record")
        changed_paths = subprocess.check_output(
            ["git", "diff", "--name-only", baseline_sha, candidate_sha],
            cwd=repo_root, text=True, timeout=20).splitlines()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise AuthorizationError("Candidate diff from production baseline could not be proven") from error
    paths = sorted(set(changed_paths) - {path})
    if not paths:
        raise AuthorizationError("Candidate has no implementation changes beyond the authorization record")
    return paths
