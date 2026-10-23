from __future__ import annotations

import io
import json
import ssl
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_release_workflow  # noqa: E402


class JsonResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self.payload if size < 0 else self.payload[:size]


def setup_task_scoped_wrapper(monkeypatch, tmp_path):
    sha = "a" * 40
    migration = "0098_durable_autopilot_execution_claim_v1"
    manifest = tmp_path / "manifest.json"
    result_path = tmp_path / "result.json"
    rehearsal_path = tmp_path / "prepare-result.json"
    image = {"repository": "ghcr.io/akhripunov/maxibot-vps-api", "tag": f"prepare-{sha}-123-1",
             "digest": "sha256:" + "2" * 64, "config_digest": "sha256:" + "1" * 64,
             "platform": "linux/amd64", "labels": {
                 "org.opencontainers.image.revision": sha, "org.opencontainers.image.source-sha": sha,
                 "org.maxibot.release.sha": sha}}
    manifest.write_text(json.dumps({"schema": "MAXIBOT_PREPARED_IMAGE_V2", "candidate_sha": sha,
                                    "prepare_run_id": "123", "prepare_run_attempt": "1", "image": image}))
    rehearsal_path.write_text(json.dumps({"schema": "MAXIBOT_PREPARE_RECEIPT_V2", "candidate_sha": sha,
        "github_run_id": "123", "github_run_attempt": "1", "status": "READY", "tests": "success",
        "frozen_path_guard": "success", "linux_amd64_image": "success", "ghcr_image_publish": "success",
        "image": image, "private_docker_rehearsal": "PASS", "private_docker_version": "29.1.3",
        "private_store": "containerd", "private_snapshotter": "overlayfs", "private_network_isolated": "YES",
        "rehearsal_candidate_sha": sha, "rehearsal_prepare_run_id": "123",
        "rehearsal_archive_sha256": "a" * 64}))
    for key, value in {"CANDIDATE_SHA": sha, "TARGET_MIGRATION": migration,
        "RELEASE_MODE": "task-scoped-auto", "APPROVE_PRODUCTION": "false",
        "TASK_AUTHORIZATION_ID": "maxibot-task-2026-10-06-001", "RELEASE_RESULT": str(result_path),
        "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "PREPARE_RUN_ID": "123",
        "PREPARE_RUN_ATTEMPT": "1", "IMAGE_MANIFEST": str(manifest),
        "RELEASE_IMAGE_DIGEST": image["digest"], "RELEASE_IMAGE_REPOSITORY": image["repository"],
        "RELEASE_IMAGE_ID": image["config_digest"], "IMAGE_RESULT": str(rehearsal_path),
        "GITHUB_TOKEN": "test-token"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(run_release_workflow.subprocess, "check_output",
        lambda command, **kwargs: sha if command[:2] == ["git", "rev-parse"] else "")
    return sha, migration, result_path


def test_github_deployment_creation_does_not_require_commit_status_after_prepare_proof(monkeypatch):
    observed = {}
    status_calls = []
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")

    class Response:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"id":1234}'

    def create_deployment(request, timeout):
        assert timeout == 30
        observed.update(json.loads(request.data))
        return Response()

    monkeypatch.setattr(run_release_workflow.urllib.request, "urlopen", create_deployment)
    monkeypatch.setattr(run_release_workflow, "update_github_deployment", lambda *args: status_calls.append(args))

    deployment_id = run_release_workflow.create_github_deployment(
        "a" * 40, "0098_durable_autopilot_execution_claim_v1", "production",
        "test-token", "777", "sha256:" + "b" * 64,
    )

    assert deployment_id == 1234
    assert status_calls == []
    assert observed == {
        "ref": "a" * 40,
        "task": "maxibot-production-deploy",
        "environment": "maxibot-production",
        "auto_merge": False,
        "required_contexts": [],
        "description": "MAXIBOT production aaaaaaaaaaaa",
        "payload": {
            "candidate_sha": "a" * 40,
            "target_migration": "0098_durable_autopilot_execution_claim_v1",
            "mode": "production",
            "workflow_run_id": "777",
            "workflow_run_attempt": "2",
            "prepare_receipt_digest": "sha256:" + "b" * 64,
        },
    }


