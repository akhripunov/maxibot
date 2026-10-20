from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_release_workflow  # noqa: E402


def test_github_deployment_creation_requires_exact_prepare_context(monkeypatch):
    observed = {}

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
    monkeypatch.setattr(run_release_workflow, "update_github_deployment", lambda *_args: None)

    deployment_id = run_release_workflow.create_github_deployment(
        "a" * 40, "0098_durable_autopilot_execution_claim_v1", "production",
        "test-token", "777", "sha256:" + "b" * 64,
    )

    assert deployment_id == 1234
    assert observed["required_contexts"] == ["prepare"]


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
