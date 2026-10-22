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


def test_github_deployment_creation_requires_exact_prepare_context(monkeypatch):
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
        "required_contexts": ["prepare"],
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