def test_github_deployment_creation_reports_only_http_status(monkeypatch):
    secret_response_body = b"private deployment response detail"

    def reject_deployment(_request, timeout):
        assert timeout == 30
        raise urllib.error.HTTPError(
            "https://api.github.com/repos/akhripunov/maxibot/deployments",
            403,
            "Forbidden",
            {},
            io.BytesIO(secret_response_body),
        )

    monkeypatch.setattr(run_release_workflow.urllib.request, "urlopen", reject_deployment)

    with pytest.raises(ValueError, match=r"GitHub Deployment creation failed \(HTTP 403\)") as exc:
        run_release_workflow.create_github_deployment(
            "a" * 40, "0098_durable_autopilot_execution_claim_v1", "production",
            "test-token", "777", "sha256:" + "b" * 64,
        )

    assert "private deployment response detail" not in str(exc.value)


def test_github_deployment_creation_rejects_boolean_id(monkeypatch):
    class Response:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"id":true}'

    monkeypatch.setattr(run_release_workflow.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    with pytest.raises(ValueError, match="no valid ID"):
        run_release_workflow.create_github_deployment(
            "a" * 40, "0098_durable_autopilot_execution_claim_v1", "production",
            "test-token", "777", "sha256:" + "b" * 64,
        )


def test_wrapper_http_409_is_pre_apply_failure(monkeypatch, tmp_path):
    sha, migration, result_path = setup_task_scoped_wrapper(monkeypatch, tmp_path)
    commands = []

    def execute(command, _env):
        commands.append(command)
        if command[-1] == "--dry-run":
            return 0, f"SAFE_TO_RELEASE=YES\nDRY_RUN_RESULT=PASS\nPROD_DB_HEAD={migration}\n"
        pytest.fail("HTTP 409 must stop before production Apply")

    monkeypatch.setattr(run_release_workflow, "run", execute)
    monkeypatch.setattr(run_release_workflow, "verify_task_authorization", lambda *_args, **_kwargs: {
        "record_sha256": "b" * 64, "source_reference": "linear:task#owner",
        "changed_paths": "scripts/run_release_workflow.py", "_record": {}, "_changed_paths_list": []})
    monkeypatch.setattr(run_release_workflow, "assert_task_scoped_no_migration", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(run_release_workflow, "create_github_deployment", lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(ValueError("GitHub Deployment creation failed (HTTP 409); production apply was not started"))))

    assert run_release_workflow.main() == 1

    result = json.loads(result_path.read_text())
    assert result["candidate_sha"] == sha
    assert result["status"] == "FAILED_PRE_SWITCH"
    assert result["preflight"] == "PASS"
    assert result["production_changed"] is False
    assert "HTTP 409" in result["error"]
    assert commands == [["bash", "scripts/release_prod.sh", sha, "--dry-run"]]


@pytest.mark.parametrize("failure_stage", ["preflight", "authorization"])
def test_wrapper_gate_failure_never_creates_deployment_or_applies(monkeypatch, tmp_path, failure_stage):
    sha, migration, result_path = setup_task_scoped_wrapper(monkeypatch, tmp_path)
    commands = []

    def execute(command, _env):
        commands.append(command)
        if failure_stage == "preflight":
            return 1, "SAFE_TO_RELEASE=NO\nDRY_RUN_RESULT=FAILED\n"
        return 0, f"SAFE_TO_RELEASE=YES\nDRY_RUN_RESULT=PASS\nPROD_DB_HEAD={migration}\n"

    monkeypatch.setattr(run_release_workflow, "run", execute)
    if failure_stage == "authorization":
        monkeypatch.setattr(run_release_workflow, "verify_task_authorization",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("authorization revoked")))
    monkeypatch.setattr(run_release_workflow, "create_github_deployment",
        lambda *_args, **_kwargs: pytest.fail("failed gate must not create a Deployment"))

    assert run_release_workflow.main() == 1

    result = json.loads(result_path.read_text())
    assert result["production_changed"] is False
    assert result["status"] == ("FAILED" if failure_stage == "preflight" else "FAILED_PRE_SWITCH")
    assert commands == [["bash", "scripts/release_prod.sh", sha, "--dry-run"]]


