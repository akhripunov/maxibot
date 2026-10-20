from __future__ import annotations

import hashlib
import json

import pytest

from scripts import ghcr_release_artifacts as artifacts
from scripts.ghcr_prepare_cache import resolve_prepare, pull_verified_image


SHA = "a" * 40
RUN_ID = "1234"
ATTEMPT = "2"


def oci_manifest(artifact_type: str, layer_type: str, payload: bytes) -> tuple[bytes, str]:
    layer_digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    manifest = json.dumps({
        "schemaVersion": 2,
        "mediaType": artifacts.OCI_MANIFEST_MEDIA_TYPE,
        "artifactType": artifact_type,
        "config": {"mediaType": artifacts.OCI_EMPTY_CONFIG_MEDIA_TYPE,
                   "digest": artifacts.OCI_EMPTY_CONFIG_DIGEST, "size": 2},
        "layers": [{"mediaType": layer_type, "digest": layer_digest,
                    "size": len(payload)}],
    }, separators=(",", ":")).encode()
    return manifest, "sha256:" + hashlib.sha256(manifest).hexdigest()


def test_artifact_tag_binds_project_sha_run_and_attempt():
    assert artifacts.artifact_tag("prepare", SHA, RUN_ID, ATTEMPT) == f"prepare-{SHA}-{RUN_ID}-{ATTEMPT}"


def test_release_receipts_reuse_the_existing_repository_linked_image_package():
    assert artifacts.PREPARE_REPOSITORY == artifacts.IMAGE_REPOSITORY
    assert artifacts.EVIDENCE_REPOSITORY == artifacts.IMAGE_REPOSITORY


def test_oras_subprocess_failure_logs_safe_operation_and_http_status(monkeypatch):
    import subprocess

    def fail(args, **_kwargs):
        raise subprocess.CalledProcessError(
            1, args, output="registry token should stay private",
            stderr="unexpected status: 403 Forbidden secret-token-value")

    monkeypatch.setattr(artifacts.subprocess, "run", fail)
    with pytest.raises(artifacts.ArtifactError) as error:
        artifacts._run_oras(["oras", "push", "ghcr.io/owner/package:tag"])

    assert "ORAS push failed (exit 1; HTTP 403" in str(error.value)
    assert "403 Forbidden" in str(error.value)
    assert "secret-token-value" not in str(error.value)
    assert "registry token should stay private" not in str(error.value)


def test_oras_failure_diagnostic_redacts_credentials_and_is_bounded(monkeypatch):
    import subprocess

    secret = "super-secret-registry-password"
    monkeypatch.setenv("GHCR_READ_TOKEN", "ghp_abcdefghijklmnop123456")

    def fail(args, **_kwargs):
        raise subprocess.CalledProcessError(
            1, args, output=("upload failed " + "x" * 300),
            stderr=(f"403 Forbidden password={secret} Bearer abcdefghijklmnop1234567890 "
                    "https://registry.example/v2?token=querysecret response={private}"))

    monkeypatch.setattr(artifacts.subprocess, "run", fail)
    with pytest.raises(artifacts.ArtifactError) as error:
        artifacts._run_oras(["oras", "push", "ghcr.io/owner/package:tag"], input=secret)

    message = str(error.value)
    diagnostic = message.split("; detail: ", 1)[1].rstrip(")")
    assert len(diagnostic) <= 240
    for forbidden in (secret, "ghp_abcdefghijklmnop123456", "querysecret", "private",
                      "abcdefghijklmnop1234567890"):
        assert forbidden not in message
    assert "403 Forbidden" in message


@pytest.mark.parametrize("args", [
    ("gpoint", SHA, RUN_ID, ATTEMPT), ("prepare", "bad", RUN_ID, ATTEMPT),
    ("prepare", SHA, "0", ATTEMPT), ("prepare", SHA, RUN_ID, "0"),
])
def test_artifact_tag_rejects_invalid_or_foreign_identity(args):
    with pytest.raises(artifacts.ArtifactError):
        artifacts.artifact_tag(*args)


def test_verify_oci_artifact_binds_manifest_digest_layer_and_payload():
    payload = b'{"status":"READY"}'
    manifest, digest = oci_manifest(artifacts.PREPARE_ARTIFACT_TYPE,
                                    artifacts.PREPARE_LAYER_MEDIA_TYPE, payload)
    verified = artifacts.verify_oci_artifact(manifest, digest, payload,
        artifacts.PREPARE_ARTIFACT_TYPE, artifacts.PREPARE_LAYER_MEDIA_TYPE)
    assert verified == payload


