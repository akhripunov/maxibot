from datetime import datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from release_telemetry import summarize


def test_runner_telemetry_classifies_and_measures_wall_clock():
    now = datetime(2026, 10, 5, 10, 0, 30, tzinfo=timezone.utc)
    jobs = [[
        {"labels": ["ubuntu-latest"], "runner_name": "GitHub Actions 4",
         "started_at": "2026-10-05T10:00:00Z", "completed_at": "2026-10-05T10:00:12Z"},
        {"labels": ["self-hosted", "macOS", "maxibot-production"], "runner_name": "maxibot-owner-mac",
         "started_at": "2026-10-05T10:00:05Z", "completed_at": None},
        {"labels": ["ubuntu-latest"], "runner_name": None,
         "status": "queued", "started_at": None},
    ]]
    assert summarize(jobs, now) == {
        "GITHUB_HOSTED_JOB_COUNT": 1,
        "GITHUB_HOSTED_RUNNER_SECONDS": 12,
        "SELF_HOSTED_JOB_COUNT": 1,
        "SELF_HOSTED_RUNNER_SECONDS": 25,
    }


def test_release_workflow_keeps_publishable_when_telemetry_fails():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/maxibot-deploy.yml").read_text()
    telemetry = workflow.split("- name: Measure release runner usage", 1)[1].split("- name:", 1)[0]
    fallback = workflow.split("- name: Mark release telemetry unknown", 1)[1].split("- name:", 1)[0]
    assert "continue-on-error: true" in telemetry
    assert "if: always() && steps.telemetry.outcome != 'success'" in fallback
    for key in ("GITHUB_HOSTED_JOB_COUNT", "GITHUB_HOSTED_RUNNER_SECONDS", "SELF_HOSTED_JOB_COUNT",
                "SELF_HOSTED_RUNNER_SECONDS", "DUPLICATE_HOSTED_WORK_REMOVED", "CACHE_PREVENTED_DOWNLOAD"):
        assert f"{key}=UNKNOWN" in fallback
    assert "Publish preflight result to MAXIBOT Linear" in workflow
