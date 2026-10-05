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
