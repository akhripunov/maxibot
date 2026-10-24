from __future__ import annotations

import sys
import json
import os
import re
from unittest.mock import patch
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_release_dispatch import ValidationError, github_get, validate  # noqa: E402
import validate_release_dispatch as dispatch_module  # noqa: E402
READ_PREPARE_PUBLISHER_DIGEST = dispatch_module.read_prepare_publisher_digest
from publish_release_to_linear import SyncError, append_journal_event, upsert_block, write_document  # noqa: E402
import publish_release_to_linear  # noqa: E402
import run_release_workflow  # noqa: E402
import release_orchestrator as orchestrator  # noqa: E402
from release_authorization import owner_task_commitment_sha256  # noqa: E402


SHA = "a" * 40
MIGRATION = "0098_durable_autopilot_execution_claim_v1"


class FakeGitHub:
    def __init__(self, main_sha=SHA, runs=None, deploy_runs=None):
        self.main_sha = main_sha
        self.deploy_runs = deploy_runs if deploy_runs is not None else []
        self.runs = runs if runs is not None else [
            {"id": 14, "run_number": 4, "run_attempt": 1, "head_sha": SHA,
             "status": "completed", "conclusion": "success", "event": "push",
             "head_branch": "main", "html_url": "https://github.com/akhripunov/maxibot/actions/runs/14"}
        ]
        self.paths = []
        self.release_enabled = True
        self.authorization_markers = set()

    def __call__(self, path, token):
        self.paths.append(path)
        if path == "git/ref/heads/main":
            return {"object": {"sha": self.main_sha}}
        if path.startswith("actions/workflows/maxibot-deploy.yml/"):
            return {"workflow_runs": self.deploy_runs}
        if path.startswith("actions/workflows/"):
            return {"workflow_runs": self.runs}
        if path.startswith("releases/tags/"):
            tag = path.split("/", 2)[2]
            if not self.release_enabled:
                raise ValidationError("not found")
            return {"tag_name": tag, "draft": False, "immutable": True, "assets": [{
                "id": 123, "name": "bundle.zip", "state": "uploaded", "size": 42,
                "digest": "sha256:" + "a" * 64,
            }]}
        if path.startswith("git/ref/tags/"):
            if "-task-auth/" in path:
                if path in self.authorization_markers:
                    return {"object": {"type": "commit", "sha": SHA}}
                raise ValidationError("GitHub release provenance request failed (HTTP 404)")
            return {"object": {"type": "commit", "sha": SHA}}
        if path == "actions/runs/777":
            return {"id": 777, "head_sha": SHA, "head_branch": "main",
                    "path": ".github/workflows/maxibot-deploy.yml",
                    "run_attempt": 3, "event": "workflow_dispatch", "status": "completed",
                    "conclusion": "failure", "display_title": f"MAXIBOT production {SHA} by owner"}
        raise AssertionError(path)


def values(**overrides):
    result = {
        "CANDIDATE_SHA": SHA,
        "TARGET_MIGRATION": MIGRATION,
        "RELEASE_MODE": "preflight",
        "APPROVE_PRODUCTION": "false",
        "GITHUB_TOKEN": "test-token",
    }
    result.update(overrides)
    return result


def ready_evidence(sha, run_id, attempt, _token, _api_get):
    return ({"candidate_sha": sha, "deploy_run_id": run_id,
             "deploy_run_attempt": attempt, "prepare_run_id": "14",
             "prepare_run_attempt": "1"},
            {"candidate_sha": sha, "target_migration": MIGRATION,
             "mode": "preflight", "status": "PLANNED",
             "release_state": "READY_FOR_OWNER", "preflight": "PASS",
             "production_changed": False, "prepare_run_id": "14",
             "prepare_run_attempt": "1"})


def ready_preflight_run(status="completed", conclusion="success", sha=SHA):
    return {"id": 81, "run_number": 12, "run_attempt": 1, "head_sha": sha,
            "head_branch": "main", "event": "workflow_dispatch", "path": ".github/workflows/maxibot-deploy.yml",
            "status": status, "conclusion": conclusion,
            "display_title": f"MAXIBOT preflight {sha} by owner"}


@pytest.fixture(autouse=True)
def verified_oci_receipt(monkeypatch):
    def resolve(_repository, tag, _artifact_type, _layer_type, _fetch):
        match = __import__("re").fullmatch(r"prepare-([0-9a-f]{40})-([1-9][0-9]*)-([1-9][0-9]*)", tag)
        assert match
        sha, run_id, attempt = match.groups()
        receipt = {
            "schema": "MAXIBOT_PREPARE_RECEIPT_V2", "candidate_sha": sha,
            "github_run_id": run_id, "github_run_attempt": attempt, "status": "READY",
            "tests": "success", "frozen_path_guard": "success", "linux_amd64_image": "success",
            "ghcr_image_publish": "success", "private_docker_rehearsal": "PASS",
            "image": {"repository": "ghcr.io/akhripunov/maxibot-vps-api", "tag": f"prepare-{sha}-{run_id}-{attempt}",
                      "digest": "sha256:" + "c" * 64, "config_digest": "sha256:" + "d" * 64,
                      "platform": "linux/amd64", "labels": {
                          "org.opencontainers.image.revision": sha,
                          "org.opencontainers.image.source-sha": sha,
                          "org.maxibot.release.sha": sha}},
        }
        return "sha256:" + "e" * 64, json.dumps(receipt).encode()
    monkeypatch.setattr(dispatch_module, "resolve_oci_artifact", resolve)
    monkeypatch.setattr(dispatch_module, "read_prepare_publisher_digest", lambda *_: "sha256:" + "e" * 64)
    monkeypatch.setattr(run_release_workflow, "create_github_deployment", lambda *_args, **_kwargs: 987)
    monkeypatch.setattr(run_release_workflow, "update_github_deployment", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("GITHUB_ACTOR", "codex-test")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")


def _direct_owner_record(value):
    from release_authorization import owner_task_commitment_sha256
    value.update(environment="production", source_instruction_identifier="OWNER_TASK_MAXIBOT_TEST_RELEASE_20261020",
        owner_task_reference="Direct OWNER full-cycle release instruction for this bounded test task.",
        candidate_selection_rule="EXACT_CURRENT_MAIN_WITHIN_AUTHORIZED_SCOPE",
        allowed_operations=["integrate", "coalesce", "prepare", "preflight", "deploy", "verify", "rollback", "publish_evidence"])
    value["owner_task_commitment_sha256"] = owner_task_commitment_sha256(value)
    return value


def test_dispatch_selects_exact_current_main_and_latest_successful_prepare():
    github = FakeGitHub()
    selected = validate(values(), github)
    assert selected["candidate"] == SHA
    assert selected["target_migration"] == MIGRATION
    assert selected["prepare_run_id"] == "14"
    assert selected["prepare_run_attempt"] == "1"
    assert any(f"head_sha={SHA}" in path for path in github.paths)


@pytest.mark.parametrize("status", [404, 401, 403])
def test_github_get_preserves_http_status_without_retry_or_private_detail(status):
    import urllib.error

    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(
            request.full_url, status, "private-upstream-detail", {}, None)

    with pytest.raises(ValidationError, match=f"HTTP {status}") as failure:
        github_get("git/ref/tags/test/revoked", "test-token", opener)

    assert len(calls) == 1
    assert "private-upstream-detail" not in str(failure.value)


def test_github_get_recovers_after_extended_tls_outage_and_continues_read():
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return b'{"object":{"sha":"' + SHA.encode() + b'"}}'

    import ssl
    import urllib.error
    from release_network_retry import RetryBudget

    calls = []
    now = [0.0]
    events = []

    def opener(_request, timeout):
        assert timeout == 30
        calls.append(now[0])
        if len(calls) <= 27:
            raise urllib.error.URLError(ssl.SSLError("TLS read timeout"))
        return Response()

    result = github_get(
        "git/ref/heads/main", "test-token", opener,
        budget=RetryBudget(1800, clock=lambda: now[0]),
        retry_options={
            "clock": lambda: now[0],
            "sleep": lambda seconds: now.__setitem__(0, now[0] + seconds),
            "jitter": lambda _low, high: high,
            "emit": events.append,
        },
    )
    assert result["object"]["sha"] == SHA
    assert now[0] > 1200
    assert any(event.startswith("NETWORK_RETRYING") for event in events)
    assert any(event.startswith("NETWORK_RESTORED") for event in events)
    assert events[-1] == "RELEASE_CONTINUING stage=release_provenance"


@pytest.mark.parametrize(
    ("expected_id", "expected_attempt"),
    [("13", "1"), ("14", "2")],
)
def test_dispatch_recheck_rejects_changed_prepare_run_or_attempt(expected_id, expected_attempt):
    with pytest.raises(ValidationError, match="changed after artifact selection"):
        validate(values(PREPARE_RUN_ID=expected_id, PREPARE_RUN_ATTEMPT=expected_attempt), FakeGitHub())


def test_dispatch_recheck_accepts_same_prepare_run_and_attempt():
    selected = validate(values(PREPARE_RUN_ID="14", PREPARE_RUN_ATTEMPT="1"), FakeGitHub())
    assert selected["prepare_run_id"] == "14"
    assert selected["prepare_run_attempt"] == "1"


def test_dispatch_recheck_requires_run_id_and_attempt_together():
    with pytest.raises(ValidationError, match="must be supplied together"):
        validate(values(PREPARE_RUN_ID="14"), FakeGitHub())


@pytest.mark.parametrize("candidate", ["main", "a" * 39, "A" * 40, "a" * 40 + "\n"])
def test_dispatch_rejects_branch_names_and_noncanonical_shas(candidate):
    with pytest.raises(ValidationError, match="full 40-character"):
        validate(values(CANDIDATE_SHA=candidate), FakeGitHub())


def test_dispatch_rejects_candidate_that_is_no_longer_main():
    with pytest.raises(ValidationError, match="exact current origin/main"):
        validate(values(), FakeGitHub(main_sha="b" * 40))


@pytest.mark.parametrize("status,conclusion", [("in_progress", None), ("completed", "failure")])
def test_dispatch_rejects_unfinished_or_failed_latest_prepare(status, conclusion):
    run = {"id": 9, "run_number": 2, "run_attempt": 1, "head_sha": SHA,
           "status": status, "conclusion": conclusion, "event": "push", "head_branch": "main"}
    with pytest.raises(ValidationError, match="latest Prepare"):
        validate(values(), FakeGitHub(runs=[run]))


def test_dispatch_rejects_missing_immutable_prepare_release():
    def missing(*_args):
        raise dispatch_module.ArtifactError("GHCR receipt is missing")
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(dispatch_module, "resolve_oci_artifact", missing)
        with pytest.raises(ValidationError, match="immutable Prepare OCI receipt"):
            validate(values(), FakeGitHub())


def test_dispatch_rejects_release_tag_that_points_to_another_commit():
    class ForeignTag(FakeGitHub):
        def __call__(self, path, token):
            if path.startswith("git/ref/tags/"):
                return {"object": {"type": "commit", "sha": "b" * 40}}
            return super().__call__(path, token)

    def foreign(*_args):
        raise dispatch_module.ArtifactError("receipt candidate SHA differs")
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(dispatch_module, "resolve_oci_artifact", foreign)
        with pytest.raises(ValidationError, match="candidate SHA"):
            validate(values(), ForeignTag())


def test_publication_recovery_requires_exact_prior_deploy_and_is_not_production_approval():
    selected = validate(values(
        RELEASE_MODE="publication-recovery", RECONCILES_DEPLOY_RUN_ID="777",
        RECONCILES_DEPLOY_RUN_ATTEMPT="3",
    ), FakeGitHub())
    assert selected["mode"] == "publication-recovery"
    assert selected["reconciles_deploy_run_id"] == "777"
    assert selected["reconciles_deploy_run_attempt"] == "3"
    with pytest.raises(ValidationError, match="prior trusted production Deploy"):
        validate(values(
            RELEASE_MODE="publication-recovery", RECONCILES_DEPLOY_RUN_ID="777",
            RECONCILES_DEPLOY_RUN_ATTEMPT="2",
        ), FakeGitHub())
    with pytest.raises(ValidationError, match="owner approval"):
        validate(values(RELEASE_MODE="production"), FakeGitHub())


def test_deploy_workflow_resolves_digest_pinned_prepare_receipt_and_uses_portable_pinned_python():
    workflow = (ROOT / ".github/workflows/maxibot-deploy.yml").read_text()
    assert "if: always() && steps.execute.outcome != 'skipped'" in workflow
    assert "scripts/ghcr_prepare_cache.py" in workflow
    assert "PREPARE_RECEIPT_DIGEST: ${{ needs.validate.outputs.prepare_receipt_digest }}" in workflow
    assert "IMAGE_DIGEST: ${{ needs.validate.outputs.image_digest }}" in workflow
    assert "actions/download-artifact@v4" not in workflow
    assert "python scripts/" not in workflow
    assert '"$MAXIBOT_PYTHON" scripts/validate_release_dispatch.py' in workflow
    assert "scripts/ghcr_release_artifacts.py" in workflow
    assert "astral-sh/setup-uv@bec219d24cd3e171d82865faccec33120bb574f4" in workflow
    assert "uv python install 3.12.10" in workflow
    assert "uv python find --managed-python 3.12.10" in workflow
    assert "Python 3.12.10" in workflow
    assert "actions/setup-python@v5" not in workflow
    assert 'RUNNER_TOOL_CACHE=$HOME/.cache/maxibot-release/toolcache" >> "$GITHUB_ENV"' in workflow
    assert 'PYTHONDONTWRITEBYTECODE: "1"' in workflow
    assert 'git status --porcelain' in workflow
    assert "PREPARE_RUN_ID: ${{ needs.validate.outputs.prepare_run_id }}" in workflow
    assert "PREPARE_RUN_ATTEMPT: ${{ needs.validate.outputs.prepare_run_attempt }}" in workflow
    assert "inputs.mode == 'publication-recovery'" in workflow
    assert "evidence_publication: ${{ steps.evidence.outcome }}" in workflow
    assert "Publish immutable Deploy evidence to GHCR" in workflow
    assert "scripts/rehearse_prepared_image.py" not in workflow
    prepare = (ROOT / ".github/workflows/maxibot-prepare.yml").read_text()
    assert "scripts/rehearse_private_docker.py" in prepare
    assert "scripts/rehearse_worker_memory.py" not in prepare
    assert "WORKER_MEMORY_BUDGET_REHEARSAL" not in prepare
    assert "docker/setup-docker-action@2bf61fb9464cc67f0cbdeabed6aa0380accd1c70" in prepare
    assert '"containerd-snapshotter":false' in prepare
    assert "private_docker_rehearsal" in prepare
    assert '"native_image_id": native_image_id' in prepare
    assert 'ARCHIVE_CONFIG_ID=' in prepare
    assert "MAXIBOT_RELEASE_ADMIN_TOKEN" not in prepare
    assert "MAXIBOT_RELEASE_ADMIN_TOKEN" not in workflow
    assert "Publish complete Prepare artifacts" not in prepare
    assert "Publish branch rehearsal receipt" not in prepare
    assert '"release", "create"' in (ROOT / "scripts/release_assets.py").read_text()
    assert "application/octet-stream" in (ROOT / "scripts/prepared_artifact_cache.py").read_text()
    assert "MAXIBOT_RELEASE_ADMIN_TOKEN" not in (ROOT / "docs/RELEASE_AUTOMATION.md").read_text()
    assets = (ROOT / "scripts/release_assets.py").read_text()
    assert "MAXIBOT_RELEASE_ADMIN_TOKEN" not in assets
    assert "verify_immutable_releases_enabled" not in assets


def test_prepare_write_capability_is_unavailable_to_branch_dispatch():
    workflow = (ROOT / ".github/workflows/maxibot-prepare.yml").read_text()
    assert "if: github.ref == 'refs/heads/main'" in workflow
    assert "BRANCH_REHEARSAL_SHA=PASS" not in workflow
    assert "Publish branch rehearsal receipt" not in workflow
    assert "always() && github.ref == 'refs/heads/main'" in workflow


def test_prepare_writes_valid_json_newline_to_image_manifest():
    workflow = (ROOT / ".github/workflows/maxibot-prepare.yml").read_text()
    assert 'sort_keys=True) + "\\n")' in workflow
    assert 'sort_keys=True) + "\\\\n")' not in workflow


@pytest.mark.parametrize(
    ("prepare_result", "expected"),
    [("success", "CI_PASS"), ("failure", "FAILED"), ("cancelled", "CANCELLED"), ("skipped", "SKIPPED")],
)
def test_linear_candidate_status_reflects_final_prepare_and_artifact_result(prepare_result, expected):
    workflow = (ROOT / ".github/workflows/maxibot-prepare.yml").read_text()
    assert "Publish immutable Prepare receipt to GHCR" in workflow
    assert workflow.index("Publish immutable Prepare receipt to GHCR") < workflow.index("Publish final Prepare result to MAXIBOT Linear")
    assert "PREPARE_RESULT: ${{ needs.prepare.result }}" in workflow
    assert publish_release_to_linear.candidate_status(prepare_result) == expected


def test_preflight_only_does_not_record_production_event():
    workflow = (ROOT / ".github/workflows/maxibot-deploy.yml").read_text()
    publisher = workflow.split("  publish:", 1)[1]
    assert "if: always() && github.ref == 'refs/heads/main'" in publisher
    assert "needs.validate.outputs.candidate || inputs.candidate || github.sha" in publisher
    assert "ref: ${{ github.sha }}" in publisher
    step = workflow.split("- name: Publish production result to MAXIBOT Linear", 1)[1]
    assert "inputs.mode == 'production' || inputs.mode == 'task-scoped-auto' || inputs.mode == 'publication-recovery'" in step
    assert "RELEASE_PHASE: production" in step
    assert "RELEASE_PHASE: production" not in workflow.split("- name: Publish preflight result to MAXIBOT Linear", 1)[1].split("- name: Publish production result to MAXIBOT Linear", 1)[0]


@pytest.mark.parametrize("status", ["FAILED_PRE_SWITCH", "COMPLETE", "RECOVERY_REQUIRED", "FAILED"])
def test_deploy_linear_records_canonical_attempt_status(status):
    workflow = (ROOT / ".github/workflows/maxibot-deploy.yml").read_text()
    assert "needs.deploy.outputs.status || 'FAILED'" in workflow
    assert status in (ROOT / "scripts/run_release_workflow.py").read_text() or status == "FAILED"
    assert "PLANNED" not in publish_release_to_linear.STATUSES["production"]


def test_linear_rerun_attempts_keep_distinct_journal_events():
    publisher = (ROOT / "scripts/publish_release_to_linear.py").read_text()
    assert "GITHUB_RUN_ATTEMPT" in publisher
    assert "ATTEMPT_{attempt}" in publisher
    assert "предыдущая запись сохранена" in publisher
    first = publish_release_to_linear.journal_event_marker("123", "1", "candidate", "CI_PASS")
    second = publish_release_to_linear.journal_event_marker("123", "2", "candidate", "CI_PASS")
    journal = append_journal_event("history", first, "first")
    journal = append_journal_event(journal, second, "second")
    assert first in journal and second in journal
    assert "first" in journal and "second" in journal


def test_publisher_uses_only_maxibot_documents():
    source = (ROOT / "scripts/publish_release_to_linear.py").read_text()
    assert 'HANDOFF_ID = "6f799a5c-75b2-4fc1-a522-86bab4c1950b"' in source
    assert 'JOURNAL_ID = "13df4e43-f252-424c-afca-fdb2a37eaf29"' in source
    assert "1d84a3c6-f297-40c2-b9e7-ab81bc749c48" not in source
    assert "56f7f9f5-c6e1-42b7-8c29-5b24e6c41a06" not in source


def test_publisher_reads_legacy_actions_result_only_when_evidence_release_is_absent(monkeypatch):
    import io
    import zipfile

    result = {"mode": "production", "status": "COMPLETE", "candidate_sha": SHA,
              "github_run_id": "321", "github_run_attempt": "1", "final_runtime_sha": SHA}
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("maxibot-release-result-321-1.json", json.dumps(result))
    run = {"id": 321, "run_attempt": 1, "head_sha": SHA}
    calls = []

    def api(path):
        calls.append(path)
        if "/releases/tags/" in path:
            raise SyncError("GitHub resource not found")
        if path.endswith("/artifacts"):
            return {"artifacts": [{"name": "maxibot-release-result-321-1", "expired": False,
                                   "archive_download_url": "https://api.github.com/result.zip"}]}
        raise AssertionError(path)

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self):
            return payload.getvalue()

    class FakeOpener:
        def open(self, _request, timeout):
            assert timeout == 30
            return Response()

    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_ACTOR", "codex-test")
    monkeypatch.setattr(publish_release_to_linear, "resolve_oci_artifact",
        lambda *_args: (_ for _ in ()).throw(publish_release_to_linear.MissingArtifactError("missing")))
    monkeypatch.setattr(publish_release_to_linear, "github_json", api)
    monkeypatch.setattr(publish_release_to_linear.urllib.request, "build_opener", lambda *_args: FakeOpener())
    assert publish_release_to_linear.read_result_artifact(run) == result
    assert any("/actions/runs/321/artifacts" in path for path in calls)


