import sys
import json
import ssl
import urllib.error
from types import SimpleNamespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_runner_readiness as readiness  # noqa: E402
from release_network_retry import RetryBudget  # noqa: E402
from release_runner_readiness import MIN_FREE_BYTES, RunnerReadinessError, ensure_free_disk, probe_deployment_ssh, release_artifact_cache_path, validate_runner_identity_and_scope, validate_release_host  # noqa: E402

SHA = "a" * 40


def ready(**overrides):
    values = {"runner_name": "maxibot-owner-mac", "runner_os": "macOS", "runner_arch": "ARM64",
              "free_bytes": MIN_FREE_BYTES,
              "main_sha": SHA, "candidate_sha": SHA, "active_conflict": False, "paths_ready": True,
              "tools_ready": True, "network_ready": True}
    values.update(overrides)
    return validate_release_host(**values)


def test_runner_readiness_passes_for_exact_mac_lane_and_release_dependencies():
    result = ready()
    assert result["deployment_runtime"] == "VPS_VIA_SSH"
    assert "docker" not in result
    assert result["network"] == "READY"
    assert result["conflicting_production_mutation"] == "NO"


@pytest.mark.parametrize("overrides", [
    {"runner_name": "other-mac"}, {"runner_os": "Linux"}, {"runner_arch": "X64"},
    {"free_bytes": MIN_FREE_BYTES - 1}, {"main_sha": "b" * 40}, {"active_conflict": True},
    {"paths_ready": False}, {"tools_ready": False},
])
def test_runner_readiness_fails_closed_before_expensive_or_mutating_stages(overrides):
    with pytest.raises(RunnerReadinessError):
        ready(**overrides)


def test_runner_readiness_rejects_unavailable_canonical_deployment_network():
    with pytest.raises(RunnerReadinessError, match="deployment SSH network"):
        ready(network_ready=False)


def test_runner_readiness_never_invokes_local_docker(monkeypatch, tmp_path):
    subprocess_calls = []
    monkeypatch.setattr(readiness, "release_artifact_cache_path", lambda _home: tmp_path / "cache")
    monkeypatch.setattr(readiness, "_safe_writable_path", lambda *_args: True)
    monkeypatch.setattr(readiness, "probe_deployment_ssh", lambda **_kwargs: True)
    monkeypatch.setattr(readiness, "ensure_free_disk", lambda *_args: MIN_FREE_BYTES)
    monkeypatch.setattr(readiness.shutil, "which", lambda _binary: "/usr/bin/tool")
    monkeypatch.setattr(readiness, "_request", lambda path, _token: (
        {"object": {"sha": SHA}} if path == "git/ref/heads/main" else {"workflow_runs": []}))
    monkeypatch.setattr(readiness.subprocess, "run", lambda command, **_kwargs: subprocess_calls.append(command))
    env = {"GITHUB_TOKEN": "test-only", "RUNNER_TEMP": str(tmp_path / "runner"),
           "RUNNER_NAME": "maxibot-owner-mac", "RUNNER_OS": "macOS", "RUNNER_ARCH": "ARM64",
           "PRODUCTION_HOST": "135.106.166.46", "PRODUCTION_SSH_KEY": str(tmp_path / "ssh-key"),
           "CANDIDATE_SHA": SHA, "GITHUB_RUN_ID": "123"}
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    facts = readiness.run()

    assert facts["deployment_runtime"] == "VPS_VIA_SSH"
    assert not any(command and command[0] == "docker" for command in subprocess_calls)


def test_runner_scope_preflight_requires_exact_candidate_and_no_active_conflict():
    facts = {"runner_name": "maxibot-owner-mac", "runner_os": "macOS", "runner_arch": "ARM64",
             "main_sha": SHA, "candidate_sha": SHA, "active_conflict": False,
             "paths_ready": True, "tools_ready": True, "network_ready": True}

    assert validate_runner_identity_and_scope(**facts)["candidate_sha"] == SHA

    with pytest.raises(RunnerReadinessError, match="exact current main"):
        validate_runner_identity_and_scope(**{**facts, "candidate_sha": "b" * 40})
    with pytest.raises(RunnerReadinessError, match="Conflicting MAXIBOT production mutation"):
        validate_runner_identity_and_scope(**{**facts, "active_conflict": True})


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


