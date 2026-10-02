from __future__ import annotations

import sys
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_release_dispatch import ValidationError, validate  # noqa: E402
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

    def __call__(self, path, token):
        self.paths.append(path)
        if path == "git/ref/heads/main":
            return {"object": {"sha": self.main_sha}}
        if path.startswith("actions/workflows/"):
            return {"workflow_runs": self.runs}
        if path.startswith("actions/runs/") and path.endswith("/artifacts"):
            run_id = path.split("/")[2]
            attempt = next((run.get("run_attempt", 1) for run in self.runs if str(run["id"]) == run_id), 1)
            return {"artifacts": [{"id": 123, "name": f"maxibot-image-{SHA}-{run_id}-{attempt}", "expired": False}]}
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


def test_dispatch_rejects_missing_or_expired_prepared_image_artifact():
    class MissingArtifactGitHub(FakeGitHub):
        def __call__(self, path, token):
            if path.startswith("actions/runs/"):
                return {"artifacts": []}
            return super().__call__(path, token)

    with pytest.raises(ValidationError, match="image artifact"):
        validate(values(), MissingArtifactGitHub())


def test_deploy_workflow_downloads_prepare_artifact_and_uses_portable_pinned_python():
    workflow = (ROOT / ".github/workflows/maxibot-deploy.yml").read_text()
    assert "scripts/prepared_artifact_cache.py" in workflow
    assert "PREPARE_ARTIFACT_ID: ${{ needs.validate.outputs.prepare_artifact_id }}" in workflow
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
    assert "inputs.mode == 'production' && needs.deploy.outputs.status != ''" in workflow
    assert "scripts/rehearse_prepared_image.py" not in workflow
    prepare = (ROOT / ".github/workflows/maxibot-prepare.yml").read_text()
    assert "scripts/rehearse_private_docker.py" in prepare
    assert "docker/setup-docker-action@2bf61fb9464cc67f0cbdeabed6aa0380accd1c70" in prepare
    assert '"containerd-snapshotter":true' in prepare
    assert '"native_image_id": native_image_id' in prepare
    assert 'ARCHIVE_CONFIG_ID=' in prepare


def test_prepare_allows_explicit_branch_rehearsal_without_linear_publish():
    workflow = (ROOT / ".github/workflows/maxibot-prepare.yml").read_text()
    assert "github.event_name == 'workflow_dispatch' || github.ref == 'refs/heads/main'" in workflow
    assert "BRANCH_REHEARSAL_SHA=PASS" in workflow
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
    assert "Publish complete Prepare artifacts" in workflow
    assert workflow.index("Publish complete Prepare artifacts") < workflow.index("Publish final Prepare result to MAXIBOT Linear")
    assert "PREPARE_RESULT: ${{ needs.prepare.result }}" in workflow
    assert publish_release_to_linear.candidate_status(prepare_result) == expected


def test_preflight_only_does_not_record_production_event():
    workflow = (ROOT / ".github/workflows/maxibot-deploy.yml").read_text()
    step = workflow.split("- name: Publish production result to MAXIBOT Linear", 1)[1].split("- name: Preserve exact release result", 1)[0]
    assert "if: always() && inputs.mode == 'production' && needs.deploy.outputs.status != ''" in step
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
    manifest.write_text(json.dumps({"candidate_sha": SHA, "prepare_run_id": "123", "archive_sha256": "a" * 64, "platform": "linux/amd64", "image_id": "sha256:" + "1" * 64}))
    rehearsal_path.write_text(json.dumps({"private_docker_rehearsal": "PASS"}))
    for key, value in {
        "CANDIDATE_SHA": SHA, "TARGET_MIGRATION": MIGRATION, "RELEASE_MODE": "production",
        "APPROVE_PRODUCTION": "true", "RELEASE_RESULT": str(result_path), "GITHUB_RUN_ID": "123",
        "PREPARE_RUN_ID": "123", "IMAGE_ARCHIVE": str(archive), "IMAGE_MANIFEST": str(manifest),
        "IMAGE_RESULT": str(rehearsal_path),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(run_release_workflow.subprocess, "check_output", lambda command, **kwargs: SHA if command[:2] == ["git", "rev-parse"] else "")
    monkeypatch.setattr(run_release_workflow, "checksum", lambda _: "a" * 64)
    monkeypatch.setattr(run_release_workflow, "inspect_prepared_archive", lambda *_: ("sha256:" + "1" * 64, "linux/amd64"))
    commands = []
    monkeypatch.setattr(run_release_workflow, "run", lambda command, env: (commands.append(command) or (1, "MIGRATION_ALLOWED=NO\n")))
    assert run_release_workflow.main() == 1
    assert run_release_workflow.main() == 1
    assert len(commands) == 2
    assert all(command[:2] != ["git", "push"] for command in commands)
    assert json.loads(result_path.read_text())["production_changed"] is False


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