def test_publisher_reads_legacy_immutable_release_evidence_before_expired_actions_artifact(monkeypatch):
    run = {"id": 321, "run_attempt": 1, "head_sha": SHA}
    result = {"candidate_sha": SHA, "github_run_id": "321", "github_run_attempt": "1",
              "final_runtime_sha": SHA, "status": "COMPLETE"}
    calls = []

    def api(path):
        calls.append(path)
        if "/releases/tags/" in path:
            return {"tag_name": "maxibot-evidence-" + SHA + "-321-1"}
        raise AssertionError(path)

    def read_legacy(_sha, _run_id, _attempt, target_dir, _github_get, _token):
        Path(target_dir, "result.json").write_text(json.dumps(result), encoding="utf-8")
        return {}

    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_ACTOR", "codex-test")
    monkeypatch.setattr(publish_release_to_linear, "resolve_oci_artifact",
        lambda *_args: (_ for _ in ()).throw(publish_release_to_linear.MissingArtifactError("missing")))
    monkeypatch.setattr(publish_release_to_linear, "github_json", api)
    monkeypatch.setattr(publish_release_to_linear, "read_evidence", read_legacy)

    assert publish_release_to_linear.read_result_artifact(run) == result
    assert calls == ["repos/akhripunov/maxibot/releases/tags/maxibot-evidence-" + SHA + "-321-1"]


def test_publisher_prefers_digest_pinned_ghcr_evidence_without_legacy_fallback(monkeypatch, tmp_path):
    run = {"id": 322, "run_attempt": 2, "head_sha": SHA}
    calls = []

    evidence = {"schema": "MAXIBOT_RELEASE_WORKFLOW_RESULT_V1", "candidate_sha": SHA,
        "github_run_id": "322", "github_run_attempt": "2", "canonical": True}

    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_ACTOR", "codex-test")
    monkeypatch.setattr(publish_release_to_linear, "resolve_oci_artifact",
        lambda *_args: ("sha256:" + "f" * 64, json.dumps(evidence).encode()))
    result = publish_release_to_linear.read_result_artifact(run)
    assert result["canonical"] is True
    assert result["evidence_digest"] == "sha256:" + "f" * 64


def test_publisher_does_not_fallback_when_existing_ghcr_evidence_is_invalid(monkeypatch, tmp_path):
    run = {"id": 323, "run_attempt": 1, "head_sha": SHA}
    calls = []
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_ACTOR", "codex-test")
    monkeypatch.setattr(
        publish_release_to_linear, "resolve_oci_artifact",
        lambda *_args: (_ for _ in ()).throw(publish_release_to_linear.ArtifactError("bad digest")),
    )
    with pytest.raises(SyncError, match="GHCR Deploy evidence failed validation"):
        publish_release_to_linear.read_result_artifact(run)
    assert not calls


def test_current_state_block_is_identical_idempotent_and_preserves_manual_handoff():
    state = {
        "synced_at":"2026-10-03T07:00:00+00:00", "main_sha":"a"*40, "main_checked_at":"now",
        "production_sha":"b"*40, "production_checked_at":"then", "production_status":"COMPLETE",
        "in_sync":False, "prepare":{"head_sha":"a"*40,"id":12}, "current_prepare":{"conclusion":"success"},
        "candidate":"READY", "deploy":{"id":14,"html_url":"https://github.test/run"},
        "evidence":{"final_migration":"0098_durable_autopilot_execution_claim_v1"},
    }
    block = publish_release_to_linear.render_current_state(state)
    handoff = publish_release_to_linear.upsert_current_state("## Owner section\nkeep", block, handoff=True)
    journal = publish_release_to_linear.upsert_current_state("# Journal\n\nhistory", block)
    assert handoff.startswith("<!-- MAXIBOT_CURRENT_STATE_START -->")
    assert "## Owner section\nkeep" in handoff
    assert "MAXIBOT_CURRENT_STATE_START" in journal.split("history")[0]
    assert "MAIN_AHEAD_OF_PRODUCTION" in block
    assert "ADS_AUTOPILOT_ENABLED: **FALSE**" in block
    assert publish_release_to_linear.upsert_current_state(handoff, block, handoff=True) == handoff
    assert "GPOINT_CURRENT_STATE" not in block


def test_current_state_markers_fail_closed_on_duplicates():
    block = publish_release_to_linear.render_current_state({
        "synced_at":"t", "main_sha":"a"*40, "main_checked_at":"t", "production_sha":"a"*40,
        "production_checked_at":"t", "production_status":"COMPLETE", "in_sync":True,
        "prepare":None, "current_prepare":None, "candidate":"READY", "deploy":{"id":1,"html_url":"url"},
        "evidence":{"final_migration":"0098"},
    })
    bad = "<!-- MAXIBOT_CURRENT_STATE_START -->a<!-- MAXIBOT_CURRENT_STATE_END -->\n<!-- MAXIBOT_CURRENT_STATE_START -->b<!-- MAXIBOT_CURRENT_STATE_END -->"
    with pytest.raises(SyncError, match="CURRENT STATE markers"):
        publish_release_to_linear.upsert_current_state(bad, block)


def test_current_state_repairs_duplicate_adjacent_end_marker():
    block = publish_release_to_linear.render_current_state({
        "synced_at":"t", "main_sha":"a"*40, "main_checked_at":"t", "production_sha":"a"*40,
        "production_checked_at":"t", "production_status":"COMPLETE", "in_sync":True,
        "prepare":None, "current_prepare":None, "candidate":"READY", "deploy":{"id":1,"html_url":"url"},
        "evidence":{"final_migration":"0098"},
    })
    existing = "<!-- MAXIBOT_CURRENT_STATE_START -->old<!-- MAXIBOT_CURRENT_STATE_END --><!-- MAXIBOT_CURRENT_STATE_END -->\nmanual"
    repaired = publish_release_to_linear.upsert_current_state(existing, block, handoff=True)
    assert repaired.count("<!-- MAXIBOT_CURRENT_STATE_START -->") == 1
    assert repaired.count("<!-- MAXIBOT_CURRENT_STATE_END -->") == 1
    assert repaired.endswith("\nmanual")
    assert "<!-- MAXIBOT_CURRENT_STATE_START -->\n\nСинхронизировано:" in repaired