@pytest.mark.parametrize("mutation", ["manifest", "artifact_type", "layer_type", "layer_hash", "size"])
def test_verify_oci_artifact_rejects_mismatched_identity(mutation):
    payload = b'{"status":"READY"}'
    manifest, digest = oci_manifest(artifacts.PREPARE_ARTIFACT_TYPE,
                                    artifacts.PREPARE_LAYER_MEDIA_TYPE, payload)
    value = json.loads(manifest)
    if mutation == "artifact_type":
        value["artifactType"] = "application/vnd.other"
    elif mutation == "layer_type":
        value["layers"][0]["mediaType"] = "application/vnd.other+json"
    elif mutation == "layer_hash":
        value["layers"][0]["digest"] = "sha256:" + "0" * 64
    elif mutation == "size":
        value["layers"][0]["size"] += 1
    else:
        value["schemaVersion"] = 1
    changed = json.dumps(value, separators=(",", ":")).encode()
    claimed_digest = digest if mutation == "manifest" else "sha256:" + hashlib.sha256(changed).hexdigest()
    with pytest.raises(artifacts.ArtifactError):
        artifacts.verify_oci_artifact(changed, claimed_digest, payload,
            artifacts.PREPARE_ARTIFACT_TYPE, artifacts.PREPARE_LAYER_MEDIA_TYPE)


def test_validate_prepare_receipt_requires_exact_sha_attempt_and_image_digest():
    receipt = {
        "schema": "MAXIBOT_PREPARE_RECEIPT_V2", "candidate_sha": SHA,
        "github_run_id": RUN_ID, "github_run_attempt": ATTEMPT, "status": "READY",
        "tests": "success", "frozen_path_guard": "success", "linux_amd64_image": "success",
        "ghcr_image_publish": "success", "private_docker_rehearsal": "PASS",
        "image": {"repository": "ghcr.io/akhripunov/maxibot-vps-api", "tag": f"prepare-{SHA}-{RUN_ID}-{ATTEMPT}",
                  "digest": "sha256:" + "b" * 64, "platform": "linux/amd64",
                  "config_digest": "sha256:" + "c" * 64, "labels": {
                      "org.opencontainers.image.revision": SHA,
                      "org.opencontainers.image.source-sha": SHA,
                      "org.maxibot.release.sha": SHA}},
    }
    assert artifacts.validate_prepare_receipt(receipt, SHA, RUN_ID, ATTEMPT)["image"]["digest"] == "sha256:" + "b" * 64

    for field, value in (("candidate_sha", "d" * 40), ("github_run_attempt", "3"),
                         ("status", "FAILED"), ("image", {"digest": "latest"})):
        altered = {**receipt, field: value}
        with pytest.raises(artifacts.ArtifactError):
            artifacts.validate_prepare_receipt(altered, SHA, RUN_ID, ATTEMPT)


@pytest.mark.parametrize("header_name", ["Docker-Content-Digest", "docker-content-digest"])
def test_resolve_oci_artifact_reads_tag_then_digest_and_verifies_exact_blob(header_name):
    payload = b'{"candidate_sha":"' + SHA.encode() + b'"}'
    manifest, digest = oci_manifest(artifacts.PREPARE_ARTIFACT_TYPE,
                                    artifacts.PREPARE_LAYER_MEDIA_TYPE, payload)
    repository = artifacts.PREPARE_REPOSITORY
    tag = artifacts.artifact_tag("prepare", SHA, RUN_ID, ATTEMPT)
    base = f"https://ghcr.io/v2/{repository.split('/', 1)[1]}"
    calls = []

    def fetch(url, accept):
        calls.append(url)
        if url == f"{base}/manifests/{tag}":
            return manifest, {header_name: digest}
        if url == f"{base}/manifests/{digest}":
            return manifest, {header_name: digest}
        if url == f"{base}/blobs/sha256:{hashlib.sha256(payload).hexdigest()}":
            return payload, {}
        raise AssertionError(url)

    result_digest, result = artifacts.resolve_oci_artifact(repository, tag,
        artifacts.PREPARE_ARTIFACT_TYPE, artifacts.PREPARE_LAYER_MEDIA_TYPE, fetch)
    assert (result_digest, result) == (digest, payload)
    assert calls == [f"{base}/manifests/{tag}", f"{base}/manifests/{digest}",
                     f"{base}/blobs/sha256:{hashlib.sha256(payload).hexdigest()}"]


