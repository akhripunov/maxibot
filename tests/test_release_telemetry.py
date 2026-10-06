from datetime import datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from release_telemetry import summarize, timeline_metrics


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


def test_release_timeline_uses_actual_commit_prepare_preflight_and_deploy_times():
    result = timeline_metrics(
        commit_at="2026-10-05T09:00:00Z",
        prepare={"created_at": "2026-10-05T09:02:00Z", "run_started_at": "2026-10-05T09:03:00Z",
                 "updated_at": "2026-10-05T09:15:00Z", "status": "completed"},
        preflight={"created_at": "2026-10-05T09:15:10Z", "run_started_at": "2026-10-05T09:16:00Z",
                   "updated_at": "2026-10-05T09:22:00Z", "status": "completed"},
        production={"run_started_at": "2026-10-05T09:30:00Z", "updated_at": "2026-10-05T09:45:00Z",
                    "status": "completed"},
    )
    assert result == {
        "COMMIT_TO_PREPARE_START_SECONDS": 180,
        "PREPARE_DURATION_SECONDS": 720,
        "PREPARE_TO_PREFLIGHT_START_SECONDS": 60,
        "PREFLIGHT_DURATION_SECONDS": 360,
        "PREFLIGHT_TO_DEPLOY_START_SECONDS": 480,
        "DEPLOY_DURATION_SECONDS": 900,
        "COMMIT_TO_COMPLETE_SECONDS": 2700,
    }
