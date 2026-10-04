from __future__ import annotations

import sys
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_release_dispatch import ValidationError, github_get, validate  # noqa: E402
from publish_release_to_linear import SyncError, append_journal_event, upsert_block, write_document  # noqa: E402
import publish_release_to_linear  # noqa: E402
import run_release_workflow  # noqa: E402


SHA = "a" * 40
MIGRATION = "0098_durable_autopilot_execution_claim_v1"


class FakeGitHub:
    def __init__(self, main_sha=SHA, runs=None):
        self.main_sha = main_sha
        self.runs = runs if runs is not None else [
            {"id": 14, "run_number": 4, "run_attempt": 1, "head_sha": SHA,
             "status": "completed", "conclusion": "success", "event": "push",
             "head_branch": "main", "html_url": "https://github.com/akhripunov/maxibot/actions/runs/14"}
        ]
        self.paths = []
        self.release_enabled = True

    def __call__(self, path, token):
        self.paths.append(path)
        if path == "git/ref/heads/main":
            return {"object": {"sha": self.main_sha}}
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


def test_dispatch_selects_exact_current_main_and_latest_successful_prepare():
    github = FakeGitHub()
    selected = validate(values(), github)
    assert selected["candidate"] == SHA
    assert selected["target_migration"] == MIGRATION
    assert selected["prepare_run_id"] == "14"
    assert selected["prepare_run_attempt"] == "1"
    assert any(f"head_sha={SHA}" in path for path in github.paths)


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
    github = FakeGitHub()
    github.release_enabled = False
    with pytest.raises(ValidationError, match="immutable Prepare Release"):
        validate(values(), github)


def test_dispatch_rejects_release_tag_that_points_to_another_commit():
    class ForeignTag(FakeGitHub):
        def __call__(self, path, token):
            if path.startswith("git/ref/tags/"):
                return {"object": {"type": "commit", "sha": "b" * 40}}
            return super().__call__(path, token)

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


def test_deploy_workflow_downloads_immutable_prepare_release_and_uses_portable_pinned_python():
    workflow = (ROOT / ".github/workflows/maxibot-deploy.yml").read_text()
    assert "scripts/prepared_artifact_cache.py" in workflow
    assert "PREPARE_RELEASE_ASSET_ID: ${{ needs.validate.outputs.prepare_release_asset_id }}" in workflow
    assert "PREPARE_RELEASE_TAG: ${{ needs.validate.outputs.prepare_release_tag }}" in workflow
    assert "actions/download-artifact@v4" not in workflow
    assert "python scripts/" not in workflow
    assert '"$MAXIBOT_PYTHON" scripts/validate_release_dispatch.py' in workflow
    assert "scripts/verify_prepared_image.py" in workflow
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
    assert "Publish immutable Deploy evidence Release" in workflow
    assert "scripts/rehearse_prepared_image.py" not in workflow
    prepare = (ROOT / ".github/workflows/maxibot-prepare.yml").read_text()
    assert "scripts/rehearse_private_docker.py" in prepare
    assert "docker/setup-docker-action@2bf61fb9464cc67f0cbdeabed6aa0380accd1c70" in prepare
    assert '"containerd-snapshotter":false' in prepare
    assert "private_docker_rehearsal" in prepare
    assert '"native_image_id": native_image_id' in prepare
    assert 'ARCHIVE_CONFIG_ID=' in prepare
    assert "MAXIBOT_RELEASE_ADMIN_TOKEN" in prepare
    assert "MAXIBOT_RELEASE_ADMIN_TOKEN" in workflow
    assert "Publish complete Prepare artifacts" not in prepare
    assert "Publish branch rehearsal receipt" not in prepare
    assert '"release", "create"' in (ROOT / "scripts/release_assets.py").read_text()
    assert "application/octet-stream" in (ROOT / "scripts/prepared_artifact_cache.py").read_text()
    assert "MAXIBOT_RELEASE_ADMIN_TOKEN" in (ROOT / "docs/RELEASE_AUTOMATION.md").read_text()


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
    assert "Publish immutable Prepare Release bundle" in workflow
    assert workflow.index("Publish immutable Prepare Release bundle") < workflow.index("Publish final Prepare result to MAXIBOT Linear")
    assert "PREPARE_RESULT: ${{ needs.prepare.result }}" in workflow
    assert publish_release_to_linear.candidate_status(prepare_result) == expected


