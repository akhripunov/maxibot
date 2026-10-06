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


def assert_task_scoped_no_migration(target_migration: str, evidence: dict[str, str]) -> None:
    """TASK_SCOPED_AUTO is limited to releases with no database migration."""
    if not target_migration or evidence.get("PROD_DB_HEAD") != target_migration:
        raise AuthorizationError("TASK_SCOPED_AUTO cannot apply or reconcile database migrations")


def authorization_state_ref(project: str, authorization_id: str, state: str) -> str:
    project = project.lower()
    if project not in {"maxibot", "gpoint"} or not AUTH_ID_RE.fullmatch(authorization_id):
        raise AuthorizationError("Invalid project-scoped authorization ID")
    if state not in {"revoked", "consumed"}:
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
    if not isinstance(record, dict) or record.get("schema_version") != 1:
        raise AuthorizationError("Unsupported task authorization record")
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
            raise AuthorizationError(f"Candidate touches owner-only path: {path}")
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
            ["git", "rev-list", "--first-parent", "--reverse", "--ancestry-path", f"{baseline_sha}..{candidate_sha}"],
            cwd=repo_root, text=True, timeout=20).splitlines()
        all_changes: list[tuple[int, str, str]] = []
        for index, commit in enumerate(commits):
            output = subprocess.check_output(
                ["git", "diff-tree", "--root", "--first-parent", "--no-commit-id", "--name-status", "-r", commit],
                cwd=repo_root, text=True, timeout=20)
            for line in output.splitlines():
                status, _, changed = line.partition("\t")
                if status and changed:
                    all_changes.append((index, status, changed))
        record_changes = [item for item in all_changes if item[2] == path]
        scope_changes = [item for item in all_changes if item[2] != path]
        if (len(record_changes) != 1 or record_changes[0][1] != "A"
                or not scope_changes or record_changes[0][0] >= min(item[0] for item in scope_changes)):
            raise AuthorizationError("Durable authorization record must be added before implementation and remain unchanged")
        changed_paths = subprocess.check_output(
            ["git", "diff", "--name-only", baseline_sha, candidate_sha],
            cwd=repo_root, text=True, timeout=20).splitlines()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise AuthorizationError("Candidate diff from production baseline could not be proven") from error
    paths = sorted(set(changed_paths) - {path})
    if not paths:
        raise AuthorizationError("Candidate has no implementation changes beyond the authorization record")
    return paths
