from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "maxibot_release_diagnostics.py"
SHA = "d38e3613f50a5659c463572064272e4438763ea4"
RUN_ID = "40077453537"
ATTEMPT = "2"
EVIDENCE_DIGEST = "sha256:c1cb813bc2c0ace2931e226935a2b830119c7c6ac5c5a48ce3a2dfbc1584a121"
PREPARE_DIGEST = "sha256:7e3402cbd50485005a3cd01f74bb13d53f21abd47c8943c2849e9e561f5630ae"
AUTH_ID = "maxibot-me50-v5-release-infra-20261024"


def load_diagnostics():
    spec = importlib.util.spec_from_file_location("maxibot_release_diagnostics_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def run_record():
    return {"id": int(RUN_ID), "run_attempt": int(ATTEMPT), "head_sha": SHA,
        "head_branch": "main", "event": "workflow_dispatch",
        "path": ".github/workflows/maxibot-deploy.yml"}


def evidence(**changes):
    value = {"schema": "MAXIBOT_RELEASE_WORKFLOW_RESULT_V1", "candidate_sha": SHA,
        "github_run_id": RUN_ID, "github_run_attempt": ATTEMPT,
        "mode": "task-scoped-auto", "target_migration": "0098_durable_autopilot_execution_claim_v1",
        "error": "NETWORK_UNAVAILABLE stage=release_provenance",
        "release_state": "FAILED_PRE_SWITCH", "preflight": "PASS",
        "production_changed": False, "github_deployment_id": None,
        "github_deployment_state": None, "authorization_consumed": False,
        "preflight_evidence": {"CURRENT_PRODUCTION_SHA": "ae6f5ea623e12fde4d0e2271023f7822a3f9822f",
            "CURRENT_PRODUCTION_DB_HEAD": "0098_durable_autopilot_execution_claim_v1",
            "SAFE_TO_RELEASE": "YES", "DRY_RUN_RESULT": "PASS"}}
    value.update(changes)
    return value


def no_deployment_api(diag, sha=SHA):
    paths = []
    def get(path, _token, **_kwargs):
        paths.append(path)
        if path == f"actions/runs/{RUN_ID}/attempts/{ATTEMPT}":
            return run_record()
        if path.startswith("deployments?"):
            return []
        if path == f"git/ref/tags/maxibot-task-auth/{AUTH_ID}/claimed":
            return {"object": {"sha": sha, "type": "commit"}}
        if path.endswith("/consumed") or path.endswith("/revoked"):
            raise diag.ValidationError("GitHub provenance request failed (HTTP 404)")
        raise AssertionError(f"unexpected read path {path}")
    return get, paths


def test_original_attempt_is_verified_without_using_current_workflow_identity(monkeypatch, tmp_path):
    assert SCRIPT.is_file(), "read-only historical release diagnostics entrypoint is missing"
    diag = load_diagnostics()
    get, paths = no_deployment_api(diag)
    monkeypatch.setattr(diag, "github_get", get)
    monkeypatch.setenv("GITHUB_RUN_ID", "99999999999")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "3")
    monkeypatch.setattr(diag, "read_deploy_evidence", lambda *args: evidence())

    result = diag.diagnose(sha=SHA, run_id=RUN_ID, attempt=ATTEMPT,
        evidence_digest=EVIDENCE_DIGEST, prepare_digest=PREPARE_DIGEST,
        authorization_id=AUTH_ID, token="read-token", actor="github-actions[bot]",
        destination=tmp_path, api_get=get)

    assert result["candidate_sha"] == SHA
    assert result["github_run_id"] == RUN_ID
    assert result["github_run_attempt"] == ATTEMPT
    assert result["error"] == "NETWORK_UNAVAILABLE stage=release_provenance"
    assert result["github_deployment_api_match"] == "NOT_CREATED"
    assert result["authorization_claim"]["matches_candidate"] is True
    assert "actions/runs/40077453537/attempts/2" in paths
    assert all("99999999999" not in path for path in paths)


def test_matching_original_deployment_and_latest_status_are_read_back(monkeypatch, tmp_path):
    assert SCRIPT.is_file(), "read-only historical release diagnostics entrypoint is missing"
    diag = load_diagnostics()
    deployment = {"id": 9182, "sha": SHA, "ref": SHA,
        "task": "maxibot-production-deploy", "environment": "maxibot-production",
        "payload": {"candidate_sha": SHA, "target_migration": "0098_durable_autopilot_execution_claim_v1",
            "mode": "task-scoped-auto", "workflow_run_id": RUN_ID,
            "workflow_run_attempt": ATTEMPT, "prepare_receipt_digest": PREPARE_DIGEST}}
    paths = []
    def get(path, _token, **_kwargs):
        paths.append(path)
        if path == f"actions/runs/{RUN_ID}/attempts/{ATTEMPT}": return run_record()
        if path.startswith("deployments?"): return [deployment]
        if path == "deployments/9182/statuses?per_page=100": return [{"state": "failure"}]
        if path == f"git/ref/tags/maxibot-task-auth/{AUTH_ID}/claimed":
            return {"object": {"sha": SHA, "type": "commit"}}
        if path.endswith("/consumed") or path.endswith("/revoked"):
            raise diag.ValidationError("GitHub provenance request failed (HTTP 404)")
        raise AssertionError(path)
    monkeypatch.setattr(diag, "github_get", get)
    monkeypatch.setattr(diag, "read_deploy_evidence", lambda *args: evidence(
        github_deployment_id=9182, github_deployment_state="failure"))

    result = diag.diagnose(sha=SHA, run_id=RUN_ID, attempt=ATTEMPT,
        evidence_digest=EVIDENCE_DIGEST, prepare_digest=PREPARE_DIGEST,
        authorization_id=AUTH_ID, token="read-token", actor="github-actions[bot]",
        destination=tmp_path, api_get=get)

    assert result["github_deployment_id"] == 9182
    assert result["github_deployment_api_state"] == "failure"
    assert "deployments/9182/statuses?per_page=100" in paths


def test_ambiguous_deployment_matches_block_diagnostics(monkeypatch, tmp_path):
    assert SCRIPT.is_file(), "read-only historical release diagnostics entrypoint is missing"
    diag = load_diagnostics()
    one = {"id": 10, "sha": SHA, "ref": SHA, "task": "maxibot-production-deploy",
        "environment": "maxibot-production", "payload": {"candidate_sha": SHA,
        "target_migration": "0098_durable_autopilot_execution_claim_v1", "mode": "task-scoped-auto",
        "workflow_run_id": RUN_ID, "workflow_run_attempt": ATTEMPT,
        "prepare_receipt_digest": PREPARE_DIGEST}}
    get, _ = no_deployment_api(diag)
    def ambiguous(path, token, **kwargs):
        if path.startswith("deployments?"): return [one, {**one, "id": 11}]
        return get(path, token, **kwargs)
    monkeypatch.setattr(diag, "read_deploy_evidence", lambda *args: evidence())
    with pytest.raises(diag.DiagnosticError, match="Multiple GitHub Deployments"):
        diag.diagnose(sha=SHA, run_id=RUN_ID, attempt=ATTEMPT,
            evidence_digest=EVIDENCE_DIGEST, prepare_digest=PREPARE_DIGEST,
            authorization_id=AUTH_ID, token="read-token", actor="github-actions[bot]",
            destination=tmp_path, api_get=ambiguous)