@pytest.mark.parametrize("status_fails", [False, True])
def test_wrapper_registers_deployment_after_preflight_and_authorization_before_apply(monkeypatch, tmp_path, status_fails):
    sha, migration, result_path = setup_task_scoped_wrapper(monkeypatch, tmp_path)
    events = []

    def verify(*_args, **_kwargs):
        events.append("authorization")
        return {"record_sha256": "b" * 64, "source_reference": "linear:task#owner",
                "changed_paths": "scripts/run_release_workflow.py", "_record": {}, "_changed_paths_list": []}

    def execute(command, _env):
        events.append("preflight" if command[-1] == "--dry-run" else "apply")
        output = f"SAFE_TO_RELEASE=YES\nDRY_RUN_RESULT=PASS\nPROD_DB_HEAD={migration}\n"
        return 0, output if command[-1] == "--dry-run" else "MIGRATION_ALLOWED=YES\nRELEASE_STATUS=PASS\n"

    monkeypatch.setattr(run_release_workflow, "run", execute)
    monkeypatch.setattr(run_release_workflow, "verify_task_authorization", verify)
    monkeypatch.setattr(run_release_workflow, "assert_task_scoped_no_migration", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(run_release_workflow, "consume_task_authorization", lambda *_args: events.append("consume"))
    monkeypatch.setattr(run_release_workflow, "create_github_deployment",
                        lambda *_args, **_kwargs: events.append("deployment") or 987)
    def publish_status(*_args, **_kwargs):
        if status_fails and _args[1] == "success": raise ValueError("status write unconfirmed")
    monkeypatch.setattr(run_release_workflow, "update_github_deployment", publish_status)

    assert run_release_workflow.main() == (1 if status_fails else 0)

    result = json.loads(result_path.read_text())
    assert result["candidate_sha"] == sha
    assert result["status"] == ("RECOVERY_REQUIRED" if status_fails else "COMPLETE")
    assert result["production_changed"] is True
    assert events == ["preflight", "authorization", "deployment", "apply", "consume"]


def test_ambiguous_status_post_confirms_same_deployment_without_duplicate_post(monkeypatch):
    calls = []
    description = "MAXIBOT production apply started"

    def urlopen(request, timeout):
        calls.append((request.get_method(), request.full_url))
        assert timeout <= 30
        if request.get_method() == "POST":
            raise urllib.error.URLError(TimeoutError("response lost"))
        assert request.full_url.endswith("/deployments/1234/statuses?per_page=100")
        return JsonResponse([{"state": "in_progress", "description": description}])

    monkeypatch.setattr(run_release_workflow.urllib.request, "urlopen", urlopen)
    run_release_workflow.update_github_deployment(1234, "in_progress", description, "test-token")

    assert [method for method, _ in calls].count("POST") == 1
    assert [method for method, _ in calls].count("GET") == 1


def test_unconfirmed_status_readback_retries_only_same_deployment_status(monkeypatch):
    calls = []
    description = "MAXIBOT production apply started"
    post_body = []

    def urlopen(request, timeout):
        calls.append((request.get_method(), request.full_url))
        if request.get_method() == "POST":
            post_body.append(json.loads(request.data))
            if len(post_body) == 1:
                raise urllib.error.URLError(TimeoutError("response lost"))
            return JsonResponse({"id": 5678}, status=201)
        return JsonResponse([{"state": "failure", "description": "previous status"}])

    monkeypatch.setattr(run_release_workflow.urllib.request, "urlopen", urlopen)
    run_release_workflow.update_github_deployment(1234, "in_progress", description, "test-token")

    assert [method for method, _ in calls].count("POST") == 2
    assert [method for method, _ in calls].count("GET") == 1
    assert post_body[0] == post_body[1] == {
        "state": "in_progress", "description": description,
        "environment_url": "https://maxibot.ru",
    }
    assert all("/deployments/1234/statuses" in url for _, url in calls)


@pytest.mark.parametrize("status", [401, 403])
def test_status_auth_failures_are_fatal_without_retry_or_body_leak(monkeypatch, status):
    calls = []
    private_body = b"private authorization detail"

    def reject(_request, timeout):
        calls.append(timeout)
        raise urllib.error.HTTPError("https://api.github.com/private", status,
            "Forbidden", {}, io.BytesIO(private_body))

    monkeypatch.setattr(run_release_workflow.urllib.request, "urlopen", reject)
    with pytest.raises(ValueError, match=rf"Deployment status write failed \(HTTP {status}\)") as exc:
        run_release_workflow.update_github_deployment(1234, "in_progress", "starting", "test-token")

    assert len(calls) == 1
    assert "private authorization detail" not in str(exc.value)


def test_status_readback_auth_failure_is_fatal_without_retry_or_body_leak(monkeypatch):
    calls = []
    private_body = b"private authorization detail"

    def urlopen(request, timeout):
        calls.append(request.get_method())
        if request.get_method() == "POST":
            raise urllib.error.URLError(TimeoutError("response lost"))
        raise urllib.error.HTTPError("https://api.github.com/private", 403,
            "Forbidden", {}, io.BytesIO(private_body))

    monkeypatch.setattr(run_release_workflow.urllib.request, "urlopen", urlopen)
    with pytest.raises(ValueError, match=r"Deployment status readback failed \(HTTP 403\)") as exc:
        run_release_workflow.update_github_deployment(1234, "in_progress", "starting", "test-token")

    assert calls == ["POST", "GET"]
    assert "private authorization detail" not in str(exc.value)


def test_status_readback_certificate_failure_is_fatal_without_retry(monkeypatch):
    calls = []

    def reject(request, timeout):
        calls.append(request.get_method())
        if request.get_method() == "POST":
            raise urllib.error.URLError(TimeoutError("response lost"))
        raise urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))

    monkeypatch.setattr(run_release_workflow.urllib.request, "urlopen", reject)
    with pytest.raises(ValueError, match="Deployment status readback failed"):
        run_release_workflow.update_github_deployment(1234, "in_progress", "starting", "test-token")

    assert calls == ["POST", "GET"]


