#!/usr/bin/env python3
"""Fail-closed task-scoped release authorization over the full baseline diff."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
OWNER_ONLY_PREFIXES = (
    ".github/", "scripts/release", "scripts/run_release", "scripts/validate_release",
    "scripts/automated", "docs/RELEASE", "docs/release", "alembic/", "migrations/",
    "db/", "security/", "auth/", "payments/", "billing/", "app/ads/",
    "app/traffic/", "app/integrations/", "miniapp/traffic", "deploy/",
)
OWNER_ONLY_NAMES = {
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml", "pyproject.toml",
    "uv.lock", "package-lock.json", "pnpm-lock.yaml", "requirements.txt",
}


@dataclass(frozen=True)
class ScopeDecision:
    status: str
    reason: str
    changed_paths: tuple[str, ...]


def evaluate_scope(*, baseline_sha: str, candidate_sha: str, mode: str,
                   authorization_ref: str, task_scope: str,
                   authorized_prefixes: Iterable[str], changed_paths: Iterable[str],
                   risk_attestation: str) -> ScopeDecision:
    """Only a recorded, exact task scope plus bounded low-risk path set can auto-pass."""
    paths = tuple(sorted(set(changed_paths)))
    if not SHA_RE.fullmatch(baseline_sha) or not SHA_RE.fullmatch(candidate_sha) or baseline_sha == candidate_sha:
        return ScopeDecision("OWNER_DECISION_REQUIRED", "invalid_or_empty_baseline_diff", paths)
    if mode != "TASK_SCOPED_AUTO":
        return ScopeDecision("OWNER_DECISION_REQUIRED", "exact_sha_or_unknown_authorization_mode", paths)
    if not authorization_ref.strip() or not task_scope.strip() or not paths:
        return ScopeDecision("OWNER_DECISION_REQUIRED", "authorization_or_scope_record_missing", paths)
    if risk_attestation != "LOW":
        return ScopeDecision("OWNER_DECISION_REQUIRED", "risk_not_explicitly_low", paths)
    prefixes = tuple(p.strip().rstrip("/") + "/" for p in authorized_prefixes if p.strip())
    if not prefixes:
        return ScopeDecision("OWNER_DECISION_REQUIRED", "authorized_paths_missing", paths)
    for path in paths:
        if path.startswith(OWNER_ONLY_PREFIXES) or path.rsplit("/", 1)[-1] in OWNER_ONLY_NAMES:
            return ScopeDecision("OWNER_DECISION_REQUIRED", "owner_only_path:" + path, paths)
        if not any(path.startswith(prefix) for prefix in prefixes):
            return ScopeDecision("OWNER_DECISION_REQUIRED", "scope_expansion:" + path, paths)
    return ScopeDecision("AUTO_RELEASE_ALLOWED", "exact_task_scope_low_risk", paths)