def test_deployment_network_probe_retries_temporary_ssh_outage(tmp_path):
    key = tmp_path / "identity"
    key.write_text("test-key")
    results = iter([
        SimpleNamespace(returncode=255, stdout="", stderr="Connection timed out"),
        SimpleNamespace(returncode=0, stdout="MAXIBOT_DEPLOYMENT_NETWORK_READY\n", stderr=""),
    ])
    calls = []

    def run_command(command, **kwargs):
        calls.append(command)
        return next(results)

    assert readiness.probe_deployment_ssh(host="135.106.166.46", key=str(key),
        run_command=run_command, budget=RetryBudget(seconds=10),
        retry_options={"sleep": lambda _seconds: None, "jitter": lambda _low, _high: 0,
            "emit": lambda _event: None})
    assert len(calls) == 2


def test_deployment_network_probe_does_not_retry_authentication_failure(tmp_path):
    key = tmp_path / "identity"
    key.write_text("test-key")
    calls = []

    def run_command(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=255, stdout="", stderr="Permission denied (publickey)")

    assert not readiness.probe_deployment_ssh(host="135.106.166.46", key=str(key),
        run_command=run_command, budget=RetryBudget(seconds=10),
        retry_options={"sleep": lambda _seconds: None, "jitter": lambda _low, _high: 0,
            "emit": lambda _event: None})
    assert len(calls) == 1


def test_disk_readiness_prunes_only_known_release_cache_when_below_threshold(tmp_path):
    calls = []
    measurements = iter([MIN_FREE_BYTES - 1, MIN_FREE_BYTES + 10])

    result = ensure_free_disk(tmp_path, lambda: calls.append("pruned") or 123,
                              lambda _path: SimpleNamespace(free=next(measurements)))

    assert result == MIN_FREE_BYTES + 10
    assert calls == ["pruned"]


def test_disk_readiness_does_not_prune_cache_when_already_ready(tmp_path):
    calls = []

    result = ensure_free_disk(tmp_path, lambda: calls.append("pruned"),
                              lambda _path: SimpleNamespace(free=MIN_FREE_BYTES))

    assert result == MIN_FREE_BYTES
    assert calls == []


def test_disk_readiness_stays_fail_closed_if_scoped_cache_reclaim_is_insufficient(tmp_path):
    measurements = iter([MIN_FREE_BYTES - 100, MIN_FREE_BYTES - 1])

    with pytest.raises(RunnerReadinessError, match=f"Insufficient free disk.*free_bytes={MIN_FREE_BYTES - 1}.*required_bytes={MIN_FREE_BYTES}"):
        ensure_free_disk(tmp_path, lambda: 1, lambda _path: SimpleNamespace(free=next(measurements)))


def test_release_cache_path_rejects_symlinked_parent_before_pruning(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "other-cache"
    outside.mkdir()
    old_bundle = outside / ("v2-" + "a" * 64 + ".zip")
    old_bundle.write_bytes(b"must not delete")
    (home / ".cache").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RunnerReadinessError, match="unsafe symlink"):
        release_artifact_cache_path(home)
    assert old_bundle.exists()


def test_release_cache_path_accepts_only_canonical_non_symlink_project_path(tmp_path):
    home = tmp_path / "home"
    (home / ".cache" / "maxibot-release" / "artifacts").mkdir(parents=True)

    assert release_artifact_cache_path(home) == home / ".cache" / "maxibot-release" / "artifacts"


def test_github_request_retries_one_temporary_tls_eof_then_passes(monkeypatch):
    calls = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return json.dumps({"object": {"sha": SHA}}).encode()

    def opener(request, timeout):
        calls.append((request.full_url, timeout))
        if len(calls) == 1:
            raise urllib.error.URLError(ssl.SSLEOFError("TLS peer closed"))
        return Response()

    result = readiness._request("git/ref/heads/main", "workflow-token", opener=opener,
        budget=RetryBudget(seconds=10), retry_options={"sleep": lambda _seconds: None,
            "jitter": lambda _low, _high: 0, "emit": lambda _event: None})

    assert result == {"object": {"sha": SHA}}
    assert len(calls) == 2