def test_certificate_verification_failure_is_fatal_without_readback(monkeypatch):
    calls = []
    failure = urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))

    def reject(request, timeout):
        assert timeout <= 30
        calls.append(request.get_method())
        raise failure

    monkeypatch.setattr(run_release_workflow.urllib.request, "urlopen", reject)
    with pytest.raises(ValueError, match="Deployment status write failed"):
        run_release_workflow.update_github_deployment(1234, "in_progress", "starting", "test-token")

    assert calls == ["POST"]


def test_unconfirmed_start_status_is_bounded_preserves_id_and_stops_before_apply(monkeypatch):
    clock = [0.0]

    class TinyBudget:
        seconds = 3.0
        spent = 0.0
        state_path = None

        def __init__(self):
            self.deadline = clock[0] + self.seconds

        def remaining_seconds(self):
            return max(0.0, self.deadline - clock[0])

        def persist_interval(self, *_args):
            return None

    monkeypatch.setattr(run_release_workflow.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(run_release_workflow.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    monkeypatch.setattr(run_release_workflow, "RetryBudget", TinyBudget)
    monkeypatch.setattr(run_release_workflow, "create_github_deployment", lambda *_args: 1234)
    calls = []

    def unconfirmed(request, timeout):
        calls.append(request.get_method())
        if request.get_method() == "POST":
            raise urllib.error.URLError(TimeoutError("response lost"))
        return JsonResponse([{"state": "failure", "description": "previous attempt"}])

    monkeypatch.setattr(run_release_workflow.urllib.request, "urlopen", unconfirmed)
    result = {}
    apply_calls = []

    with pytest.raises(ValueError, match="shared network budget"):
        deployment_id = run_release_workflow.start_github_deployment(
            "a" * 40, "0098_durable_autopilot_execution_claim_v1", "production",
            "test-token", "777", "sha256:" + "b" * 64, result,
        )
        apply_calls.append(deployment_id)

    assert result["github_deployment_id"] == 1234
    assert result["github_deployment_state"] == "PUBLICATION_FAILED"
    assert calls == ["POST", "GET", "POST", "GET"]
    assert apply_calls == []


def test_ambiguous_deployment_create_reads_back_exact_run_without_reposting(monkeypatch):
    calls = []
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "3")
    receipt_digest = "sha256:" + "b" * 64

    def urlopen(request, timeout):
        calls.append((request.get_method(), request.full_url))
        assert timeout == 30
        if request.get_method() == "POST":
            raise urllib.error.URLError(TimeoutError("response lost"))
        assert request.get_method() == "GET"
        assert request.full_url == (
            "https://api.github.com/repos/akhripunov/maxibot/deployments"
            "?sha=" + "a" * 40 + "&environment=maxibot-production&per_page=100"
        )
        return JsonResponse([{
            "id": 9876,
            "ref": "a" * 40,
            "task": "maxibot-production-deploy",
            "environment": "maxibot-production",
            "payload": {
                "candidate_sha": "a" * 40,
                "target_migration": "0098_durable_autopilot_execution_claim_v1",
                "mode": "task-scoped-auto",
                "workflow_run_id": "777",
                "workflow_run_attempt": "3",
                "prepare_receipt_digest": receipt_digest,
            },
        }])

    monkeypatch.setattr(run_release_workflow.urllib.request, "urlopen", urlopen)
    deployment_id = run_release_workflow.create_github_deployment(
        "a" * 40, "0098_durable_autopilot_execution_claim_v1", "task-scoped-auto",
        "test-token", "777", receipt_digest,
    )

    assert deployment_id == 9876
    assert [method for method, _url in calls] == ["POST", "GET"]


def test_ambiguous_deployment_create_blocks_when_exact_readback_is_not_unique(monkeypatch):
    calls = []
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "3")

    def urlopen(request, timeout):
        calls.append(request.get_method())
        assert timeout == 30
        if request.get_method() == "POST":
            raise urllib.error.URLError(TimeoutError("response lost"))
        return JsonResponse([{
            "id": 9876,
            "ref": "a" * 40,
            "task": "maxibot-production-deploy",
            "environment": "maxibot-production",
            "payload": {
                "candidate_sha": "a" * 40,
                "target_migration": "0098_durable_autopilot_execution_claim_v1",
                "mode": "task-scoped-auto",
                "workflow_run_id": "different-run",
                "workflow_run_attempt": "3",
                "prepare_receipt_digest": "sha256:" + "b" * 64,
            },
        }])

    monkeypatch.setattr(run_release_workflow.urllib.request, "urlopen", urlopen)
    with pytest.raises(ValueError, match="readback cannot confirm one exact Deployment"):
        run_release_workflow.create_github_deployment(
            "a" * 40, "0098_durable_autopilot_execution_claim_v1", "task-scoped-auto",
            "test-token", "777", "sha256:" + "b" * 64,
        )
    assert calls == ["POST", "GET"]