def test_resolve_oci_artifact_rejects_tag_digest_drift_and_unapproved_repository():
    payload = b'{"status":"READY"}'
    manifest, digest = oci_manifest(artifacts.PREPARE_ARTIFACT_TYPE,
                                    artifacts.PREPARE_LAYER_MEDIA_TYPE, payload)
    tag = artifacts.artifact_tag("prepare", SHA, RUN_ID, ATTEMPT)
    calls = 0

    def drift(_url, _accept):
        nonlocal calls
        calls += 1
        return manifest, {"Docker-Content-Digest": digest if calls == 1 else "sha256:" + "f" * 64}

    with pytest.raises(artifacts.ArtifactError):
        artifacts.resolve_oci_artifact(artifacts.PREPARE_REPOSITORY, tag,
            artifacts.PREPARE_ARTIFACT_TYPE, artifacts.PREPARE_LAYER_MEDIA_TYPE, drift)
    with pytest.raises(artifacts.ArtifactError):
        artifacts.resolve_oci_artifact("ghcr.io/other/repo", tag,
            artifacts.PREPARE_ARTIFACT_TYPE, artifacts.PREPARE_LAYER_MEDIA_TYPE, drift)


def test_publish_oci_artifact_links_new_package_to_workflow_repository(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTOR", "owner")
    monkeypatch.setenv("GITHUB_TOKEN", "workflow-token")
    payload = b'{"candidate_sha":"' + SHA.encode() + b'"}'
    payload_path = tmp_path / "receipt.json"
    payload_path.write_bytes(payload)
    manifest, digest = oci_manifest(artifacts.PREPARE_ARTIFACT_TYPE,
                                    artifacts.PREPARE_LAYER_MEDIA_TYPE, payload)
    tag = artifacts.artifact_tag("prepare", SHA, RUN_ID, ATTEMPT)
    base = f"https://ghcr.io/v2/{artifacts.PREPARE_REPOSITORY.split('/', 1)[1]}"
    calls = []

    def run(args, input=None):
        calls.append((args, input))
        if args[1] == "push":
            return json.dumps({"digest": digest})
        return ""

    fetched_urls = []
    def fetch(url, _accept):
        fetched_urls.append(url)
        if url.endswith(f"/manifests/{tag}") or url.endswith(f"/manifests/{digest}"):
            return manifest, {"Docker-Content-Digest": digest}
        if "/blobs/" in url:
            return payload, {}
        raise AssertionError(url)

    result_digest, result_payload = artifacts.publish_oci_artifact(
        artifacts.PREPARE_REPOSITORY, "prepare", SHA, RUN_ID, ATTEMPT,
        payload_path, artifacts.PREPARE_ARTIFACT_TYPE, artifacts.PREPARE_LAYER_MEDIA_TYPE,
        run=run, fetch_factory=lambda *_args: fetch)

    push_args = calls[1][0]
    assert "--debug" in push_args
    assert "--disable-path-validation" in push_args
    annotation_index = push_args.index("--annotation")
    assert push_args[annotation_index + 1] == (
        "org.opencontainers.image.source=https://github.com/akhripunov/maxibot")
    assert fetched_urls == [
        f"{base}/manifests/{tag}",
        f"{base}/manifests/{digest}",
        f"{base}/blobs/sha256:{hashlib.sha256(payload).hexdigest()}",
    ]
    assert (result_digest, result_payload) == (digest, payload)


def test_resolve_prepare_requires_validator_selected_digest_and_writes_only_receipt(tmp_path, monkeypatch):
    receipt = {
        "schema": "MAXIBOT_PREPARE_RECEIPT_V2", "candidate_sha": SHA,
        "github_run_id": RUN_ID, "github_run_attempt": ATTEMPT, "status": "READY",
        "tests": "success", "frozen_path_guard": "success", "linux_amd64_image": "success",
        "ghcr_image_publish": "success", "private_docker_rehearsal": "PASS",
        "image": {"repository": artifacts.IMAGE_REPOSITORY, "tag": f"prepare-{SHA}-{RUN_ID}-{ATTEMPT}", "digest": "sha256:" + "b" * 64,
                  "config_digest": "sha256:" + "c" * 64, "platform": "linux/amd64", "labels": {
                      "org.opencontainers.image.revision": SHA,
                      "org.opencontainers.image.source-sha": SHA,
                      "org.maxibot.release.sha": SHA}},
    }
    payload = json.dumps(receipt).encode()
    digest = "sha256:" + "d" * 64
    monkeypatch.setattr("scripts.ghcr_prepare_cache.resolve_oci_artifact",
                        lambda *_args: (digest, payload))
    result = resolve_prepare(SHA, RUN_ID, ATTEMPT, digest, "owner", "token", tmp_path)
    assert result["digest"] == digest
    assert json.loads((tmp_path / "maxibot-prepare-result.json").read_text()) == receipt
    assert json.loads((tmp_path / "maxibot-image-manifest.json").read_text())["image"] == receipt["image"]

    with pytest.raises(artifacts.ArtifactError):
        resolve_prepare(SHA, RUN_ID, ATTEMPT, "sha256:" + "e" * 64,
                        "owner", "token", tmp_path)


def test_deploy_runner_uses_stdin_credential_and_removes_temporary_docker_config(tmp_path, monkeypatch):
    import subprocess
    from pathlib import Path
    calls = []
    docker_config = tmp_path / "docker-auth"
    docker_config.mkdir()
    monkeypatch.setattr("scripts.ghcr_prepare_cache.shutil.which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr("scripts.ghcr_prepare_cache.tempfile.mkdtemp", lambda **_kwargs: str(docker_config))
    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args[1:3] == ["login", "ghcr.io"]:
            config_path = Path(kwargs["env"]["DOCKER_CONFIG"]) / "config.json"
            assert json.loads(config_path.read_text()) == {"auths": {"ghcr.io": {}}}
            assert config_path.stat().st_mode & 0o777 == 0o600
            assert config_path.parent.stat().st_mode & 0o777 == 0o700
        return subprocess.CompletedProcess(args, 0)
    def check_output(args, **kwargs):
        fmt = args[-1]
        if fmt == "{{.Id}}": return "sha256:" + "c" * 64
        if fmt == "{{.Os}}/{{.Architecture}}": return "linux/amd64"
        return json.dumps({"org.opencontainers.image.revision": SHA,
            "org.opencontainers.image.source-sha": SHA, "org.maxibot.release.sha": SHA})
    monkeypatch.setattr("scripts.ghcr_prepare_cache.subprocess.run", run)
    monkeypatch.setattr("scripts.ghcr_prepare_cache.subprocess.check_output", check_output)
    image = {"repository": artifacts.IMAGE_REPOSITORY, "digest": "sha256:" + "b" * 64,
        "config_digest": "sha256:" + "c" * 64, "platform": "linux/amd64", "labels": {
            "org.opencontainers.image.revision": SHA, "org.opencontainers.image.source-sha": SHA,
            "org.maxibot.release.sha": SHA}}
    pull_verified_image(image, SHA, "owner", "secret-token")
    login = calls[0]
    assert login[0][1] == "login" and login[1]["input"] == "secret-token\n"
    assert "secret-token" not in " ".join(login[0])
    assert calls[-1][0][1] == "logout"
    assert all(call[1]["env"]["DOCKER_CONFIG"] == str(docker_config) for call in calls)
    assert not Path(docker_config).exists()


def test_pull_token_is_short_lived_and_scoped_to_image_package(monkeypatch):
    from scripts.ghcr_release_artifacts import registry_pull_token
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self, _limit): return json.dumps({"token": "short-pull-token", "expires_in": 300}).encode()
    requests = []
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: requests.append((request, timeout)) or Response())
    token = registry_pull_token(artifacts.IMAGE_REPOSITORY, "owner", "workflow-secret")
    assert token == "short-pull-token"
    request, timeout = requests[0]
    assert "repository%3Aakhripunov%2Fmaxibot-vps-api%3Apull" in request.full_url
    assert request.get_header("Authorization").startswith("Basic ")
    assert timeout == 20
    with pytest.raises(artifacts.ArtifactError):
        registry_pull_token("ghcr.io/akhripunov/unapproved", "owner", "workflow-secret")
