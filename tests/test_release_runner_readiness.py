import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from release_runner_readiness import MIN_FREE_BYTES, RunnerReadinessError, probe_deployment_ssh, validate_runtime  # noqa: E402

SHA = "a" * 40


def ready(**overrides):
    values = {"runner_name": "maxibot-owner-mac", "runner_os": "macOS", "runner_arch": "ARM64",
              "free_bytes": MIN_FREE_BYTES, "docker_available": True, "docker_os": "linux",
              "main_sha": SHA, "candidate_sha": SHA, "active_conflict": False, "paths_ready": True,
              "tools_ready": True, "network_ready": True}
    values.update(overrides)
    return validate_runtime(**values)


def test_runner_readiness_passes_for_exact_mac_lane_and_release_dependencies():
    result = ready()
    assert result["docker"] == "READY"
    assert result["network"] == "READY"
    assert result["conflicting_production_mutation"] == "NO"


@pytest.mark.parametrize("overrides", [
    {"runner_name": "other-mac"}, {"runner_os": "Linux"}, {"runner_arch": "X64"},
    {"free_bytes": MIN_FREE_BYTES - 1}, {"docker_available": False},
    {"docker_os": "windows"}, {"main_sha": "b" * 40}, {"active_conflict": True},
    {"paths_ready": False}, {"tools_ready": False},
])
def test_runner_readiness_fails_closed_before_expensive_or_mutating_stages(overrides):
    with pytest.raises(RunnerReadinessError):
        ready(**overrides)


def test_runner_readiness_rejects_unavailable_canonical_deployment_network():
    with pytest.raises(RunnerReadinessError, match="deployment SSH network"):
        ready(network_ready=False)


def test_deployment_network_probe_uses_canonical_host_and_read_only_ssh_command(tmp_path):
    key = tmp_path / "identity"
    key.write_text("test-key")
    captured = {}

    class Result:
        returncode = 0
        stdout = "MAXIBOT_DEPLOYMENT_NETWORK_READY\n"

    def run_command(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Result()

    assert probe_deployment_ssh(host="135.106.166.46", key=str(key), run_command=run_command)
    assert captured["command"][-2:] == ["root@135.106.166.46", "printf MAXIBOT_DEPLOYMENT_NETWORK_READY"]
    assert captured["kwargs"]["timeout"] == 15
    assert not probe_deployment_ssh(host="203.0.113.1", key=str(key), run_command=run_command)