def test_deployment_http_409_is_sanitized_and_not_retried(monkeypatch):
    calls = []

    def reject(request, timeout):
        calls.append(request.get_method())
        assert timeout == 30
        raise urllib.error.HTTPError(
            request.full_url, 409, "Conflict", {},
            io.BytesIO(b"private status-check or deployment metadata"),
        )

    monkeypatch.setattr(run_release_workflow.urllib.request, "urlopen", reject)
    with pytest.raises(ValueError, match=r"GitHub Deployment creation failed \(HTTP 409\)") as exc:
        run_release_workflow.create_github_deployment(
            "a" * 40, "0098_durable_autopilot_execution_claim_v1", "task-scoped-auto",
            "test-token", "777", "sha256:" + "b" * 64,
        )
    assert "private status-check" not in str(exc.value)
    assert calls == ["POST"]


def test_metadata_recovery_retries_transient_deployment_read(monkeypatch):
    calls = []
    sha = "a" * 40
    def read(request, **kwargs):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.URLError(ssl.SSLEOFError("unexpected EOF"))
        if "/statuses" in request.full_url:
            return JsonResponse([{"state": "failure"}])
        return JsonResponse([{"id": 727, "ref": sha, "environment": "maxibot-production",
            "payload": {"workflow_run_id": "777", "workflow_run_attempt": "2"}}])
    monkeypatch.setattr(run_release_workflow.urllib.request, "urlopen", read)
    monkeypatch.setattr(run_release_workflow.time, "sleep", lambda _: None)
    assert run_release_workflow.find_recoverable_deployment(sha, "777", "2", "test-token") == 727
    assert len(calls) == 3