@pytest.mark.parametrize("status", [401, 403])
def test_github_request_does_not_retry_authorization_failures(status):
    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, status, "private body", {}, None)

    with pytest.raises(RunnerReadinessError, match=f"HTTP {status}") as error:
        readiness._request("git/ref/heads/main", "do-not-print-this-token", opener=opener,
            budget=RetryBudget(seconds=10), retry_options={"sleep": lambda _seconds: None,
                "jitter": lambda _low, _high: 0, "emit": lambda _event: None})
    assert len(calls) == 1
    assert "do-not-print-this-token" not in str(error.value)
    assert "private body" not in str(error.value)


def test_main_sha_returned_after_retry_is_still_checked_exactly():
    calls = []
    changed_sha = "b" * 40

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return json.dumps({"object": {"sha": changed_sha}}).encode()

    def opener(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.URLError(TimeoutError("temporary network timeout"))
        return Response()

    latest = readiness._request("git/ref/heads/main", "workflow-token", opener=opener,
        budget=RetryBudget(seconds=10), retry_options={"sleep": lambda _seconds: None,
            "jitter": lambda _low, _high: 0, "emit": lambda _event: None})
    with pytest.raises(RunnerReadinessError, match="exact current main"):
        validate_runner_identity_and_scope(runner_name="maxibot-owner-mac", runner_os="macOS",
            runner_arch="ARM64", main_sha=latest["object"]["sha"], candidate_sha=SHA,
            active_conflict=False, paths_ready=True, tools_ready=True, network_ready=True)
    assert len(calls) == 2


@pytest.mark.parametrize("reference", [
    [], {}, {"object": []}, {"object": {"sha": "not-a-commit"}},
])
def test_malformed_current_main_reference_fails_with_safe_category(reference):
    with pytest.raises(RunnerReadinessError, match="invalid main reference"):
        readiness.current_main_sha(reference)


def test_candidate_sha_with_invalid_shape_fails_closed():
    with pytest.raises(RunnerReadinessError, match="main reference or candidate SHA is invalid"):
        readiness.validate_runner_identity_and_scope(
            runner_name="maxibot-owner-mac", runner_os="macOS", runner_arch="ARM64",
            main_sha=SHA, candidate_sha="not-a-commit", active_conflict=False,
            paths_ready=True, tools_ready=True, network_ready=True)


def test_conflicting_deploy_found_after_retry_still_blocks_readiness():
    import json
    import urllib.error

    calls = []
    current = "123"

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return b'{"workflow_runs":[{"id":456,"status":"in_progress"}]}'

    def opener(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.URLError(TimeoutError("temporary network timeout"))
        return Response()

    active = readiness._request("actions/workflows/maxibot-deploy.yml/runs?branch=main&per_page=100",
        "workflow-token", opener=opener, budget=RetryBudget(seconds=10),
        retry_options={"sleep": lambda _seconds: None, "jitter": lambda _low, _high: 0,
            "emit": lambda _event: None})
    assert readiness.has_conflicting_production_run(active["workflow_runs"], current)
    with pytest.raises(RunnerReadinessError, match="Conflicting MAXIBOT production mutation"):
        validate_runner_identity_and_scope(runner_name="maxibot-owner-mac", runner_os="macOS",
            runner_arch="ARM64", main_sha=SHA, candidate_sha=SHA, active_conflict=True,
            paths_ready=True, tools_ready=True, network_ready=True)
    assert len(calls) == 2


def test_github_request_exhausted_budget_fails_with_network_unavailable():
    def opener(_request, **_kwargs):
        raise AssertionError("zero budget must fail before opening a connection")

    with pytest.raises(RunnerReadinessError, match="NETWORK_UNAVAILABLE stage=runner_readiness_github_api"):
        readiness._request("git/ref/heads/main", "workflow-token", opener=opener,
            budget=RetryBudget(seconds=0), retry_options={"emit": lambda _event: None})


@pytest.mark.parametrize("payload", [b"[]", b'{"workflow_runs":[{"id":123}]}'])
def test_github_production_run_data_with_invalid_shape_fails_closed(payload):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return payload

    with pytest.raises(RunnerReadinessError, match="invalid JSON|invalid production workflow data"):
        active = readiness._request("actions/workflows/maxibot-deploy.yml/runs", "workflow-token",
            opener=lambda *_args, **_kwargs: Response(), budget=RetryBudget(seconds=10),
            retry_options={"emit": lambda _event: None})
        readiness.has_conflicting_production_run(active.get("workflow_runs"), "123")