def test_current_state_repairs_duplicate_orphaned_end_markers():
    block = publish_release_to_linear.render_current_state({
        "synced_at":"t", "main_sha":"a"*40, "main_checked_at":"t", "production_sha":"a"*40,
        "production_checked_at":"t", "production_status":"COMPLETE", "in_sync":True,
        "prepare":None, "current_prepare":None, "candidate":"READY", "deploy":{"id":1,"html_url":"url"},
        "evidence":{"final_migration":"0098"},
    })
    existing = "manual\n<!-- MAXIBOT_CURRENT_STATE_END --><!-- MAXIBOT_CURRENT_STATE_END -->\nnotes"
    repaired = publish_release_to_linear.upsert_current_state(existing, block, handoff=True)
    assert repaired.count("<!-- MAXIBOT_CURRENT_STATE_START -->") == 1
    assert repaired.count("<!-- MAXIBOT_CURRENT_STATE_END -->") == 1
    assert "manual" in repaired and "notes" in repaired


def test_self_run_in_progress_waits_for_workflow_run_reconciliation():
    run = {"id": 44, "head_sha":"a"*40, "status":"in_progress", "conclusion":None}
    event = {"sha":"a"*40,"run_id":"44","status":"CI_PASS"}
    assert publish_release_to_linear.current_candidate_status("a"*40, run, event) == "PREPARE_GATES_PASS_WORKFLOW_PENDING"
    assert run["status"] == "in_progress" and run["conclusion"] is None
    assert publish_release_to_linear.current_candidate_status("a"*40, run) == "NOT_PREPARED_FOR_CURRENT_MAIN"


def test_final_completion_reconciles_ready_and_old_run_cannot_revert_it():
    completed = {"id":44,"head_sha":"a"*40,"status":"completed","conclusion":"success"}
    assert publish_release_to_linear.current_candidate_status("a"*40, completed) == "READY"
    current = {"id":44,"head_sha":"a"*40,"status":"in_progress"}
    stale = {"id":43,"head_sha":"a"*40,"status":"completed","conclusion":"success"}
    stale_event = {"sha":"a"*40,"run_id":"43","status":"CI_PASS"}
    assert publish_release_to_linear.current_candidate_status("a"*40, current, stale_event) == "NOT_PREPARED_FOR_CURRENT_MAIN"
    assert publish_release_to_linear.current_candidate_status("a"*40, stale, stale_event) == "READY"
    workflow = (ROOT / ".github/workflows/maxibot-linear-reconcile.yml").read_text()
    assert "types: [completed]" in workflow
    assert "STATE_SYNC_ONLY: 'true'" in workflow
    assert "group: maxibot-linear-release" in workflow


