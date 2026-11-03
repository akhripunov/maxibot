#!/usr/bin/env python3
"""Deterministic release readiness policy for prospective MAXIBOT candidates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping


POLICY_VERSION = "prospective-qa-advisory-v1"
MANDATORY_RELEASE_GATES = (
    "TARGETED_TESTS",
    "FULL_SUITE",
    "FROZEN_PATH_GUARD",
    "EXACT_SHA",
    "LINUX_AMD64_CANDIDATE",
    "API_WORKER_IMAGE_PARITY",
    "PLATFORM_PREFLIGHT",
    "MIGRATION_REHEARSAL",
    "RELEASE_DRY_RUN",
    "PRODUCTION_REHEARSAL",
    "POST_DEPLOY_HEALTH_SHA_READBACK",
)
ADVISORY_QA_STATUSES = {"GO", "NO_GO", "NOT_RUN", "TIMEOUT"}


@dataclass(frozen=True)
class ReleaseDecision:
    candidate_ready: bool
    release_ready: bool
    deploy_allowed: bool
    blocked_gates: tuple[str, ...]
    qa_status: str
    post_deploy_complete: bool


def evaluate_release(
    evidence: Mapping[str, object], *, expected_sha: str, candidate_sha: str, qa_status: str
) -> ReleaseDecision:
    """Evaluate technical evidence; QA status is recorded but never gates readiness."""
    sha_ok = bool(re.fullmatch(r"[0-9a-f]{40}", expected_sha)) and candidate_sha == expected_sha
    blocked = []
    post_deploy_complete = evidence.get("POST_DEPLOY_HEALTH_SHA_READBACK") == "PASS"
    for gate in MANDATORY_RELEASE_GATES:
        if gate == "POST_DEPLOY_HEALTH_SHA_READBACK":
            continue
        if gate == "EXACT_SHA":
            passed = sha_ok
        elif gate == "FULL_SUITE":
            passed = evidence.get(gate) in ("PASS", "0 failures", 0)
        elif gate in {"FROZEN_PATH_GUARD", "MIGRATION_REHEARSAL"}:
            passed = evidence.get(gate) in {"PASS", "NOT_APPLICABLE"}
        else:
            passed = evidence.get(gate) == "PASS"
        if not passed:
            blocked.append(gate)
    status = qa_status.upper()
    if status not in ADVISORY_QA_STATUSES:
        status = "UNKNOWN"
    ready = not blocked
    return ReleaseDecision(ready, ready, ready, tuple(blocked), status, post_deploy_complete)
