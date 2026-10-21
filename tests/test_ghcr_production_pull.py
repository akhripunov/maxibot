"""Exercise the production SSH payload without network or a real Docker daemon."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import ghcr_release_artifacts as artifacts

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
MANIFEST = "sha256:" + "b" * 64
CONFIG = "sha256:" + "c" * 64
REFERENCE = artifacts.IMAGE_REPOSITORY + "@" + MANIFEST


@pytest.mark.parametrize("actor,token,accepted", [
    ("github-actions[bot]", "fixture-workflow-secret", True),
    ("akhripunov", "fixture-workflow-secret", True),
    ("github-actions[bot]", "", False),
    ("", "fixture-workflow-secret", False),
    ("github-actions[bot];echo unsafe", "fixture-workflow-secret", False),
    ("owner\nother", "fixture-workflow-secret", False),
    ("[bot]", "fixture-workflow-secret", False),
])
def test_production_pull_credential_gate_accepts_controller_bot_without_weakening_token(actor, token, accepted):
    source = (ROOT / "scripts/release_prod.sh").read_text()
    gate = source[source.index('[[ -n "${GITHUB_TOKEN:-}"'):source.index('RELEASE_IMAGE_ID=${RELEASE_IMAGE_ID:-}')]
    env = os.environ.copy()
    env.update(GITHUB_ACTOR=actor, GITHUB_TOKEN=token)
    result = subprocess.run(
        ["bash", "-c", 'set -Eeuo pipefail\ndie() { echo BLOCKED >&2; exit 1; }\n' + gate],
        env=env, capture_output=True, text=True, timeout=5,
    )
    assert (result.returncode == 0) is accepted
    assert "fixture-workflow-secret" not in result.stdout + result.stderr


def response(body):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self, _limit): return json.dumps(body).encode()
    return lambda *_args, **_kwargs: Response()


def test_ghcr_token_response_without_optional_expiry_is_accepted():
    # GHCR's real response contains only token. Registry V2 defaults expiry to 60s.
    assert artifacts.registry_pull_token(
        artifacts.IMAGE_REPOSITORY, "owner", "workflow-secret",
        opener=response({"token": "fixture-pull-token"}),
    ) == "fixture-pull-token"


@pytest.mark.parametrize("expiry", [0, -1, 3601, True, None, "60"])
def test_explicit_invalid_token_expiry_is_rejected(expiry):
    with pytest.raises(artifacts.ArtifactError):
        artifacts.registry_pull_token(
            artifacts.IMAGE_REPOSITORY, "owner", "workflow-secret",
            opener=response({"token": "fixture-pull-token", "expires_in": expiry}),
        )


@pytest.mark.parametrize("expiry, expected_status", [(60, 0), (0, 1)])
def test_mint_cli_never_mixes_retry_diagnostics_into_credential_stdout(monkeypatch, capsys, expiry, expected_status):
    monkeypatch.setenv("GITHUB_ACTOR", "owner")
    monkeypatch.setenv("GITHUB_TOKEN", "workflow-secret")
    monkeypatch.setattr("urllib.request.urlopen", response({
        "token": "fixture-pull-token", "expires_in": expiry,
    }))
    assert artifacts.main_pull_token() == expected_status
    captured = capsys.readouterr()
    assert captured.out == ("fixture-pull-token" if expected_status == 0 else "")
    assert "fixture-pull-token" not in captured.err
    assert "workflow-secret" not in captured.out + captured.err


def run_production_pull(tmp_path, *, incorrect=None, mint_fails=False):
    """Run the actual remote shell with a controlled Docker CLI boundary."""
    script = (ROOT / "scripts/release_prod.sh").read_text()
    function = script[script.index("pull_ghcr_image() {"):script.index("say RELEASE_SSH_USER")]
    timer = script[script.index("timed_stage() {"):script.index("# Only wrap idempotent reads")]
    fixture_root = tmp_path / "source"
    (fixture_root / "scripts").mkdir(parents=True)
    (fixture_root / "scripts/ghcr_release_artifacts.py").write_text(
        "import sys\nsys.stdout.write('diagnostic-not-a-token'); sys.exit(1)\n" if mint_fails else
        "import sys\nsys.stdout.write('fixture-pull-token')\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(f"#!{sys.executable}\n" + '''import json, os, pathlib, sys
args = sys.argv[1:]
trace = pathlib.Path(os.environ["PULL_TRACE"])
state = json.loads(trace.read_text()) if trace.exists() else {}
if args[0] == "pull":
    auth_path = pathlib.Path(os.environ["DOCKER_CONFIG"]) / "config.json"
    state["auth"] = json.loads(auth_path.read_text())["auths"]["ghcr.io"]
    state["auth_path"] = str(auth_path)
    state["reference"] = args[1]
    state["mode"] = auth_path.stat().st_mode & 0o777
elif args[:2] == ["image", "inspect"]:
    fmt = args[-1]
    values = {"{{.Id}}": os.environ["TEST_MANIFEST"],
              "{{.Os}}/{{.Architecture}}": "linux/amd64"}
    if "RepoDigests" in fmt:
        print(os.environ["TEST_REFERENCE"])
    elif fmt in values:
        print(values[fmt])
    elif "Labels" in fmt:
        print(os.environ["TEST_SHA"])
    else:
        raise SystemExit("unexpected inspect format")
elif args[0] == "tag":
    state["tagged"] = args[1:]
else:
    raise SystemExit("unexpected Docker operation")
trace.write_text(json.dumps(state))
''')
    docker.chmod(0o755)
    # Use real Python for the exact stdin-to-config writer in the remote payload.
    (bin_dir / "python3").symlink_to(sys.executable)
    env = os.environ.copy()
    env.update(PATH=str(bin_dir) + os.pathsep + env["PATH"],
               PULL_TRACE=str(tmp_path / "trace.json"), TEST_MANIFEST=MANIFEST,
               TEST_REFERENCE=REFERENCE, TEST_SHA=SHA)
    if incorrect:
        env.update(incorrect)
    command = 'set -Eeuo pipefail\ndie() { echo "$*" >&2; exit 1; }\nROOT_DIR="$1"\nSSH=(bash -c)\n' + timer + function + (
        '\ntimed_stage IMAGE_PULL pull_ghcr_image "$2" "maxibot-vps-api:$4" "$3" "$4"\n'
    )
    result = subprocess.run(
        ["bash", "-c", command, "pull-test", str(fixture_root), REFERENCE, CONFIG, SHA],
        env=env, capture_output=True, text=True, timeout=20,
    )
    trace_path = tmp_path / "trace.json"
    trace = json.loads(trace_path.read_text()) if trace_path.exists() else {}
    return result, trace


def test_production_pull_sends_registry_bearer_and_removes_auth_file(tmp_path):
    result, trace = run_production_pull(tmp_path)
    assert trace["auth"] == {"registrytoken": "fixture-pull-token"}
    assert trace["mode"] == 0o600
    assert not Path(trace["auth_path"]).exists()
    assert "fixture-pull-token" not in result.stdout + result.stderr
    assert "workflow-secret" not in result.stdout + result.stderr


def test_containerd_manifest_id_can_differ_from_verified_config_digest(tmp_path):
    result, trace = run_production_pull(tmp_path)
    assert result.returncode == 0, result.stderr
    assert trace["reference"] == REFERENCE
    assert trace["tagged"] == [REFERENCE, "maxibot-vps-api:" + SHA]


def test_mint_failure_cannot_start_remote_pull_even_with_nonempty_stdout(tmp_path):
    result, trace = run_production_pull(tmp_path, mint_fails=True)
    assert result.returncode != 0
    assert trace == {}
    assert "Could not mint" in result.stderr


@pytest.mark.parametrize("incorrect", [
    {"TEST_MANIFEST": "sha256:" + "d" * 64},
    {"TEST_REFERENCE": artifacts.IMAGE_REPOSITORY + "@sha256:" + "d" * 64},
    {"TEST_SHA": "d" * 40},
])
def test_wrong_manifest_repository_digest_or_source_never_gets_tagged(tmp_path, incorrect):
    result, trace = run_production_pull(tmp_path, incorrect=incorrect)
    assert result.returncode != 0
    assert "tagged" not in trace
    assert not Path(trace["auth_path"]).exists()