def test_prepare_failure_for_current_main_does_not_reuse_old_success(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test")
    monkeypatch.setattr(publish_release_to_linear, "latest_runs", lambda workflow: [
        {"id":1,"head_sha":"a"*40,"created_at":"2026-10-03T06:00:00Z","conclusion":"success","status":"completed"},
        {"id":2,"head_sha":"a"*40,"created_at":"2026-10-03T07:00:00Z","conclusion":"failure","status":"completed"},
    ] if "prepare" in workflow else [{"id":3,"head_sha":"b"*40,"status":"completed","conclusion":"success","name":"MAXIBOT production candidate"}])
    monkeypatch.setattr(publish_release_to_linear, "read_result_artifact", lambda run: {
        "mode":"production", "status":"COMPLETE", "github_run_id":"3", "github_run_attempt":"1",
        "final_runtime_sha":"b"*40, "candidate_sha":"b"*40,
        "final_migration":"0098"})
    monkeypatch.setattr(publish_release_to_linear, "release_jobs", lambda _run: {
        name: {"status":"completed", "conclusion":"success"}
        for name in ("validate", "deploy", "publish")
    })
    monkeypatch.setattr(publish_release_to_linear, "github_json", lambda path: (
        {"sha":"a"*40,"commit":{"committer":{"date":"t"}}} if "commits/main" in path else {"workflow_runs":[]}))
    state = publish_release_to_linear.current_state()
    assert state["candidate"] == "NOT_PREPARED_FOR_CURRENT_MAIN"
    assert state["prepare"]["id"] == 1
    assert state["current_prepare"]["id"] == 2


def test_task_scoped_auto_complete_is_reconciled_as_production(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test")
    task_run = {
        "id": 7, "head_sha": SHA, "status": "completed", "conclusion": "success",
        "name": "MAXIBOT task-scoped-auto " + SHA,
        "html_url": "https://github.com/akhripunov/maxibot/actions/runs/7",
        "created_at": "2026-10-19T09:00:00Z",
    }
    monkeypatch.setattr(publish_release_to_linear, "latest_runs", lambda workflow: (
        [{"id": 4, "head_sha": SHA, "status": "completed", "conclusion": "success"}]
        if "prepare" in workflow else [task_run]
    ))
    result = {
        "mode": "task-scoped-auto", "status": "COMPLETE", "github_run_id": "7", "github_run_attempt": "1",
        "candidate_sha": SHA, "final_runtime_sha": SHA,
        "target_migration": MIGRATION, "final_migration": MIGRATION,
        "production_changed": True, "github_deployment_id": None,
    }
    monkeypatch.setattr(publish_release_to_linear, "read_result_artifact", lambda run: result)
    monkeypatch.setattr(publish_release_to_linear, "release_jobs", lambda _run: {
        name: {"status":"completed", "conclusion":"success"}
        for name in ("validate", "deploy", "publish")
    })
    monkeypatch.setattr(publish_release_to_linear, "github_json", lambda path: (
        {"sha": SHA, "commit": {"committer": {"date": "t"}}}
        if "commits/main" in path else {"workflow_runs": []}
    ))

    state = publish_release_to_linear.current_state()

    assert state["deploy"] == task_run
    assert state["production_sha"] == SHA
    assert state["production_changed"] is True
    assert state["reconciliation"] is False


def test_read_only_publication_reconciliation_becomes_canonical_linear_state(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test")
    recovery_run = {
        "id": 9, "head_sha": SHA, "status": "completed", "conclusion": "success",
        "name": "publication-recovery " + SHA,
        "html_url": "https://github.com/akhripunov/maxibot/actions/runs/9",
        "created_at": "2026-10-04T09:00:00Z",
    }
    monkeypatch.setattr(publish_release_to_linear, "latest_runs", lambda workflow: (
        [{"id": 4, "head_sha": SHA, "status": "completed", "conclusion": "success"}]
        if "prepare" in workflow else [recovery_run]
    ))
    recovery_result = {
        "mode": "publication-recovery", "status": "COMPLETE",
        "github_run_id": "9", "github_run_attempt": "1",
        "candidate_sha": SHA, "final_runtime_sha": SHA,
        "target_migration": MIGRATION, "final_migration": MIGRATION,
        "production_changed": False,
        "recovery": "READ_ONLY_RECONCILIATION_PASS",
        "reconciles_deploy_run_id": "8", "reconciles_deploy_run_attempt": "1",
    }
    monkeypatch.setattr(publish_release_to_linear, "read_result_artifact", lambda run: recovery_result)
    monkeypatch.setattr(publish_release_to_linear, "release_jobs", lambda _run: {
        name: {"status":"completed", "conclusion":"success"}
        for name in ("validate", "deploy", "publish")
    })
    monkeypatch.setattr(publish_release_to_linear, "github_json", lambda path: (
        {"sha": SHA, "commit": {"committer": {"date": "t"}}}
        if "commits/main" in path else {"workflow_runs": []}
    ))

    state = publish_release_to_linear.current_state()

    assert state["deploy"] == recovery_run
    assert state["production_sha"] == SHA
    assert state["production_changed"] is False
    assert state["reconciliation"] is True
    rendered = publish_release_to_linear.render_current_state(state)
    assert "read-only reconciliation" in rendered.lower()
    assert "Последний production Deploy" not in rendered


def _publisher_recovery_fixture(monkeypatch, *, run_status="in_progress", conclusion=None,
                                validate_conclusion="success", deploy_conclusion="success",
                                evidence_sha=SHA, evidence_attempt="2", evidence_digest="sha256:" + "f" * 64,
                                deployment_state="success", mode="publication-recovery",
                                deployment_payload=None, main_sha=SHA, old_sha=SHA,
                                old_deployment_state="inactive"):
    run = {
        "id": 970, "run_attempt": 2, "head_sha": main_sha, "head_branch": "main",
        "event": "workflow_dispatch", "path": ".github/workflows/maxibot-deploy.yml",
        "status": run_status, "conclusion": conclusion,
        "name": f"MAXIBOT {mode} {main_sha}", "html_url": "https://github.com/akhripunov/maxibot/actions/runs/970",
        "created_at": "2026-10-22T10:00:00Z",
    }
    result = {
        "mode": mode, "status": "COMPLETE", "github_run_id": "970",
        "github_run_attempt": evidence_attempt, "candidate_sha": evidence_sha,
        "final_runtime_sha": evidence_sha, "target_migration": MIGRATION,
        "final_migration": MIGRATION, "production_changed": mode != "publication-recovery",
        "recovery": "READ_ONLY_RECONCILIATION_PASS" if mode == "publication-recovery" else None,
        "reconciles_deploy_run_id": "969" if mode == "publication-recovery" else None,
        "reconciles_deploy_run_attempt": "1" if mode == "publication-recovery" else None,
        "github_deployment_id": 12345, "evidence_digest": evidence_digest,
    }
    old = {
        "id": 968, "run_attempt": 1, "head_sha": old_sha, "head_branch": "main",
        "event": "workflow_dispatch", "path": ".github/workflows/maxibot-deploy.yml",
        "status": "completed", "conclusion": "success",
        "name": f"MAXIBOT production {old_sha}", "html_url": "https://github.com/akhripunov/maxibot/actions/runs/968",
        "created_at": "2026-10-20T10:00:00Z",
    }
    old_result = {**result, "mode": "production", "github_run_id": "968", "github_run_attempt": "1",
                  "candidate_sha": old_sha, "final_runtime_sha": old_sha,
                  "github_deployment_id": 12344, "recovery": None,
                  "reconciles_deploy_run_id": None, "reconciles_deploy_run_attempt": None}
    monkeypatch.setattr(publish_release_to_linear, "latest_runs", lambda workflow: (
        [{"id": 4, "head_sha": SHA, "status": "completed", "conclusion": "success"}]
        if "prepare" in workflow else [run, old]
    ))
    monkeypatch.setattr(publish_release_to_linear, "read_result_artifact",
                        lambda selected: result if str(selected["id"]) == "970" else old_result)
    jobs = {
        "970": [
            {"name": "validate", "status": "completed", "conclusion": validate_conclusion},
            {"name": "deploy", "status": "completed", "conclusion": deploy_conclusion},
            {"name": "publish", "status": "in_progress" if run_status == "in_progress" else "completed",
             "conclusion": None if run_status == "in_progress" else ("failure" if conclusion == "failure" else "success")},
        ],
        "968": [
            {"name": "validate", "status": "completed", "conclusion": "success"},
            {"name": "deploy", "status": "completed", "conclusion": "success"},
            {"name": "publish", "status": "completed", "conclusion": "success"},
        ],
    }
    calls = []

    def api(path):
        calls.append(path)
        if path.endswith("/commits/main"):
            return {"sha": main_sha, "commit": {"committer": {"date": "t"}}}
        if path.endswith("/deployments/12345"):
            payload = deployment_payload
            if payload is None:
                payload = {"workflow_run_id": "969", "workflow_run_attempt": "1"}
            return {"sha": evidence_sha, "environment": "maxibot-production", "payload": payload}
        if path.endswith("/deployments/12345/statuses?per_page=100"):
            return [{"state": deployment_state}]
        if path.endswith("/deployments/12344"):
            return {"sha": old_sha, "environment": "maxibot-production",
                    "payload": {"workflow_run_id": "968", "workflow_run_attempt": "1"}}
        if path.endswith("/deployments/12344/statuses?per_page=100"):
            return [{"state": old_deployment_state}]
        if "/actions/runs/970/attempts/2/jobs?per_page=100" in path:
            return {"jobs": jobs["970"]}
        if "/actions/runs/968/attempts/1/jobs?per_page=100" in path:
            return {"jobs": jobs["968"]}
        if path.endswith("/actions/workflows/maxibot-prepare.yml/runs?branch=main&per_page=100"):
            return {"workflow_runs": []}
        if path.endswith("/actions/workflows/maxibot-deploy.yml/runs?branch=main&per_page=100"):
            return {"workflow_runs": [run, old]}
        raise AssertionError(path)

    monkeypatch.setattr(publish_release_to_linear, "github_json", api)
    return run, result, calls


def test_publisher_accepts_current_run_while_linear_publisher_job_is_still_running(monkeypatch):
    run, _result, calls = _publisher_recovery_fixture(monkeypatch)

    state = publish_release_to_linear.current_state()

    assert state["deploy"] == run
    assert state["reconciliation"] is True
    assert any("/actions/runs/970/attempts/2/jobs" in path for path in calls)


def test_publisher_recovers_publisher_only_failed_run_and_skips_inactive_previous_deployment(monkeypatch):
    run, _result, calls = _publisher_recovery_fixture(
        monkeypatch, run_status="completed", conclusion="failure")

    state = publish_release_to_linear.current_state()

    assert state["deploy"] == run
    assert state["reconciliation"] is True
    assert any("/deployments/12345" in path for path in calls)
    assert not any("/deployments/12344" in path for path in calls)


@pytest.mark.parametrize(("validate_conclusion", "deploy_conclusion"), [
    ("failure", "success"), ("success", "failure"),
])
def test_publisher_rejects_run_with_failed_validate_or_deploy_job(monkeypatch, validate_conclusion,
                                                                  deploy_conclusion):
    _run, _result, calls = _publisher_recovery_fixture(
        monkeypatch, run_status="completed", conclusion="failure",
        validate_conclusion=validate_conclusion, deploy_conclusion=deploy_conclusion)

    with pytest.raises(SyncError):
        publish_release_to_linear.current_state()
    assert not any("/deployments/12345" in path for path in calls)


def test_failed_new_candidate_keeps_old_successful_runtime_as_factual_state(monkeypatch):
    old_sha = SHA
    new_sha = "b" * 40
    _publisher_recovery_fixture(
        monkeypatch, run_status="completed", conclusion="failure",
        validate_conclusion="failure", deploy_conclusion="failure",
        evidence_sha=new_sha, main_sha=new_sha, old_sha=old_sha,
        old_deployment_state="success")

    state = publish_release_to_linear.current_state()

    assert state["main_sha"] == new_sha
    assert state["production_sha"] == old_sha
    assert state["in_sync"] is False
    assert state["deploy"]["id"] == 968
    assert state["production_status"] == "COMPLETE"


@pytest.mark.parametrize(("evidence_sha", "evidence_attempt", "evidence_digest"), [
    ("b" * 40, "2", "sha256:" + "f" * 64),
    (SHA, "1", "sha256:" + "f" * 64),
    (SHA, "2", None),
])
def test_publisher_rejects_mismatched_or_missing_immutable_attempt_evidence(monkeypatch, evidence_sha,
                                                                             evidence_attempt,
                                                                             evidence_digest):
    _publisher_recovery_fixture(monkeypatch, evidence_sha=evidence_sha,
                                evidence_attempt=evidence_attempt, evidence_digest=evidence_digest)

    with pytest.raises(SyncError):
        publish_release_to_linear.current_state()


def test_publisher_rejects_non_success_deployment_without_falling_back_to_old_inactive_run(monkeypatch):
    _publisher_recovery_fixture(monkeypatch, deployment_state="failure")

    with pytest.raises(SyncError):
        publish_release_to_linear.current_state()


def test_publisher_rejects_recovery_deployment_with_mismatched_original_run_linkage(monkeypatch):
    _publisher_recovery_fixture(monkeypatch, deployment_payload={
        "workflow_run_id": "968", "workflow_run_attempt": "1",
    })

    with pytest.raises(SyncError):
        publish_release_to_linear.current_state()


def test_prepare_write_permission_is_main_only():
    workflow = (ROOT / ".github/workflows/maxibot-prepare.yml").read_text()
    assert "permissions:\n  contents: read\n  actions: read" in workflow
    assert "    if: github.ref == 'refs/heads/main'" in workflow
    prepare_permissions = workflow.split("  prepare:\n", 1)[1].split("    steps:\n", 1)[0]
    assert "permissions:\n      contents: write\n      actions: read" in prepare_permissions
    assert "Publish branch rehearsal receipt" not in workflow


def test_release_apply_loads_verified_prepared_image_without_rebuilding():
    release = (ROOT / "scripts/release_prod.sh").read_text()
    assert 'docker pull "$image_ref"' in release
    assert 'mint-pull-token' in release
    assert 'registrytoken' in release
    assert 'prepared_image_config_digest' in release
    assert 'VPS_LOADED_IMAGE_CONFIG_DIGEST=$RELEASE_IMAGE_ID' in release
    assert 'VPS_LOADED_IMAGE_NATIVE_ID=$REMOTE_LOADED_IMAGE_ID' in release
    assert 'docker build --platform linux/amd64' not in release
    assert 'RELEASE_IMAGE_ID' in release
    assert 'prepared-image.digest' in release


def test_release_workflow_wrapper_never_builds_and_requires_prepare_image():
    wrapper = (ROOT / "scripts/run_release_workflow.py").read_text()
    assert '"docker", "build"' not in wrapper
    assert 'IMAGE_MANIFEST' in wrapper and 'RELEASE_IMAGE_DIGEST' in wrapper
    assert 'PREPARE_RUN_ID' in wrapper


def test_dispatch_requires_exact_operator_confirmation_for_production():
    with pytest.raises(ValidationError, match="explicit owner approval"):
        validate(values(RELEASE_MODE="production"), FakeGitHub())
    with pytest.raises(ValidationError, match="must not carry production approval"):
        validate(values(APPROVE_PRODUCTION="true"), FakeGitHub())
    approved = validate(values(RELEASE_MODE="production", APPROVE_PRODUCTION="true"),
                        FakeGitHub(), lambda *_args: pytest.fail("prior preflight must not be required"))
    assert approved["mode"] == "production"


def test_task_scoped_auto_requires_durable_record_but_runs_preflight_inside_deploy(monkeypatch, tmp_path):
    record_path = tmp_path / "task-auth.json"
    record_path.write_text(json.dumps(_direct_owner_record({
        "schema_version": 3, "project": "maxibot", "authorization_mode": "TASK_SCOPED_AUTO",
        "authorization_id": "maxibot-task-2026-10-06-001",
        "owner_instruction_scope": "FULL_PRODUCTION_CYCLE",
        "source_reference": "linear:release-orchestrator-handoff#owner-instruction-2026-10-06",
        "production_baseline_sha": SHA, "task": "Change one user-facing menu link",
        "allowed_paths": ["app/bot/menu.py"], "acceptance_criteria": ["menu opens the configured website"],
        "exclusions": ["database", "migrations", "security", "secrets", "payments", "ads", "paid_traffic", "release_infrastructure"],
        "state": "ACTIVE", "revoked": False, "consumed": False,
    })))
    monkeypatch.setattr("validate_release_dispatch.record_path", lambda _authorization_id: str(record_path))
    monkeypatch.setattr("validate_release_dispatch.candidate_scope_diff", lambda *_args: ["app/bot/menu.py"])
    github = FakeGitHub()
    github.authorization_markers.add(
        "git/ref/tags/maxibot-task-auth/maxibot-task-2026-10-06-001/claimed")

    def api_with_real_optional_ref_http_errors(path, token):
        if path.endswith(("/revoked", "/consumed")):
            import urllib.error

            github.paths.append(path)

            def opener(request, timeout):
                raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

            return github_get(path, token, opener)
        return github(path, token)

    selected = validate(values(RELEASE_MODE="task-scoped-auto", TASK_AUTHORIZATION_ID="maxibot-task-2026-10-06-001"),
                        api_with_real_optional_ref_http_errors,
                        lambda *_args: pytest.fail("prior preflight must not be required"))
    assert selected["mode"] == "task-scoped-auto"
    assert selected["task_authorization_id"] == "maxibot-task-2026-10-06-001"
    assert selected["task_authorization_changed_paths"] == "app/bot/menu.py"
    assert not any(path.startswith("actions/workflows/maxibot-deploy.yml/") for path in github.paths)
    assert any("/revoked" in path for path in github.paths)
    assert any("/consumed" in path for path in github.paths)
    with pytest.raises(ValidationError, match="durable ID"):
        validate(values(RELEASE_MODE="task-scoped-auto"), github, ready_evidence)
    with pytest.raises(ValidationError, match="must not carry production approval"):
        validate(values(RELEASE_MODE="task-scoped-auto", APPROVE_PRODUCTION="true",
                        TASK_AUTHORIZATION_ID="maxibot-task-2026-10-06-001"), github, ready_evidence)


def test_manual_diagnostic_preflight_with_linked_authorization_does_not_require_a_claim(monkeypatch, tmp_path):
    record_path = tmp_path / "task-auth.json"
    record_path.write_text(json.dumps(_direct_owner_record({
        "schema_version": 3, "project": "maxibot", "authorization_mode": "TASK_SCOPED_AUTO",
        "authorization_id": "maxibot-task-2026-10-06-001",
        "owner_instruction_scope": "FULL_PRODUCTION_CYCLE",
        "source_reference": "linear:ME-31/owner-instruction",
        "production_baseline_sha": SHA, "task": "Change a scoped menu link",
        "allowed_paths": ["app/bot/menu.py"], "acceptance_criteria": ["menu opens"],
        "exclusions": ["database", "migrations", "security", "secrets", "payments",
                       "ads", "paid_traffic", "release_infrastructure"],
        "state": "ACTIVE", "revoked": False, "consumed": False,
    })))
    monkeypatch.setattr("validate_release_dispatch.record_path", lambda _authorization_id: str(record_path))
    monkeypatch.setattr("validate_release_dispatch.candidate_scope_diff", lambda *_args: ["app/bot/menu.py"])
    github = FakeGitHub()
    selected = validate(values(RELEASE_MODE="preflight", TASK_AUTHORIZATION_ID="maxibot-task-2026-10-06-001"),
                        github)
    assert selected["mode"] == "preflight"
    assert any(path.endswith("/revoked") for path in github.paths)
    assert any(path.endswith("/consumed") for path in github.paths)
    assert any(path.endswith("/claimed") for path in github.paths)


def test_production_requires_exact_successful_prepare_and_does_not_require_separate_preflight():
    selected = validate(values(RELEASE_MODE="production", APPROVE_PRODUCTION="true"),
                        FakeGitHub(), lambda *_args: pytest.fail("prior preflight must not be required"))
    assert selected["prepare_run_id"] == "14"
    assert selected["prepare_run_attempt"] == "1"


def test_deploy_workflow_keeps_diagnostic_ready_distinct_from_internal_preflight_pass():
    workflow = (ROOT / ".github/workflows/maxibot-deploy.yml").read_text()
    controller = (ROOT / ".github/workflows/maxibot-auto-preflight.yml").read_text()
    publication = workflow.split("- name: Publish preflight result to MAXIBOT Linear", 1)[1]
    assert "inputs.mode == 'preflight' && needs.deploy.outputs.preflight == 'PASS' && 'READY_FOR_OWNER'" in publication
    assert "needs.deploy.outputs.preflight == 'PASS' && 'PASS'" in publication
    assert 'workflows: ["MAXIBOT — Prepare"]' in controller
    assert '"mode": "task-scoped-auto"' in (ROOT / "scripts/release_orchestrator.py").read_text()
    assert '"preflight", "production", "task-scoped-auto"' in (ROOT / "scripts/run_release_workflow.py").read_text()


def test_failed_in_deploy_preflight_never_reaches_production_apply(monkeypatch, tmp_path):
    result_path, _ = _setup_task_scoped_wrapper(monkeypatch, tmp_path, MIGRATION)
    commands = []
    def execute(command, _env):
        commands.append(command)
        return 1, "SAFE_TO_RELEASE=NO\nDRY_RUN_RESULT=FAILED\n"
    monkeypatch.setattr(run_release_workflow, "run", execute)
    monkeypatch.setattr(run_release_workflow, "verify_task_authorization",
                        lambda *_: pytest.fail("failed preflight must stop before authorization/apply"))
    assert run_release_workflow.main() == 1
    result = json.loads(result_path.read_text())
    assert len(commands) == 1 and commands[0][-1] == "--dry-run"
    assert result["preflight"] == "FAILED"
    assert result["production_changed"] is False


def test_wrapper_passes_direct_transport_only_after_exact_prepare_receipt_validation(monkeypatch, tmp_path):
    result_path, _migration_head = _setup_task_scoped_wrapper(monkeypatch, tmp_path, MIGRATION)
    monkeypatch.delenv("MAXIBOT_IMAGE_TRANSPORT", raising=False)
    observed = []

    def stop_at_dry_run(command, env):
        observed.append((command, env.copy()))
        return 1, "IMAGE_READY=NO\nSAFE_TO_RELEASE=NO\n"

    monkeypatch.setattr(run_release_workflow, "run", stop_at_dry_run)
    assert run_release_workflow.main() == 1
    assert len(observed) == 1 and observed[0][0][-1] == "--dry-run"
    child_env = observed[0][1]
    assert child_env["MAXIBOT_IMAGE_TRANSPORT"] == "GHCR_VPS_DIRECT"
    assert child_env["RELEASE_PREPARE_RECEIPT"] == os.environ["IMAGE_RESULT"]
    assert json.loads(result_path.read_text())["preflight"] == "FAILED"


def test_wrapper_rejects_bad_prepare_receipt_before_enabling_direct_transport(monkeypatch, tmp_path):
    result_path, _migration_head = _setup_task_scoped_wrapper(monkeypatch, tmp_path, MIGRATION)
    receipt_path = os.environ["IMAGE_RESULT"]
    receipt = json.loads(Path(receipt_path).read_text())
    receipt["image"]["config_digest"] = "sha256:" + "f" * 64
    Path(receipt_path).write_text(json.dumps(receipt))
    observed = []
    monkeypatch.setattr(run_release_workflow, "run",
                        lambda command, env: observed.append((command, env)) or pytest.fail("invalid receipt must stop before release_prod"))
    assert run_release_workflow.main() == 1
    assert observed == []
    result = json.loads(result_path.read_text())
    assert result["status"] == "FAILED"
    assert result["production_changed"] is False


def test_github_deployment_creation_error_never_reaches_production_apply(monkeypatch, tmp_path):
    result_path, migration_head = _setup_task_scoped_wrapper(monkeypatch, tmp_path, MIGRATION)
    commands = []

    def execute(command, _env):
        commands.append(command)
        if command[-1] == "--dry-run":
            return 0, f"SAFE_TO_RELEASE=YES\nDRY_RUN_RESULT=PASS\nPROD_DB_HEAD={migration_head}\n"
        pytest.fail("Deployment creation failure must stop before production apply")

    def reject_deployment(*_args, **_kwargs):
        raise ValueError("GitHub Deployment creation failed (HTTP 403); production apply was not started")

    monkeypatch.setattr(run_release_workflow, "run", execute)
    monkeypatch.setattr(run_release_workflow, "create_github_deployment", reject_deployment)
    monkeypatch.setattr(run_release_workflow, "verify_task_authorization",
                        lambda *_args, **_kwargs: {"record_sha256": "b" * 64,
                            "source_reference": "linear:task#owner", "changed_paths": "app/bot/menu.py",
                            "_record": {}, "_changed_paths_list": []})
    monkeypatch.setattr(run_release_workflow, "assert_task_scoped_no_migration", lambda *_args, **_kwargs: None)

    assert run_release_workflow.main() == 1

    result = json.loads(result_path.read_text())
    assert result["status"] == "FAILED_PRE_SWITCH"
    assert result["preflight"] == "PASS"
    assert result["production_changed"] is False
    assert result["authorization_consumed"] is False
    assert "HTTP 403" in result["error"]
    assert commands == [["bash", "scripts/release_prod.sh", SHA, "--dry-run"]]


def test_publication_recovery_is_read_only_and_cannot_repeat_apply():
    wrapper = (ROOT / "scripts/run_release_workflow.py").read_text()
    assert "mode == \"publication-recovery\"" in wrapper
    recovery = wrapper.split('elif mode == "publication-recovery":', 1)[1].split("else:", 1)[0]
    assert "--dry-run" not in recovery  # Dry-run happened above, before the branch.
    assert "--apply" not in recovery
    assert 'result["recovery"] = "READ_ONLY_RECONCILIATION_PASS"' in recovery


def test_publication_recovery_rejects_prior_preflight_run():
    class PriorPreflight(FakeGitHub):
        def __call__(self, path, token):
            result = super().__call__(path, token)
            if path == "actions/runs/777":
                result["display_title"] = f"MAXIBOT preflight {SHA} by owner"
            return result

    with pytest.raises(ValidationError, match="prior trusted production Deploy"):
        validate(values(
            RELEASE_MODE="publication-recovery", RECONCILES_DEPLOY_RUN_ID="777",
            RECONCILES_DEPLOY_RUN_ATTEMPT="3",
        ), PriorPreflight())


def test_immutable_evidence_publication_failure_cannot_trigger_automatic_apply_retry():
    workflow = (ROOT / ".github/workflows/maxibot-deploy.yml").read_text()
    apply_step = workflow.index("Run production preflight and requested release mode")
    evidence_step = workflow.index("Publish immutable Deploy evidence to GHCR")
    assert evidence_step > apply_step
    assert "continue-on-error: true" not in workflow[evidence_step:evidence_step + 240]
    assert "publication-recovery" in workflow
    assert "GITHUB_TOKEN: ${{ github.token }}" in workflow[evidence_step:evidence_step + 400]
    assert "MAXIBOT_RELEASE_ADMIN_TOKEN" not in workflow


def test_apply_wrapper_never_runs_without_owner_confirmation(monkeypatch, tmp_path):
    result_path = tmp_path / "release-result.json"
    for key, value in {
        "CANDIDATE_SHA": SHA,
        "TARGET_MIGRATION": MIGRATION,
        "RELEASE_MODE": "production",
        "APPROVE_PRODUCTION": "false",
        "RELEASE_RESULT": str(result_path),
        "GITHUB_RUN_ID": "123",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(run_release_workflow.subprocess, "check_output", lambda *a, **k: pytest.fail("must reject before shell commands"))
    assert run_release_workflow.main() == 1
    receipt = json.loads(result_path.read_text())
    assert receipt["status"] == "FAILED"
    assert receipt["production_changed"] is False


def test_failed_apply_bookkeeping_does_not_push_main_or_change_candidate(monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator, "assert_production_history", lambda *_a, **_kw: None)
    manifest = tmp_path / "manifest.json"
    result_path = tmp_path / "result.json"
    rehearsal_path = tmp_path / "prepare-result.json"
    image = {"repository": "ghcr.io/akhripunov/maxibot-vps-api", "tag": f"prepare-{SHA}-123-1", "digest": "sha256:" + "2" * 64,
             "config_digest": "sha256:" + "1" * 64, "platform": "linux/amd64", "labels": {
                 "org.opencontainers.image.revision": SHA, "org.opencontainers.image.source-sha": SHA,
                 "org.maxibot.release.sha": SHA}}
    manifest.write_text(json.dumps({"schema": "MAXIBOT_PREPARED_IMAGE_V2", "candidate_sha": SHA,
        "prepare_run_id": "123", "prepare_run_attempt": "1", "image": image}))
    rehearsal_path.write_text(json.dumps({"schema": "MAXIBOT_PREPARE_RECEIPT_V2", "candidate_sha": SHA,
        "github_run_id": "123", "github_run_attempt": "1", "status": "READY", "tests": "success",
        "frozen_path_guard": "success", "linux_amd64_image": "success", "ghcr_image_publish": "success",
        "image": image, "private_docker_rehearsal": "PASS", "private_docker_version": "29.1.3",
        "private_store": "containerd", "private_snapshotter": "overlayfs", "private_network_isolated": "YES",
        "rehearsal_candidate_sha": SHA, "rehearsal_prepare_run_id": "123", "rehearsal_archive_sha256": "a" * 64}))
    for key, value in {
        "CANDIDATE_SHA": SHA, "TARGET_MIGRATION": MIGRATION, "RELEASE_MODE": "production",
        "APPROVE_PRODUCTION": "true", "RELEASE_RESULT": str(result_path), "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1", "PREPARE_RUN_ID": "123", "PREPARE_RUN_ATTEMPT": "1",
        "IMAGE_MANIFEST": str(manifest), "RELEASE_IMAGE_DIGEST": image["digest"],
        "RELEASE_IMAGE_REPOSITORY": image["repository"], "RELEASE_IMAGE_ID": image["config_digest"],
        "IMAGE_RESULT": str(rehearsal_path),
    }.items():
        monkeypatch.setenv(key, value)
    git_commands = []
    def read_git(command, **kwargs):
        git_commands.append(command)
        return SHA if command[:2] == ["git", "rev-parse"] else ""
    monkeypatch.setattr(run_release_workflow.subprocess, "check_output", read_git)
    commands = []
    def execute(command, env):
        commands.append((command, env.copy()))
        if command[-1] == "--dry-run":
            return 0, "SAFE_TO_RELEASE=YES\nDRY_RUN_RESULT=PASS\n"
        return 1, "MIGRATION_ALLOWED=NO\n"
    monkeypatch.setattr(run_release_workflow, "run", execute)
    for attempt in (1, 2):
        result_path = tmp_path / f"attempt-{attempt}.json"
        monkeypatch.setenv("RELEASE_RESULT", str(result_path))
        assert run_release_workflow.main() == 1
        result = json.loads(result_path.read_text())
        assert result["status"] == "FAILED_PRE_SWITCH"
        assert result["production_changed"] is False
        assert result["image_digest"] == image["digest"]
    assert len(commands) == 4
    assert [c[-1] for c, _ in commands] == ["--dry-run", "--apply"]*2
    assert all(SHA in c for c, _ in commands)
    assert all(env["RELEASE_IMAGE_ID"] == "sha256:"+"1"*64 for _, env in commands)
    assert all(command[1] in ("rev-parse", "status") for command in git_commands)


def _setup_task_scoped_wrapper(monkeypatch, tmp_path, migration_head):
    monkeypatch.setattr(orchestrator, "assert_production_history", lambda *_a, **_kw: None)
    manifest = tmp_path / "manifest.json"
    result_path = tmp_path / "result.json"
    rehearsal_path = tmp_path / "prepare-result.json"
    image = {"repository": "ghcr.io/akhripunov/maxibot-vps-api", "tag": f"prepare-{SHA}-123-1", "digest": "sha256:" + "2" * 64,
        "config_digest": "sha256:" + "1" * 64, "platform": "linux/amd64", "labels": {
            "org.opencontainers.image.revision": SHA, "org.opencontainers.image.source-sha": SHA,
            "org.maxibot.release.sha": SHA}}
    manifest.write_text(json.dumps({"schema": "MAXIBOT_PREPARED_IMAGE_V2", "candidate_sha": SHA,
        "prepare_run_id": "123", "prepare_run_attempt": "1", "image": image}))
    rehearsal_path.write_text(json.dumps({"schema": "MAXIBOT_PREPARE_RECEIPT_V2", "candidate_sha": SHA,
        "github_run_id": "123", "github_run_attempt": "1", "status": "READY", "tests": "success",
        "frozen_path_guard": "success", "linux_amd64_image": "success", "ghcr_image_publish": "success", "image": image,
        "private_docker_rehearsal": "PASS",
        "private_docker_version": "29.1.3", "private_store": "containerd", "private_snapshotter": "overlayfs",
        "private_network_isolated": "YES", "rehearsal_candidate_sha": SHA, "rehearsal_prepare_run_id": "123",
        "rehearsal_archive_sha256": "a" * 64}))
    for key, value in {"CANDIDATE_SHA": SHA, "TARGET_MIGRATION": MIGRATION,
        "RELEASE_MODE": "task-scoped-auto", "APPROVE_PRODUCTION": "false",
        "TASK_AUTHORIZATION_ID": "maxibot-task-2026-10-06-001", "RELEASE_RESULT": str(result_path),
        "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "PREPARE_RUN_ID": "123",
        "PREPARE_RUN_ATTEMPT": "1", "IMAGE_MANIFEST": str(manifest),
        "RELEASE_IMAGE_DIGEST": image["digest"], "RELEASE_IMAGE_REPOSITORY": image["repository"],
        "RELEASE_IMAGE_ID": image["config_digest"],
        "IMAGE_RESULT": str(rehearsal_path), "GITHUB_TOKEN": "test-token"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(run_release_workflow.subprocess, "check_output",
        lambda command, **kwargs: SHA if command[:2] == ["git", "rev-parse"] else "")
    return result_path, migration_head


def test_task_scoped_auto_blocks_before_apply_when_migration_head_would_change(monkeypatch, tmp_path):
    result_path, migration_head = _setup_task_scoped_wrapper(monkeypatch, tmp_path, "different-head")
    commands = []
    def execute(command, env):
        commands.append(command)
        return 0, f"SAFE_TO_RELEASE=YES\nDRY_RUN_RESULT=PASS\nPROD_DB_HEAD={migration_head}\n" if command[-1] == "--dry-run" else pytest.fail("apply must not run")
    monkeypatch.setattr(run_release_workflow, "run", execute)
    monkeypatch.setattr(run_release_workflow, "verify_task_authorization",
                        lambda *_args, **_kwargs: {"_record": {}, "_changed_paths_list": []})
    assert run_release_workflow.main() == 1
    result = json.loads(result_path.read_text())
    assert result["status"] == "FAILED_PRE_SWITCH"
    assert result["preflight"] == "PASS"
    assert len(commands) == 1 and commands[0][-1] == "--dry-run"
    result = json.loads(result_path.read_text())
    assert result["status"] == "FAILED_PRE_SWITCH"
    assert result["production_changed"] is False
    assert len(commands) == 1 and commands[0][-1] == "--dry-run"


def test_task_scoped_auto_applies_only_after_revalidation_and_consumes_once(monkeypatch, tmp_path):
    result_path, migration_head = _setup_task_scoped_wrapper(monkeypatch, tmp_path, MIGRATION)
    events = []
    def verify(*_args, **_kwargs):
        events.append("revalidate")
        return {"record_sha256": "b" * 64, "source_reference": "linear:task#owner",
                "changed_paths": "app/bot/menu.py", "_record": {}, "_changed_paths_list": []}
    def consume(*_args):
        events.append("consume")
    monkeypatch.setattr(run_release_workflow, "verify_task_authorization", verify)
    monkeypatch.setattr(run_release_workflow, "consume_task_authorization", consume)
    def execute(command, _env):
        events.append("dry-run" if command[-1] == "--dry-run" else "apply")
        output = f"SAFE_TO_RELEASE=YES\nDRY_RUN_RESULT=PASS\nPROD_DB_HEAD={migration_head}\n"
        return 0, output if command[-1] == "--dry-run" else "MIGRATION_ALLOWED=YES\nRELEASE_STATUS=PASS\n"
    monkeypatch.setattr(run_release_workflow, "run", execute)
    assert run_release_workflow.main() == 0
    result = json.loads(result_path.read_text())
    assert events == ["dry-run", "revalidate", "apply", "consume"]
    assert result["status"] == "COMPLETE"
    assert result["task_authorization_state"] == "CONSUMED"
    assert result["production_changed"] is True


def test_task_authorization_consumption_reconciles_lost_github_response_by_readback(monkeypatch):
    calls = []
    def lost_response(*_args, **_kwargs):
        calls.append("write")
        raise run_release_workflow.urllib.error.URLError("connection reset after request")
    monkeypatch.setattr(run_release_workflow.urllib.request, "urlopen", lost_response)
    def readback(path, token):
        calls.append((path, token))
        return {"object": {"sha": SHA}}
    monkeypatch.setattr("validate_release_dispatch.github_get", readback)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    run_release_workflow.consume_task_authorization("maxibot-task-2026-10-06-001", SHA)
    assert calls == ["write", ("git/ref/tags/maxibot-task-auth/maxibot-task-2026-10-06-001/consumed", "test-token")]


def test_linear_owned_block_updates_in_place_and_preserves_architect_text():
    before = "Architect-owned opening.\n\n<!-- MB_RELEASE_START -->\nold\n<!-- MB_RELEASE_END -->\n\nArchitect-owned closing."
    after = upsert_block(before, "MB_RELEASE", "new release status")
    assert "Architect-owned opening." in after
    assert "Architect-owned closing." in after
    assert "old" not in after
    assert "new release status" in after


def test_linear_release_block_closes_outside_markdown_list_continuation():
    after = upsert_block("Architect-owned content.", "MB_RELEASE", "* GitHub run: [#14](https://example.test/run)")
    assert "<!-- MB_RELEASE_START -->\n\n" in after
    assert "https://example.test/run)\n\n<!-- MB_RELEASE_END -->" in after


def test_linear_journal_event_has_markdown_roundtrip_spacing():
    marker = "<!-- MAXIBOT_RELEASE_EVENT_123_ATTEMPT_1_candidate_CI_PASS -->"
    block = "### 2026-10-01T00:00:00+00:00 — candidate: **CI_PASS**\n\n* SHA: `" + SHA + "`"
    content = append_journal_event("journal intro", marker, block)
    assert f"\n\n{marker}\n\n{block}\n" in content
    assert append_journal_event(content, marker, "ignored") == content


def test_linear_write_retries_only_readback_after_eventual_visibility(monkeypatch):
    expected = "published content"
    reads = iter(["stale", expected])
    calls = []

    def fake_graphql(query, variables, api_key):
        calls.append("mutation" if "mutation UpdateDocument" in query else "read")
        if "mutation UpdateDocument" in query:
            return {"documentUpdate": {"success": True, "document": {"id": "doc", "title": "title"}}}
        return {"document": {"id": "doc", "title": "title", "content": next(reads)}}

    monkeypatch.setattr(publish_release_to_linear, "graphql_request", fake_graphql)
    monkeypatch.setattr(publish_release_to_linear.time, "sleep", lambda _: None)
    write_document("doc", "title", expected, "key")
    assert calls == ["mutation", "read", "read"]


def test_linear_journal_write_accepts_only_trailing_newline_roundtrip_variation(monkeypatch):
    calls = []

    def fake_graphql(query, variables, api_key):
        calls.append("mutation" if "mutation UpdateDocument" in query else "read")
        if "mutation UpdateDocument" in query:
            return {"documentUpdate": {"success": True, "document": {"id": "doc", "title": "title"}}}
        return {"document": {"id": "doc", "title": "title", "content": "published\n\n"}}

    monkeypatch.setattr(publish_release_to_linear, "graphql_request", fake_graphql)
    monkeypatch.setattr(publish_release_to_linear.time, "sleep", lambda _: None)
    write_document("doc", "title", "published\n", "key", allow_trailing_newline_variation=True)
    assert calls == ["mutation", "read"]


def test_linear_write_does_not_repeat_mutation_when_readback_never_matches(monkeypatch):
    calls = []

    def fake_graphql(query, variables, api_key):
        calls.append("mutation" if "mutation UpdateDocument" in query else "read")
        if "mutation UpdateDocument" in query:
            return {"documentUpdate": {"success": True, "document": {"id": "doc", "title": "title"}}}
        return {"document": {"id": "doc", "title": "title", "content": "stale"}}

    monkeypatch.setattr(publish_release_to_linear, "graphql_request", fake_graphql)
    monkeypatch.setattr(publish_release_to_linear.time, "sleep", lambda _: None)
    with pytest.raises(SyncError, match="read-back"):
        write_document("doc", "title", "expected", "key")
    assert calls.count("mutation") == 1
    assert calls.count("read") == 4


def test_linear_owned_block_fails_closed_on_duplicate_markers():
    content = "<!-- MB_RELEASE_START -->x<!-- MB_RELEASE_END -->\n<!-- MB_RELEASE_START -->y<!-- MB_RELEASE_END -->"
    with pytest.raises(SyncError, match="markers are malformed"):
        upsert_block(content, "MB_RELEASE", "next")


@pytest.mark.parametrize('approval', ['garbage','1','yes'])
def test_bootstrap_dispatch_rejects_nonboolean_approval(approval):
    with pytest.raises(ValidationError,match='bootstrap approval'):
        validate(values(APPROVE_WORKER_BOOTSTRAP=approval),FakeGitHub())


def test_bootstrap_dispatch_still_requires_exact_main_prepare_and_production_approval():
    assert validate(values(APPROVE_WORKER_BOOTSTRAP='true'),FakeGitHub())['candidate']==SHA
    with pytest.raises(ValidationError,match='exact current'):
        validate(values(APPROVE_WORKER_BOOTSTRAP='true'),FakeGitHub(main_sha='b'*40))
    with pytest.raises(ValidationError,match='owner approval'):
        validate(values(APPROVE_WORKER_BOOTSTRAP='true',RELEASE_MODE='production'),FakeGitHub())


def test_prepare_receipt_must_match_exact_publisher_logged_digest(monkeypatch):
    monkeypatch.setattr(dispatch_module, 'read_prepare_publisher_digest', lambda *_: 'sha256:' + 'f' * 64)
    with pytest.raises(ValidationError, match='publisher'):
        validate(values(RELEASE_MODE='production', APPROVE_PRODUCTION='true'), FakeGitHub())


def test_missing_prepare_publisher_proof_blocks_production(monkeypatch):
    def unavailable(*_):
        raise dispatch_module.ArtifactError('Prepare publisher proof unavailable')
    monkeypatch.setattr(dispatch_module, 'read_prepare_publisher_digest', unavailable)
    with pytest.raises(ValidationError, match='publisher'):
        validate(values(RELEASE_MODE='production', APPROVE_PRODUCTION='true'), FakeGitHub())


def test_prepare_publisher_adapter_checks_exact_attempt_and_timestamped_real_log_format(monkeypatch):
    from release_orchestrator import GitHubAPI
    expected = "sha256:" + "e" * 64
    paths = []
    def lookup(path, token):
        paths.append(path)
        assert token == "isolated-test-token"
        return {"total_count": 1, "jobs": [{"id": 456, "run_id": 123,
            "steps": [{"name": "Publish immutable Prepare receipt to GHCR and verify digest readback", "conclusion": "success"}]}]}
    monkeypatch.setattr(GitHubAPI, "get_job_log_archive", lambda self, job: "2026-10-20T17:19:54.2975796Z MAXIBOT_GHCR_PREPARE_RECEIPT=PASS DIGEST=" + expected + "\n")
    assert READ_PREPARE_PUBLISHER_DIGEST("123", "2", "isolated-test-token", lookup) == expected
    assert paths == ["actions/runs/123/attempts/2/jobs?per_page=100"]


@pytest.mark.parametrize("prior_mode", ["production", "task-scoped-auto"])
def test_publication_recovery_accepts_both_production_modes(prior_mode):
    class PriorMode(FakeGitHub):
        def __call__(self, path, token):
            result = super().__call__(path, token)
            if path == "actions/runs/777":
                result["display_title"] = f"MAXIBOT {prior_mode} {SHA} by owner"
            return result
    selected = validate(values(RELEASE_MODE="publication-recovery",
        RECONCILES_DEPLOY_RUN_ID="777", RECONCILES_DEPLOY_RUN_ATTEMPT="3"), PriorMode())
    assert selected["mode"] == "publication-recovery"


def test_consumption_retries_only_after_confirmed_missing_exact_ref(monkeypatch):
    events = []
    from validate_release_dispatch import ValidationError
    def post(request, **kwargs):
        events.append(("post", json.loads(request.data)))
        if len([x for x in events if x[0] == "post"]) == 1:
            raise run_release_workflow.urllib.error.URLError(ConnectionResetError("connection reset"))
        class Created:
            status = 201
            def __enter__(self): return self
            def __exit__(self, *_): return False
        return Created()
    def read(path, token):
        events.append(("read", path))
        if len([x for x in events if x[0] == "post"]) < 2:
            raise ValidationError("GitHub release provenance request failed (HTTP 404)")
        return {"object": {"type": "commit", "sha": SHA}}
    monkeypatch.setattr(run_release_workflow.urllib.request, "urlopen", post)
    monkeypatch.setattr("validate_release_dispatch.github_get", read)
    monkeypatch.setattr(run_release_workflow.time, "sleep", lambda _: None)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    run_release_workflow.consume_task_authorization("maxibot-task-2026-10-06-001", SHA)
    posts = [i for i,x in enumerate(events) if x[0] == "post"]
    assert len(posts) == 2
    assert any(x[0] == "read" for x in events[posts[0]+1:posts[1]])
    assert events[posts[0]][1] == events[posts[1]][1]


def test_consumption_unknown_readback_never_repeats_post(monkeypatch):
    calls = []
    def post(*args, **kwargs):
        calls.append("post")
        raise run_release_workflow.urllib.error.URLError(ConnectionResetError("connection reset"))
    def unknown(*args):
        raise ValidationError("read failed (HTTP 503)")
    monkeypatch.setattr(run_release_workflow.urllib.request, "urlopen", post)
    monkeypatch.setattr("validate_release_dispatch.github_get", unknown)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    with pytest.raises(ValueError):
        run_release_workflow.consume_task_authorization("maxibot-task-2026-10-06-001", SHA)
    assert calls == ["post"]


def test_consumption_conflicting_marker_never_retries_post(monkeypatch):
    calls = []
    def post(*args, **kwargs):
        calls.append("post")
        raise run_release_workflow.urllib.error.URLError(ConnectionResetError("reset"))
    monkeypatch.setattr(run_release_workflow.urllib.request, "urlopen", post)
    monkeypatch.setattr("validate_release_dispatch.github_get", lambda *_: {"object": {"sha": "b" * 40}})
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    with pytest.raises(ValueError, match="conflicts"):
        run_release_workflow.consume_task_authorization("maxibot-task-2026-10-06-001", SHA)
    assert calls == ["post"]



def _consumption_reconciled_fixture(monkeypatch, *, marker_sha=SHA, deployment_state="success"):
    run, result, calls = _publisher_recovery_fixture(monkeypatch,
        run_status="completed", conclusion="failure", deploy_conclusion="failure",
        mode="task-scoped-auto", deployment_state=deployment_state,
        deployment_payload={"workflow_run_id": "970", "workflow_run_attempt": "2"})
    result.update(schema="MAXIBOT_RELEASE_WORKFLOW_RESULT_V1", status="RECOVERY_REQUIRED",
        release_state="RECOVERY_REQUIRED", preflight="PASS", authorization_consumed=False,
        task_authorization_state="CONSUMPTION_PENDING", task_authorization_id="maxibot-task-2026-10-06-001",
        task_authorization_record_sha256="d" * 64,
        error="Production completed but durable TASK_SCOPED_AUTO consumption needs reconciliation",
        production_evidence={"RELEASE_STATUS": "PASS", "BACKUP": "PASS", "BACKUP_VALIDATION": "PASS",
            "BACKUP_DISK_PREFLIGHT": "PASS", "API_WORKER_IMAGE_PARITY": "PASS", "POLLING_ROLES": "PASS",
            "MIGRATION_ALLOWED": "YES", "FINAL_RUNTIME_SHA": SHA, "TARGET_SHA": SHA,
            "TARGET_MIGRATION": MIGRATION, "POSTDEPLOY_WORKER_HEALTH": json.dumps({"status": "PASS", "release": SHA, "restart_delta": 0})})
    api = publish_release_to_linear.github_json
    def reconciled_api(path):
        if "/git/ref/tags/maxibot-task-auth/" in path:
            calls.append(path)
            return {"object": {"type": "commit", "sha": marker_sha}}
        if "/actions/runs/970/attempts/2/jobs" in path:
            body = api(path)
            body["jobs"][-1]["conclusion"] = "success"
            return body
        return api(path)
    monkeypatch.setattr(publish_release_to_linear, "github_json", reconciled_api)
    return run, result, calls


def test_current_state_recovers_consumption_only_after_exact_durable_reconciliation(monkeypatch):
    run, original, calls = _consumption_reconciled_fixture(monkeypatch)
    before = json.loads(json.dumps(original))
    state = publish_release_to_linear.current_state()
    assert state["deploy"] == run and state["production_sha"] == SHA
    assert state["production_status"] == "COMPLETE"
    assert state["evidence"]["metadata_reconciled"] is True
    assert state["evidence"]["original_status"] == "RECOVERY_REQUIRED"
    assert original == before
    assert any(path.endswith("/claimed") for path in calls)
    assert any(path.endswith("/consumed") for path in calls)
    assert not any("/deployments/12344" in path for path in calls)
    rendered = publish_release_to_linear.render_current_state(state)
    assert "RECOVERY_REQUIRED" in rendered and "metadata" in rendered.lower()


@pytest.mark.parametrize("broken", ["marker", "deployment", "worker", "backup", "runtime", "error"])
def test_consumption_reconciliation_remains_fail_closed_for_incomplete_proof(monkeypatch, broken):
    _run, result, _calls = _consumption_reconciled_fixture(monkeypatch,
        marker_sha="b" * 40 if broken == "marker" else SHA,
        deployment_state="failure" if broken == "deployment" else "success")
    if broken == "worker": result["production_evidence"]["POSTDEPLOY_WORKER_HEALTH"] = "{}"
    if broken == "backup": result["production_evidence"]["BACKUP_VALIDATION"] = "FAILED"
    if broken == "runtime": result["final_runtime_sha"] = "b" * 40
    if broken == "error": result["error"] = "some other Apply failure"
    with pytest.raises(SyncError): publish_release_to_linear.current_state()


@pytest.mark.parametrize("broken", ["branch", "event", "workflow", "validation"])
def test_consumption_reconciliation_rejects_untrusted_source_run(monkeypatch, broken):
    run, _result, calls = _consumption_reconciled_fixture(monkeypatch)
    if broken == "branch":
        run["head_branch"] = "untrusted-branch"
    elif broken == "event":
        run["event"] = "pull_request"
    elif broken == "workflow":
        run["path"] = ".github/workflows/untrusted.yml"
    else:
        release_jobs = publish_release_to_linear.release_jobs
        def invalid_jobs(source_run):
            jobs = release_jobs(source_run)
            if source_run["id"] == run["id"]:
                jobs["validate"]["conclusion"] = "failure"
            return jobs
        monkeypatch.setattr(publish_release_to_linear, "release_jobs", invalid_jobs)
    with pytest.raises(SyncError):
        publish_release_to_linear.current_state()
    assert not any("/deployments/12345" in path for path in calls)


class _ControllerHistoryAPI:
    def __init__(self, *, old_evidence=None, old_attempt_count=1, old_evidence_by_attempt=None):
        self.token = "test-token"
        self.calls = []
        self.refs = {}
        self.deploy_runs = []
        self.logs = {}
        self.old_evidence_by_attempt = dict(old_evidence_by_attempt or {})
        if old_evidence is not None:
            self.old_evidence_by_attempt.setdefault("1", old_evidence)
        self.deploy_dispatches = []
        self.refs["refs/tags/maxibot-task-auth/maxibot-old-auth-20261023/claimed"] = "b" * 40
        self.refs["refs/tags/maxibot-task-auth/maxibot-old-auth-20261023/dispatches/prepare-456-1"] = "b" * 40
        self.deploy_runs = [{"id": 991, "run_attempt": old_attempt_count, "head_sha": "b" * 40,
            "path": ".github/workflows/maxibot-deploy.yml", "event": "workflow_dispatch",
            "status": "completed", "conclusion": "failure"}]
        self.logs["124"] = ("2026-10-24T09:00:00Z MAXIBOT_GHCR_PREPARE_RECEIPT=PASS DIGEST=sha256:"
                            + "e" * 64 + "\n")
        for attempt in self.old_evidence_by_attempt:
            self.logs[str(991 + int(attempt))] = (
                "2026-10-24T09:00:00Z MAXIBOT_GHCR_DEPLOY_EVIDENCE=PASS DIGEST=sha256:"
                + "f" * 64 + "\n")

    def get(self, path):
        self.calls.append(("GET", path))
        if path == "git/ref/heads/main":
            return {"object": {"sha": "a" * 40}}
        if path == "actions/runs/123":
            return {"id": 123, "run_attempt": 2, "head_sha": "a" * 40, "head_branch": "main",
                "status": "completed", "conclusion": "success", "event": "push",
                "path": ".github/workflows/maxibot-prepare.yml"}
        match = re.fullmatch(r"actions/runs/(\d+)/attempts/(\d+)/jobs\?per_page=100", path)
        if match:
            run_id, attempt = match.groups()
            if (run_id, attempt) == ("123", "2"):
                return {"total_count": 1, "jobs": [{"id": 124, "run_id": 123, "run_attempt": 2,
                    "steps": [{"name": orchestrator.PREPARE_PUBLISH_STEP, "conclusion": "success"}]}]}
            if (run_id, attempt) == ("991", attempt) and attempt in self.old_evidence_by_attempt:
                return {"total_count": 1, "jobs": [{"id": 991 + int(attempt), "run_id": 991,
                    "run_attempt": int(attempt),
                    "steps": [{"name": orchestrator.EVIDENCE_PUBLISH_STEP, "conclusion": "success"}]}]}
            if run_id == "991" and attempt.isdigit():
                return {"total_count": 0, "jobs": []}
            return {"total_count": 0, "jobs": []}
        attempt_match = re.fullmatch(r"actions/runs/991/attempts/([1-9][0-9]*)", path)
        if attempt_match:
            attempt = attempt_match.group(1)
            return {"id": 991, "run_attempt": int(attempt), "head_sha": "b" * 40,
                "head_branch": "main", "path": ".github/workflows/maxibot-deploy.yml",
                "event": "workflow_dispatch", "status": "completed", "conclusion": "failure"}
        if path.startswith("actions/workflows/maxibot-deploy.yml/runs?"):
            return {"total_count": len(self.deploy_runs), "workflow_runs": self.deploy_runs}
        if path.startswith("git/matching-refs/tags/"):
            prefix = "refs/tags/" + path.removeprefix("git/matching-refs/tags/")
            return [{"ref": ref, "object": {"type": "commit", "sha": sha}}
                    for ref, sha in self.refs.items() if ref.startswith(prefix)]
        if path.startswith("deployments?"):
            return []
        raise orchestrator.APIError(404, "", path)

    def get_job_log_archive(self, job_id):
        return self.logs[str(job_id)]

    def post(self, path, body):
        self.calls.append(("POST", path, body))
        if path == "git/refs":
            self.refs[body["ref"]] = body["sha"]
            return {"ref": body["ref"]}
        if path.endswith("/dispatches"):
            self.deploy_dispatches.append(body)
            return {}
        raise AssertionError(path)


def _controller_task_record(authorization_id):
    record = {"schema_version": 3, "project": "maxibot", "environment": "production",
        "authorization_mode": "TASK_SCOPED_AUTO", "authorization_id": authorization_id,
        "source_instruction_identifier": "OWNER_TASK_MAXIBOT_ME50_P0_CONTROLLER",
        "owner_task_reference": "Direct OWNER authorization for the MAXIBOT P0 release guard.",
        "owner_instruction_scope": "FULL_PRODUCTION_CYCLE",
        "candidate_selection_rule": "EXACT_CURRENT_MAIN_WITHIN_AUTHORIZED_SCOPE",
        "production_baseline_sha": "d" * 40, "task": "Bounded controller release safety task",
        "allowed_paths": ["scripts/release_orchestrator.py"],
        "allowed_operations": ["integrate", "coalesce", "prepare", "preflight", "deploy", "verify", "rollback", "publish_evidence"],
        "preauthorized_commits": [], "preauthorized_paths": [],
        "acceptance_criteria": ["Unresolved same-baseline Deploy attempts block dispatch."],
        "exclusions": ["database", "migrations", "security", "secrets", "payments", "ads", "paid_traffic"],
        "risk_contracts": {}, "state": "ACTIVE", "revoked": False, "consumed": False}
    record["owner_task_commitment_sha256"] = owner_task_commitment_sha256(record)
    return record


def _call_controller_with_prior_auth(tmp_path, api, monkeypatch):
    current_id = "maxibot-new-auth-20261024"
    old_id = "maxibot-old-auth-20261023"
    records = tmp_path / ".release-authorizations"
    records.mkdir()
    for auth_id in (current_id, old_id):
        (records / f"{auth_id}.json").write_text(json.dumps(_controller_task_record(auth_id)))
    receipt = {"schema": "MAXIBOT_PREPARE_RECEIPT_V2", "candidate_sha": "a" * 40,
        "github_run_id": "123", "github_run_attempt": "2", "status": "READY",
        "tests": "success", "frozen_path_guard": "success", "linux_amd64_image": "success",
        "ghcr_image_publish": "success", "private_docker_rehearsal": "PASS",
        "image": {"repository": "ghcr.io/akhripunov/maxibot-vps-api",
            "tag": orchestrator.artifact_tag("prepare", "a" * 40, "123", "2"),
            "digest": "sha256:" + "c" * 64, "config_digest": "sha256:" + "d" * 64,
            "platform": "linux/amd64", "labels": {
                "org.opencontainers.image.revision": "a" * 40,
                "org.opencontainers.image.source-sha": "a" * 40,
                "org.maxibot.release.sha": "a" * 40}}}
    def resolve(_repository, tag, *_args):
        if tag.startswith("prepare-"):
            return "sha256:" + "e" * 64, json.dumps(receipt).encode()
        prior_attempt = tag.rsplit("-", 1)[-1]
        prior_evidence = api.old_evidence_by_attempt.get(prior_attempt)
        if prior_evidence is None:
            raise orchestrator.ArtifactError("original evidence unavailable")
        return "sha256:" + "f" * 64, json.dumps(prior_evidence).encode()
    monkeypatch.setattr(orchestrator, "_find_task_authorization",
        lambda *_args: {"authorization_id": current_id, "ambiguous": "false"})
    monkeypatch.setattr(orchestrator, "resolve_oci_artifact", resolve)
    monkeypatch.setattr(orchestrator, "_registry_fetch", lambda *_args, **_kwargs: None)
    event = {"repository": {"full_name": "akhripunov/maxibot"}, "workflow_run": {
        "id": 123, "run_attempt": 2, "head_sha": "a" * 40, "head_branch": "main",
        "status": "completed", "conclusion": "success", "event": "push",
        "path": ".github/workflows/maxibot-prepare.yml"}}
    return orchestrator.dispatch_prepare(event, api, repo_root=tmp_path, migration="0098_test")


def test_new_authorization_cannot_bypass_unresolved_other_authorization_deploy(tmp_path, monkeypatch):
    api = _ControllerHistoryAPI()  # Original FAILED_PRE_SWITCH evidence is not confirmed.
    with pytest.raises(orchestrator.OrchestrationError, match="reconciliation|required|unknown"):
        _call_controller_with_prior_auth(tmp_path, api, monkeypatch)
    assert not api.deploy_dispatches


def test_new_authorization_may_continue_after_exact_prior_attempt_reconciliation(tmp_path, monkeypatch):
    prior = {"schema": "MAXIBOT_RELEASE_WORKFLOW_RESULT_V1", "candidate_sha": "b" * 40,
        "github_run_id": "991", "github_run_attempt": "1", "prepare_run_id": "456",
        "prepare_run_attempt": "1", "task_authorization_id": "maxibot-old-auth-20261023",
        "mode": "task-scoped-auto", "target_migration": "0098_test",
        "status": "FAILED_PRE_SWITCH", "release_state": "FAILED_PRE_SWITCH",
        "production_changed": False, "authorization_consumed": False,
        "production_baseline_sha": "d" * 40,
        "github_deployment_id": None, "github_deployment_state": None}
    api = _ControllerHistoryAPI(old_evidence=prior)

    result = _call_controller_with_prior_auth(tmp_path, api, monkeypatch)

    assert result["state"] == "TASK_SCOPED_AUTO_DISPATCHED"
    assert len(api.deploy_dispatches) == 1
    assert any(path.startswith("deployments?sha=" + "b" * 40) for _, path, *_ in api.calls)


def test_unreconciled_second_attempt_blocks_even_when_first_attempt_is_proven_safe(tmp_path, monkeypatch):
    first_attempt = {"schema": "MAXIBOT_RELEASE_WORKFLOW_RESULT_V1", "candidate_sha": "b" * 40,
        "github_run_id": "991", "github_run_attempt": "1", "prepare_run_id": "456",
        "prepare_run_attempt": "1", "task_authorization_id": "maxibot-old-auth-20261023",
        "mode": "task-scoped-auto", "target_migration": "0098_test",
        "status": "FAILED_PRE_SWITCH", "release_state": "FAILED_PRE_SWITCH",
        "production_changed": False, "authorization_consumed": False,
        "production_baseline_sha": "d" * 40,
        "github_deployment_id": None, "github_deployment_state": None}
    api = _ControllerHistoryAPI(old_attempt_count=2, old_evidence_by_attempt={"1": first_attempt})

    with pytest.raises(orchestrator.OrchestrationError, match="reconciliation|required|unknown"):
        _call_controller_with_prior_auth(tmp_path, api, monkeypatch)

    assert any(path == "actions/runs/991/attempts/2" for _, path, *_ in api.calls)
    assert not api.deploy_dispatches


def test_unmarked_exact_sha_deploy_cannot_bypass_controller_history(tmp_path, monkeypatch):
    api = _ControllerHistoryAPI()
    api.refs.clear()  # Exact-SHA releases have no TASK_SCOPED_AUTO marker.
    with pytest.raises(orchestrator.OrchestrationError, match="reconciliation|required|unknown"):
        _call_controller_with_prior_auth(tmp_path, api, monkeypatch)
    assert not api.deploy_dispatches


class _EnvironmentHistoryAPI:
    token = 'test-token'

    def __init__(self, runs, results=None, deployments=None):
        self.runs = runs
        self.results = results or {}
        self.deployments = deployments or []

    def get(self, path):
        if path.startswith('actions/workflows/'):
            return {'total_count': len(self.runs), 'workflow_runs': self.runs}
        if path.startswith('deployments?'):
            return self.deployments
        raise AssertionError(path)


def _history_run(run_id=991, **updates):
    return {'id': run_id, 'run_attempt': 1, 'head_sha': 'b' * 40,
            'path': '.github/workflows/maxibot-deploy.yml', 'event': 'workflow_dispatch',
            'status': 'completed', 'conclusion': 'failure', 'updated_at': '2026-10-24T10:00:00Z', **updates}


def _history_failure(**updates):
    return {'mode': 'production', 'status': 'FAILED_PRE_SWITCH',
            'release_state': 'FAILED_PRE_SWITCH', 'production_changed': False,
            'authorization_consumed': False, '_verified_deployment_api_match': 'NOT_CREATED', **updates}


@pytest.mark.parametrize('updates', [
    {'status': 'RECOVERY_REQUIRED', 'release_state': 'RECOVERY_REQUIRED', 'production_changed': 'POSSIBLE'},
    {'status': 'COMPLETE', 'release_state': 'COMPLETE', 'production_changed': True,
     'final_runtime_sha': 'b' * 40, '_verified_deployment_api_match': 'PUBLICATION_FAILED'},
    {'_verified_deployment_api_match': 'in_progress'},
    {'_verified_deployment_api_match': 'success'},
])
def test_environment_guard_blocks_uncertain_apply_and_metadata_only_recovery(tmp_path, monkeypatch, updates):
    api = _EnvironmentHistoryAPI([_history_run()])
    monkeypatch.setattr(orchestrator, '_verified_deploy_evidence', lambda *_a: ('digest', _history_failure(**updates)))
    with pytest.raises(orchestrator.OrchestrationError, match='reconciliation'):
        orchestrator.assert_production_history(api, tmp_path, 'd' * 40)


def test_environment_guard_accepts_proven_safe_exact_sha_failure(tmp_path, monkeypatch):
    api = _EnvironmentHistoryAPI([_history_run()])
    monkeypatch.setattr(orchestrator, '_verified_deploy_evidence', lambda *_a: ('digest', _history_failure()))
    orchestrator.assert_production_history(api, tmp_path, 'd' * 40)


def test_environment_guard_blocks_orphan_deployment_even_when_no_run_or_task_ref(tmp_path):
    api = _EnvironmentHistoryAPI([], deployments=[{'id': 47, 'environment': 'maxibot-production',
        'task': 'maxibot-production-deploy', 'payload': {'workflow_run_id': '991', 'workflow_run_attempt': '1'}}])
    with pytest.raises(orchestrator.OrchestrationError, match='Deployment lacks'):
        orchestrator.assert_production_history(api, tmp_path, 'd' * 40)


@pytest.mark.parametrize('inventory', [
    {'total_count': 2, 'workflow_runs': [_history_run()]},
    {'total_count': 2, 'workflow_runs': [_history_run(), _history_run()]},
    {'total_count': True, 'workflow_runs': [_history_run()]},
])
def test_environment_guard_rejects_incomplete_or_duplicate_run_inventory(tmp_path, inventory):
    class BrokenAPI:
        def get(self, _path): return inventory
    with pytest.raises(orchestrator.OrchestrationError):
        orchestrator.assert_production_history(BrokenAPI(), tmp_path, 'd' * 40)


def test_safe_prior_task_with_exact_failed_deployment_preserves_retry(tmp_path, monkeypatch):
    prior = {'schema': 'MAXIBOT_RELEASE_WORKFLOW_RESULT_V1', 'candidate_sha': 'b' * 40,
        'github_run_id': '991', 'github_run_attempt': '1', 'prepare_run_id': '456',
        'prepare_run_attempt': '1', 'task_authorization_id': 'maxibot-old-auth-20261023',
        'mode': 'task-scoped-auto', 'target_migration': '0098_test',
        'prepare_receipt_digest': 'sha256:' + 'c' * 64,
        'status': 'FAILED_PRE_SWITCH', 'release_state': 'FAILED_PRE_SWITCH',
        'production_changed': False, 'authorization_consumed': False,
        'production_baseline_sha': 'd' * 40, 'github_deployment_id': 47,
        'github_deployment_state': 'failure'}
    deployment = {'id': 47, 'sha': 'b' * 40, 'ref': 'b' * 40,
        'task': 'maxibot-production-deploy', 'environment': 'maxibot-production',
        'payload': {'candidate_sha': 'b' * 40, 'workflow_run_id': '991',
            'workflow_run_attempt': '1', 'mode': 'task-scoped-auto',
            'target_migration': '0098_test', 'prepare_receipt_digest': prior['prepare_receipt_digest']}}
    class PriorDeploymentAPI(_ControllerHistoryAPI):
        def get(self, path):
            if path.startswith('deployments?'):
                return [deployment]
            if path == 'deployments/47/statuses?per_page=100':
                return [{'state': 'failure'}]
            return super().get(path)
    api = PriorDeploymentAPI(old_evidence=prior)
    assert _call_controller_with_prior_auth(tmp_path, api, monkeypatch)['state'] == 'TASK_SCOPED_AUTO_DISPATCHED'
    assert len(api.deploy_dispatches) == 1


def test_environment_guard_accepts_completed_release_at_verified_baseline(tmp_path, monkeypatch):
    import subprocess
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '--allow-empty', '-qm', 'installed baseline'], cwd=tmp_path, check=True)
    baseline = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=tmp_path, text=True).strip()
    api = _EnvironmentHistoryAPI([_history_run(head_sha=baseline, conclusion='success')])
    evidence = _history_failure(mode='production', status='COMPLETE', release_state='COMPLETE',
        production_changed=True, authorization_consumed=True, final_runtime_sha=baseline,
        _verified_deployment_api_match='success')
    monkeypatch.setattr(orchestrator, '_verified_deploy_evidence', lambda *_a: ('digest', evidence))
    orchestrator.assert_production_history(api, tmp_path, baseline)


def test_metadata_recovery_resolves_only_its_exact_original_attempt(tmp_path, monkeypatch):
    original = _history_run(991)
    recovery = _history_run(992, conclusion='success')
    api = _EnvironmentHistoryAPI([original, recovery])  # Original may be returned first.
    proof = _history_failure(mode='publication-recovery', status='COMPLETE', release_state='COMPLETE',
        production_changed=False, final_runtime_sha='b' * 40, recovery='READ_ONLY_RECONCILIATION_PASS',
        reconciles_deploy_run_id='991', reconciles_deploy_run_attempt='1',
        _verified_deployment_api_match='success', _reconciles_attempt=('991', '1'))
    monkeypatch.setattr(orchestrator, '_verified_deploy_evidence',
        lambda _api, run: ('digest', proof) if run['id'] == 992 else None)
    # This test's baseline represents the already-installed candidate, so no new Apply is needed.
    import subprocess
    monkeypatch.setattr(orchestrator.subprocess, 'run', lambda *_a, **_kw: subprocess.CompletedProcess([], 0))
    orchestrator.assert_production_history(api, tmp_path, 'b' * 40)

    api.runs.append(_history_run(993))  # Recovery of 991 must not mask another unknown operation.
    with pytest.raises(orchestrator.OrchestrationError, match='reconciliation'):
        orchestrator.assert_production_history(api, tmp_path, 'b' * 40)


@pytest.mark.parametrize('original_mode', ['production', 'task-scoped-auto'])
def test_verified_metadata_recovery_binds_original_evidence_and_same_deployment(tmp_path, monkeypatch, original_mode):
    original = _history_run(991)
    recovery_run = _history_run(992, conclusion='success')
    original_evidence = {'schema': 'MAXIBOT_RELEASE_WORKFLOW_RESULT_V1', 'candidate_sha': 'b' * 40,
        'mode': original_mode, 'target_migration': '0098_test', 'production_changed': True,
        'github_deployment_id': 47, 'prepare_receipt_digest': 'sha256:' + 'c' * 64,
        'task_authorization_id': 'maxibot-old-auth-20261023'}
    recovery = {**original_evidence, 'mode': 'publication-recovery', 'production_changed': False,
        'github_run_id': '992', 'github_run_attempt': '1',
        'status': 'COMPLETE', 'release_state': 'COMPLETE', 'final_runtime_sha': 'b' * 40,
        'github_deployment_state': 'success', 'recovery': 'READ_ONLY_RECONCILIATION_PASS',
        'reconciles_deploy_run_id': '991', 'reconciles_deploy_run_attempt': '1'}
    deployment = {'id': 47, 'sha': 'b' * 40, 'ref': 'b' * 40, 'environment': 'maxibot-production',
        'task': 'maxibot-production-deploy', 'payload': {'candidate_sha': 'b' * 40,
            'workflow_run_id': '991', 'workflow_run_attempt': '1', 'mode': original_mode,
            'target_migration': '0098_test', 'prepare_receipt_digest': 'sha256:' + 'c' * 64}}
    class RecoveryAPI:
        token = 'test-token'
        def get(self, path):
            if path == 'actions/runs/991/attempts/1': return original
            if path.startswith('deployments?'): return [deployment]
            if path == 'deployments/47/statuses?per_page=100': return [{'state': 'success'}]
            if path.endswith('/consumed'): return {'object': {'type': 'commit', 'sha': 'b' * 40}}
            if path.endswith('/revoked'): raise orchestrator.APIError(404)
            raise AssertionError(path)
    monkeypatch.setattr(orchestrator, '_deploy_evidence_payload',
        lambda _api, run: ('digest', recovery if run['id'] == 992 else original_evidence))
    assert orchestrator._verified_deploy_evidence(RecoveryAPI(), recovery_run)[1]['_reconciles_attempt'] == ('991', '1')
    original_evidence['github_deployment_id'] = 48
    assert orchestrator._verified_deploy_evidence(RecoveryAPI(), recovery_run) is None


def test_verified_checkpoint_retires_only_completed_older_history(tmp_path, monkeypatch):
    import subprocess
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '--allow-empty', '-qm', 'installed baseline'], cwd=tmp_path, check=True)
    baseline = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=tmp_path, text=True).strip()
    anchor = _history_run(992, head_sha=baseline, conclusion='success')
    old = _history_run(991, updated_at='2026-10-24T09:00:00Z')
    proof = _history_failure(mode='production', status='COMPLETE', release_state='COMPLETE',
        production_changed=True, final_runtime_sha=baseline, _verified_deployment_api_match='success')
    monkeypatch.setattr(orchestrator, '_verified_deploy_evidence',
        lambda _api, run: ('digest', proof) if run['id'] == 992 else None)
    api = _EnvironmentHistoryAPI([old, anchor])
    orchestrator.assert_production_history(api, tmp_path, baseline)
    old['updated_at'] = '2026-10-24T10:01:00Z'  # A later retry is not retired.
    with pytest.raises(orchestrator.OrchestrationError, match='reconciliation'):
        orchestrator.assert_production_history(api, tmp_path, baseline)