def test_preflight_only_does_not_record_production_event():
    workflow = (ROOT / ".github/workflows/maxibot-deploy.yml").read_text()
    publisher = workflow.split("  publish:", 1)[1]
    assert "if: always() && github.ref == 'refs/heads/main'" in publisher
    assert "needs.validate.outputs.candidate || inputs.candidate || github.sha" in publisher
    assert "ref: ${{ github.sha }}" in publisher
    step = workflow.split("- name: Publish production result to MAXIBOT Linear", 1)[1]
    assert "inputs.mode == 'production' || inputs.mode == 'publication-recovery'" in step
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
    monkeypatch.setattr(publish_release_to_linear, "github_json", api)
    monkeypatch.setattr(publish_release_to_linear.urllib.request, "build_opener", lambda *_args: FakeOpener())
    assert publish_release_to_linear.read_result_artifact(run) == result
    assert any("/actions/runs/321/artifacts" in path for path in calls)


def test_publisher_prefers_immutable_release_without_legacy_fallback(monkeypatch, tmp_path):
    run = {"id": 322, "run_attempt": 2, "head_sha": SHA}
    calls = []

    def api(path):
        calls.append(path)
        return {"tag_name": "release"}

    def read(sha, run_id, attempt, destination, github_get, token):
        assert (sha, run_id, attempt) == (SHA, "322", "2")
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "result.json").write_text(json.dumps({"canonical": True}))
        return {}

    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(publish_release_to_linear, "github_json", api)
    monkeypatch.setattr(publish_release_to_linear, "read_evidence", read)
    result = publish_release_to_linear.read_result_artifact(run)
    assert result == {"canonical": True}
    assert calls and all("actions/runs" not in call for call in calls)


def test_publisher_does_not_fallback_when_existing_release_evidence_is_invalid(monkeypatch, tmp_path):
    run = {"id": 323, "run_attempt": 1, "head_sha": SHA}
    calls = []
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(publish_release_to_linear, "github_json", lambda path: calls.append(path) or {"tag_name": "exists"})
    monkeypatch.setattr(
        publish_release_to_linear, "read_evidence",
        lambda *_args: (_ for _ in ()).throw(publish_release_to_linear.ReleaseAssetError("bad digest")),
    )
    with pytest.raises(SyncError, match="evidence failed validation"):
        publish_release_to_linear.read_result_artifact(run)
    assert calls and all("actions/runs" not in call for call in calls)


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
    ] if "prepare" in workflow else [{"id":3,"head_sha":"b"*40,"conclusion":"success","name":"MAXIBOT production candidate"}])
    monkeypatch.setattr(publish_release_to_linear, "read_result_artifact", lambda run: {
        "mode":"production", "status":"COMPLETE", "github_run_id":"3", "final_runtime_sha":"b"*40, "candidate_sha":"b"*40,
        "final_migration":"0098"})
    monkeypatch.setattr(publish_release_to_linear, "github_json", lambda path: (
        {"sha":"a"*40,"commit":{"committer":{"date":"t"}}} if "commits/main" in path else {"workflow_runs":[]}))
    state = publish_release_to_linear.current_state()
    assert state["candidate"] == "NOT_PREPARED_FOR_CURRENT_MAIN"
    assert state["prepare"]["id"] == 1
    assert state["current_prepare"]["id"] == 2


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


def test_prepare_write_permission_is_main_only():
    workflow = (ROOT / ".github/workflows/maxibot-prepare.yml").read_text()
    assert "permissions:\n  contents: read\n  actions: read" in workflow
    assert "    if: github.ref == 'refs/heads/main'" in workflow
    prepare_permissions = workflow.split("  prepare:\n", 1)[1].split("    steps:\n", 1)[0]
    assert "permissions:\n      contents: write\n      actions: read" in prepare_permissions
    assert "Publish branch rehearsal receipt" not in workflow


def test_release_apply_loads_verified_prepared_image_without_rebuilding():
    release = (ROOT / "scripts/release_prod.sh").read_text()
    assert 'docker load --input $REMOTE_IMAGE_TMP' in release
    assert 'sha256sum $REMOTE_IMAGE_TMP' in release
    assert 'prepared_image_config_digest' in release
    assert 'image_archive_sha256' in release
    assert 'VPS_LOADED_IMAGE_CONFIG_DIGEST=$REMOTE_LOADED_IMAGE_ID' in release
    assert 'test "$(docker image inspect maxibot-vps-api:$TARGET_SHA --format \'{{.Id}}\')" = $RELEASE_IMAGE_ID' not in release
    assert 'docker build --platform linux/amd64' not in release
    assert 'RELEASE_IMAGE_ID' in release
    assert 'A verified Prepare image archive is required' in release


