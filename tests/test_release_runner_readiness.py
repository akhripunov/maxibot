from pathlib import Path

import pytest

from scripts import release_runner_readiness as readiness


def env(**overrides):
    values = {
        "RUNNER_NAME": "maxibot-owner-mac",
        "RUNNER_OS": "macOS",
        "RUNNER_ARCH": "ARM64",
        "HOME": "/tmp/maxibot-home",
    }
    values.update(overrides)
    return values


def run_ok(_args, **_kwargs):
    return readiness.CommandResult(0, "Docker Engine 29.1.3")


def test_project_runner_docker_disk_and_cache_readiness_pass(tmp_path):
    result = readiness.check_readiness(
        env(HOME=str(tmp_path)), free_bytes=15 * readiness.GIB,
        command=run_ok,
    )
    assert result["RUNNER_READY"] == "YES"
    assert result["DOCKER_READY"] == "YES"
    assert result["DISK_READY"] == "YES"
    cache = Path(result["CACHE_PATH"])
    assert cache.is_dir()
    assert not list(cache.glob(".readiness-*"))


@pytest.mark.parametrize(
    "overrides, free, command, reason",
    [
        ({"RUNNER_NAME": "other-runner"}, 20 * readiness.GIB, run_ok, "runner_name"),
        ({"RUNNER_OS": "Linux"}, 20 * readiness.GIB, run_ok, "runner_os"),
        ({"RUNNER_ARCH": "X64"}, 20 * readiness.GIB, run_ok, "runner_arch"),
        ({}, 9 * readiness.GIB, run_ok, "disk_below_hard_minimum"),
        ({}, 20 * readiness.GIB, lambda *_a, **_k: readiness.CommandResult(1, ""), "docker_unavailable"),
    ],
)
def test_readiness_fails_closed_before_dispatch(tmp_path, overrides, free, command, reason):
    with pytest.raises(readiness.ReadinessError, match=reason):
        readiness.check_readiness(env(HOME=str(tmp_path), **overrides), free_bytes=free,
                                  command=command)