def test_me50_cancelled_before_runner_has_no_production_execution(tmp_path, monkeypatch):
    run = {'id':40077453537,'run_attempt':1,'head_sha':'d38e3613f50a5659c463572064272e4438763ea4',
        'head_branch':'main','path':'.github/workflows/maxibot-deploy.yml','event':'workflow_dispatch',
        'status':'completed','conclusion':'cancelled'}
    prefix = '2026-10-24T08:17:31Z '
    env={'CANDIDATE_SHA':run['head_sha'],'RELEASE_MODE':'task-scoped-auto',
        'PREPARE_RUN_ID':'40076645136','PREPARE_RUN_ATTEMPT':'1',
        'TASK_AUTHORIZATION_ID':'maxibot-me50-v5-release-infra-20261024',
        'APPROVE_PRODUCTION':'false','APPROVE_WORKER_BOOTSTRAP':'false'}
    log=prefix+'##[group]Run python3 scripts/validate_release_dispatch.py\n'+prefix+'env:\n'
    log+=''.join(prefix+'  '+k+': '+v+'\n' for k,v in env.items())+prefix+'##[endgroup]\n'
    names=[n for n,_ in orchestrator.DEPLOY_VALIDATE_JOB_STEPS+orchestrator.DEPLOY_VALIDATE_POST_STEPS]
    jobs=[{'id':11,'name':'validate','status':'completed','conclusion':'success',
           'steps':[{'name':n,'status':'completed','conclusion':'success'} for n in names]},
          {'id':12,'name':'deploy','status':'completed','conclusion':'cancelled','runner_id':0,'runner_name':'','steps':[]},
          {'id':13,'name':'publish','status':'completed','conclusion':'success','steps':[]}]
    for j in jobs:j.update(run_id=run['id'],run_attempt=1,head_sha=run['head_sha'])
    class API:
        token='test'
        def get(self,p):
            if p.endswith('/jobs?per_page=100'):return {'total_count':3,'jobs':jobs}
            if p.endswith('/claimed'):return {'object':{'type':'commit','sha':run['head_sha']}}
            if p.endswith(('/consumed','/revoked')):raise orchestrator.APIError(404)
            if p.startswith('deployments?'):return []
            raise AssertionError(p)
        def get_job_log_archive(self,_id):return log
    proof=getattr(orchestrator,'_prove_me50_nonexecuting_attempt',lambda *_a:False)
    assert proof(API(),run,ROOT)
    jobs[1]['runner_id']=77
    assert not proof(API(),run,ROOT)


