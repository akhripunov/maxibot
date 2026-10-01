from __future__ import annotations

import sys
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_release_dispatch import ValidationError, validate  # noqa: E402
from publish_release_to_linear import SyncError, upsert_block  # noqa: E402
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
    assert any(f"head_sha={SHA}" in path for path in github.paths)


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


def test_linear_owned_block_updates_in_place_and_preserves_architect_text():
    before = "Architect-owned opening.\n\n<!-- MB_RELEASE_START -->\nold\n<!-- MB_RELEASE_END -->\n\nArchitect-owned closing."
    after = upsert_block(before, "MB_RELEASE", "new release status")
    assert "Architect-owned opening." in after
    assert "Architect-owned closing." in after
    assert "old" not in after
    assert "new release status" in after


def test_linear_owned_block_fails_closed_on_duplicate_markers():
    content = "<!-- MB_RELEASE_START -->x<!-- MB_RELEASE_END -->\n<!-- MB_RELEASE_START -->y<!-- MB_RELEASE_END -->"
    with pytest.raises(SyncError, match="markers are malformed"):
        upsert_block(content, "MB_RELEASE", "next")