@pytest.mark.parametrize("failure,expected", [
    ("TASK_SCOPED_AUTO consumption write failed (HTTP 403)", "HTTP_403"),
    ("private secret response payload", "CONSUMPTION_UNCONFIRMED"),
])
def test_finalization_reports_safe_category_without_losing_completed_runtime(monkeypatch, tmp_path, capsys, failure, expected):
    sha, migration, result_path = setup_task_scoped_wrapper(monkeypatch, tmp_path)
    commands = []
    def execute(command, _env):
        commands.append(command)
        if command[-1] == "--dry-run":
            return 0, f"SAFE_TO_RELEASE=YES\nDRY_RUN_RESULT=PASS\nPROD_DB_HEAD={migration}\n"
        return 0, "MIGRATION_ALLOWED=YES\nRELEASE_STATUS=PASS\n"
    monkeypatch.setattr(run_release_workflow, "run", execute)
    monkeypatch.setattr(run_release_workflow, "verify_task_authorization", lambda *_args, **_kwargs: {
        "record_sha256": "b" * 64, "source_reference": "owner-task", "changed_paths": "scripts/run_release_workflow.py",
        "_record": {}, "_changed_paths_list": []})
    monkeypatch.setattr(run_release_workflow, "assert_task_scoped_no_migration", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(run_release_workflow, "create_github_deployment", lambda *_args: 987)
    monkeypatch.setattr(run_release_workflow, "update_github_deployment", lambda *_args: None)
    def fail(*_args): raise ValueError(failure)
    monkeypatch.setattr(run_release_workflow, "consume_task_authorization", fail)
    assert run_release_workflow.main() == 1
    result = json.loads(result_path.read_text())
    assert result["status"] == "RECOVERY_REQUIRED"
    assert result["production_changed"] is True
    assert result["task_authorization_error_code"] == expected
    output = capsys.readouterr().out
    assert "RELEASE_FINALIZATION_ERROR=" + expected in output
    assert "private secret response payload" not in output + result_path.read_text()
    assert sum(command[-1] == "--apply" for command in commands) == 1


@pytest.mark.parametrize("status_fails", [False, True])
def test_metadata_recovery_finalizes_same_deployment_once_without_apply(monkeypatch, tmp_path, status_fails):
    sha, migration, result_path = setup_task_scoped_wrapper(monkeypatch, tmp_path)
    monkeypatch.setenv("RELEASE_MODE", "publication-recovery")
    monkeypatch.setenv("TASK_AUTHORIZATION_ID", "")
    monkeypatch.setenv("RECONCILES_DEPLOY_RUN_ID", "777")
    monkeypatch.setenv("RECONCILES_DEPLOY_RUN_ATTEMPT", "2")
    commands, writes = [], []
    def execute(command, _env):
        commands.append(command)
        assert command[-1] == "--dry-run"
        return 0, (f"SAFE_TO_RELEASE=YES\nDRY_RUN_RESULT=PASS\nPROD_DB_HEAD={migration}\n"
            f"PROD_READY=ready\nPROD_API_IMAGE=maxibot-vps-api:{sha}\nPROD_WORKER_IMAGE=maxibot-vps-api:{sha}\n"
            "PROD_WRITE_FLAGS=ADS_AUTOPILOT_ENABLED=false,ADS_WRITE_ACTIONS_ENABLED=false\n"
            "PROD_WORKER_HEALTH=PASS\nROLLBACK_CONFIG=/verified/compose.yml\nROLLBACK_READY=PASS\n")
    def status(*args):
        writes.append(args)
        if status_fails or len(writes) > 1: raise ValueError("status write failed")
    monkeypatch.setattr(run_release_workflow, "run", execute)
    monkeypatch.setattr(run_release_workflow, "find_recoverable_deployment", lambda *_args: 987)
    monkeypatch.setattr(run_release_workflow, "update_github_deployment", status)
    assert run_release_workflow.main() == (1 if status_fails else 0)
    result = json.loads(result_path.read_text())
    assert result["status"] == ("RECOVERY_REQUIRED" if status_fails else "COMPLETE")
    assert result["production_changed"] is False
    assert result["github_deployment_id"] == 987
    assert len(writes) == 1 and writes[0][0:2] == (987, "success")
    assert commands == [["bash", "scripts/release_prod.sh", sha, "--dry-run"]]