@pytest.mark.parametrize('broken', [None,'timestamp','runner','log','digest','deployment','extra_job','extra_attempt'])
def test_me50_publisher_rerun_can_only_alias_original_attempt_two(monkeypatch, broken):
    import copy
    sha='d38e3613f50a5659c463572064272e4438763ea4'
    run={'id':40077453537,'run_attempt':3,'head_sha':sha,'head_branch':'main',
         'path':'.github/workflows/maxibot-deploy.yml','event':'workflow_dispatch',
         'status':'completed','conclusion':'failure','run_started_at':'2026-10-24T08:50:37Z'}
    original={**run,'run_attempt':2,'updated_at':'2026-10-24T08:44:44Z'}
    env={'CANDIDATE_SHA':sha,'RELEASE_MODE':'task-scoped-auto','PREPARE_RUN_ID':'40076645136',
         'PREPARE_RUN_ATTEMPT':'1','TASK_AUTHORIZATION_ID':'maxibot-me50-v5-release-infra-20261024',
         'APPROVE_PRODUCTION':'false','APPROVE_WORKER_BOOTSTRAP':'false'}
    monkeypatch.setattr(orchestrator,'_validation_log_inputs',lambda _log:env)
    jobs=[]
    for n,name in enumerate(('validate','deploy','publish'),1):
        jobs.append({'id':n,'name':name,'run_id':run['id'],'run_attempt':2,'head_sha':sha,
          'head_branch':'main','status':'completed','conclusion':'failure' if name!='validate' else 'success',
          'runner_id':1,'runner_name':'mac','runner_group_id':1,'runner_group_name':'Default',
          'labels':['self-hosted'],'started_at':'2026-10-24T08:31:01Z','completed_at':'2026-10-24T08:44:12Z',
          'steps':[{'name':'execute','status':'completed','conclusion':'success',
                    'started_at':'2026-10-24T08:32:00Z','completed_at':'2026-10-24T08:41:34Z'}]})
    snapshots=copy.deepcopy(jobs)
    for j in snapshots:j.update(id=j['id']+10,run_attempt=3)
    digest='sha256:c1cb813bc2c0ace2931e226935a2b830119c7c6ac5c5a48ce3a2dfbc1584a121'
    result={'task_authorization_id':env['TASK_AUTHORIZATION_ID'],'mode':'task-scoped-auto',
        'prepare_run_id':'40076645136','prepare_run_attempt':'1','status':'FAILED_PRE_SWITCH',
        'release_state':'FAILED_PRE_SWITCH','production_changed':False,'authorization_consumed':False,
        'production_baseline_sha':'ae6f5ea623e12fde4d0e2271023f7822a3f9822f',
        '_verified_deployment_api_match':'NOT_CREATED'}
    monkeypatch.setattr(orchestrator,'_verified_deploy_evidence',lambda *_a:('sha256:'+'0'*64 if broken=='digest' else digest,result))
    if broken=='timestamp':snapshots[1]['steps'][0]['completed_at']='2026-10-24T08:51:00Z'
    if broken=='runner':snapshots[1]['runner_id']=0
    if broken=='extra_job':snapshots.append({**snapshots[1],'id':99,'name':'other'})
    if broken=='extra_attempt':run['run_attempt']=4
    class API:
        token='test'
        def get(self,p):
            if p.endswith('/attempts/2'):return original
            if p.endswith('/2/jobs?per_page=100'):return {'total_count':3,'jobs':jobs}
            if p.endswith('/3/jobs?per_page=100'):return {'total_count':len(snapshots),'jobs':snapshots}
            if p.endswith('/claimed'):return {'object':{'type':'commit','sha':sha}}
            if p.endswith(('/consumed','/revoked')):raise orchestrator.APIError(404)
            if p.startswith('deployments?'):
                return [{'id':47,'environment':'maxibot-production','payload':{'workflow_run_id':str(run['id']),'workflow_run_attempt':'3'}}] if broken=='deployment' else []
            raise AssertionError(p)
        def get_job_log_archive(self,job_id):
            return 'GITHUB_RUN_ATTEMPT: 2\n'+('changed' if broken=='log' and str(job_id)=='12' else '')
    assert orchestrator._prove_me50_nonexecuting_attempt(API(),run,ROOT) is (broken is None)