def test_release_workflow_wrapper_never_builds_and_requires_prepare_image():
    wrapper = (ROOT / "scripts/run_release_workflow.py").read_text()
    assert '"docker", "build"' not in wrapper
    assert 'IMAGE_ARCHIVE' in wrapper and 'IMAGE_MANIFEST' in wrapper
    assert 'PREPARE_RUN_ID' in wrapper


def test_dispatch_requires_exact_operator_confirmation_for_production():
    with pytest.raises(ValidationError, match="explicit owner approval"):
        validate(values(RELEASE_MODE="production"), FakeGitHub())
    with pytest.raises(ValidationError, match="must not carry production approval"):
        validate(values(APPROVE_PRODUCTION="true"), FakeGitHub())
    approved = validate(values(RELEASE_MODE="production", APPROVE_PRODUCTION="true"), FakeGitHub())
    assert approved["mode"] == "production"


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
    evidence_step = workflow.index("Publish immutable Deploy evidence Release")
    assert evidence_step > apply_step
    assert "continue-on-error: true" not in workflow[evidence_step:evidence_step + 240]
    assert "publication-recovery" in workflow
    assert "MAXIBOT_RELEASE_ADMIN_TOKEN" in workflow[evidence_step:evidence_step + 400]


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
    archive = tmp_path / "image.tar.gz"
    manifest = tmp_path / "manifest.json"
    result_path = tmp_path / "result.json"
    rehearsal_path = tmp_path / "prepare-result.json"
    archive.write_bytes(b"prepared")
    manifest.write_text(json.dumps({"candidate_sha": SHA, "prepare_run_id": "123", "prepare_run_attempt": "1", "archive_sha256": "a" * 64, "platform": "linux/amd64", "image_id": "sha256:" + "1" * 64}))
    rehearsal_path.write_text(json.dumps({"schema": "MAXIBOT_PREPARE_RECEIPT_V1", "candidate_sha": SHA, "github_run_id": "123", "github_run_attempt": "1", "status": "READY", "tests": "success", "frozen_path_guard": "success", "linux_amd64_image": "success", "private_docker_rehearsal": "PASS", "private_docker_version": "29.1.3", "private_store": "containerd", "private_snapshotter": "overlayfs", "private_network_isolated": "YES", "rehearsal_candidate_sha": SHA, "rehearsal_prepare_run_id": "123", "rehearsal_archive_sha256": "a" * 64}))
    for key, value in {
        "CANDIDATE_SHA": SHA, "TARGET_MIGRATION": MIGRATION, "RELEASE_MODE": "production",
        "APPROVE_PRODUCTION": "true", "RELEASE_RESULT": str(result_path), "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1", "PREPARE_RUN_ID": "123", "PREPARE_RUN_ATTEMPT": "1",
        "IMAGE_ARCHIVE": str(archive), "IMAGE_MANIFEST": str(manifest),
        "IMAGE_RESULT": str(rehearsal_path),
    }.items():
        monkeypatch.setenv(key, value)
    git_commands = []
    def read_git(command, **kwargs):
        git_commands.append(command)
        return SHA if command[:2] == ["git", "rev-parse"] else ""
    monkeypatch.setattr(run_release_workflow.subprocess, "check_output", read_git)
    monkeypatch.setattr(run_release_workflow, "checksum", lambda _: "a" * 64)
    monkeypatch.setattr(run_release_workflow, "inspect_prepared_archive", lambda *_: ("sha256:" + "1" * 64, "linux/amd64"))
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
        assert result["image_archive_sha256"] == "a"*64
    assert len(commands) == 4
    assert [c[-1] for c, _ in commands] == ["--dry-run", "--apply"]*2
    assert all(SHA in c for c, _ in commands)
    assert all(env["RELEASE_IMAGE_ID"] == "sha256:"+"1"*64 for _, env in commands)
    assert all(command[1] in ("rev-parse", "status") for command in git_commands)
    assert archive.read_bytes() == b"prepared"


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
