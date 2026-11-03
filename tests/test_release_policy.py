from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from release_policy import MANDATORY_RELEASE_GATES, evaluate_release  # noqa: E402


SHA = "a" * 40


def passing_evidence():
    evidence = {gate: "PASS" for gate in MANDATORY_RELEASE_GATES}
    evidence["FULL_SUITE"] = "0 failures"
    return evidence


@pytest.mark.parametrize("qa_status", ["NOT_RUN", "TIMEOUT", "NO_GO", "GO"])
def test_qa_status_never_changes_prospective_release_readiness(qa_status):
    result = evaluate_release(
        passing_evidence(), expected_sha=SHA, candidate_sha=SHA, qa_status=qa_status
    )
    assert result.candidate_ready is True
    assert result.release_ready is True
    assert result.deploy_allowed is True
    assert result.blocked_gates == ()
    assert result.qa_status == qa_status


@pytest.mark.parametrize(
    "gate,value",
    [
        ("FULL_SUITE", "1 failure"),
        ("PLATFORM_PREFLIGHT", "FAIL"),
        ("MIGRATION_REHEARSAL", "FAIL"),
        ("PRODUCTION_REHEARSAL", "FAIL"),
        ("RELEASE_DRY_RUN", "FAIL"),
        ("API_WORKER_IMAGE_PARITY", "FAIL"),
        ("TARGETED_TESTS", "FAIL"),
        ("LINUX_AMD64_CANDIDATE", "FAIL"),
    ],
)
def test_deterministic_release_gate_failure_blocks_even_with_qa_no_go(gate, value):
    evidence = passing_evidence()
    evidence[gate] = value
    result = evaluate_release(evidence, expected_sha=SHA, candidate_sha=SHA, qa_status="NO_GO")
    assert result.candidate_ready is False
    assert result.release_ready is False
    assert result.deploy_allowed is False
    assert gate in result.blocked_gates


def test_wrong_exact_sha_blocks_even_with_all_other_gates_pass():
    result = evaluate_release(
        passing_evidence(), expected_sha=SHA, candidate_sha="b" * 40, qa_status="NOT_RUN"
    )
    assert result.deploy_allowed is False
    assert "EXACT_SHA" in result.blocked_gates


def test_frozen_path_and_migration_gates_can_be_not_applicable():
    evidence = passing_evidence()
    evidence["FROZEN_PATH_GUARD"] = "NOT_APPLICABLE"
    evidence["MIGRATION_REHEARSAL"] = "NOT_APPLICABLE"
    result = evaluate_release(evidence, expected_sha=SHA, candidate_sha=SHA, qa_status="TIMEOUT")
    assert result.deploy_allowed is True


def test_post_deploy_readback_is_reported_separately_from_predeploy_authority():
    evidence = passing_evidence()
    evidence["POST_DEPLOY_HEALTH_SHA_READBACK"] = "NOT_RUN"
    result = evaluate_release(evidence, expected_sha=SHA, candidate_sha=SHA, qa_status="NO_GO")
    assert result.deploy_allowed is True
    assert result.post_deploy_complete is False
    evidence["POST_DEPLOY_HEALTH_SHA_READBACK"] = "PASS"
    result = evaluate_release(evidence, expected_sha=SHA, candidate_sha=SHA, qa_status="NO_GO")
    assert result.post_deploy_complete is True


def test_historical_candidate_without_policy_marker_is_rejected_by_release_runner():
    current = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    historical = subprocess.check_output(["git", "rev-parse", f"{current}^"], cwd=ROOT, text=True).strip()
    marker = subprocess.run(
        ["git", "cat-file", "-e", f"{historical}:scripts/release_policy.py"], cwd=ROOT
    )
    if marker.returncode == 0:
        pytest.skip("parent commit already contains prospective policy marker")
    result = subprocess.run(
        [str(ROOT / "scripts/release_prod.sh"), historical, "--dry-run"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "candidate predates prospective release policy" in result.stderr


def test_qa_runner_keeps_deterministic_failures_and_drops_verdict_as_deploy_gate():
    source = (ROOT / "scripts/qa_check.sh").read_text()
    deploy = source[source.index('if [ "${1:-}" = "DEPLOY" ]; then'):]
    assert 'git cat-file -e "$CANDIDATE_SHA:scripts/release_policy.py"' in deploy
    assert 'if [ "${OWNER_APPROVED:-0}" != "1" ] || [ -z "${PRODUCTION_DEPLOY_CMD:-}" ]; then' in deploy
    assert '"${VERDICT}" != "GO"' not in deploy
    assert "run_tests regression tests tests/regression || { REGRESSION=\"FAIL\"; fail_gate; }" in source
