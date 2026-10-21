from __future__ import annotations

import hashlib
import json

import pytest

from scripts import ghcr_release_artifacts as artifacts
from scripts.ghcr_prepare_cache import resolve_prepare, pull_verified_image, verify_image_config_digest


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


def test_oras_login_retries_transient_tls_eof_then_pushes_once(tmp_path, monkeypatch, capsys):
    import subprocess
    from scripts.release_network_retry import RetryBudget

    monkeypatch.setenv("GITHUB_ACTOR", "github-actions[bot]")
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-secret-token")
    monkeypatch.setattr(artifacts, "NETWORK_BUDGET", RetryBudget(seconds=5))
    retry = artifacts.retry_network_read
    monkeypatch.setattr(artifacts, "retry_network_read", lambda *args, **kwargs: retry(
        *args, **kwargs, sleep=lambda _seconds: None, jitter=lambda _low, _high: 0,
        emit=lambda _event: None))

    payload = b'{"candidate_sha":"' + SHA.encode() + b'"}'
    path = tmp_path / "receipt.json"
    path.write_bytes(payload)
    manifest, digest = oci_manifest(artifacts.PREPARE_ARTIFACT_TYPE,
                                    artifacts.PREPARE_LAYER_MEDIA_TYPE, payload)
    calls = []

    def run(args, input=None, **kwargs):
        calls.append((args, kwargs))
        if args[1] == "login" and sum(1 for item, _ in calls if item[1] == "login") == 1:
            raise subprocess.CalledProcessError(
                1, args, stderr='Error: failed to validate the credentials for ghcr.io: Get "https://ghcr.io/v2/": EOF')
        if args[1] == "push":
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"digest": digest}), stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    def fetch(url, _accept):
        if url.endswith("/manifests/" + artifacts.artifact_tag("prepare", SHA, RUN_ID, ATTEMPT)) or url.endswith("/manifests/" + digest):
            return manifest, {"Docker-Content-Digest": digest}
        if "/blobs/" in url:
            return payload, {}
        raise AssertionError(url)

    monkeypatch.setattr(artifacts.subprocess, "run", run)
    result = artifacts.publish_oci_artifact(
        artifacts.PREPARE_REPOSITORY, "prepare", SHA, RUN_ID, ATTEMPT, path,
        artifacts.PREPARE_ARTIFACT_TYPE, artifacts.PREPARE_LAYER_MEDIA_TYPE,
        fetch_factory=lambda *_args: fetch)

    assert result == (digest, payload)
    assert [args[1] for args, _ in calls] == ["login", "login", "push"]
    login_config = calls[0][0][calls[0][0].index("--registry-config") + 1]
    assert not __import__("pathlib").Path(login_config).exists()
    assert "fixture-secret-token" not in capsys.readouterr().out


@pytest.mark.parametrize("detail", [
    "unexpected status: 403 Forbidden",
    "x509: certificate signed by unknown authority",
    'permission denied: Head "https://ghcr.io/v2/": EOF',
    'TLS protocol version unsupported: Head "https://ghcr.io/v2/": EOF',
])
def test_oras_login_does_not_retry_auth_certificate_or_tls_protocol_failures(tmp_path, monkeypatch, detail, capsys):
    import subprocess
    from scripts.release_network_retry import RetryBudget

    monkeypatch.setenv("GITHUB_ACTOR", "github-actions[bot]")
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-secret-token")
    monkeypatch.setattr(artifacts, "NETWORK_BUDGET", RetryBudget(seconds=5))
    retry = artifacts.retry_network_read
    monkeypatch.setattr(artifacts, "retry_network_read", lambda *args, **kwargs: retry(
        *args, **kwargs, sleep=lambda _seconds: None, jitter=lambda _low, _high: 0,
        emit=lambda _event: None))
    path = tmp_path / "receipt.json"
    path.write_bytes(b"payload")
    calls = []

    def run(args, input=None, **kwargs):
        calls.append(args[1])
        raise subprocess.CalledProcessError(1, args, stderr=detail)

    monkeypatch.setattr(artifacts.subprocess, "run", run)
    with pytest.raises(artifacts.ArtifactError):
        artifacts.publish_oci_artifact(
            artifacts.PREPARE_REPOSITORY, "prepare", SHA, RUN_ID, ATTEMPT, path,
            artifacts.PREPARE_ARTIFACT_TYPE, artifacts.PREPARE_LAYER_MEDIA_TYPE,
            fetch_factory=lambda *_args: pytest.fail("login failure must prevent registry readback"))

    assert calls == ["login"]
    assert "fixture-secret-token" not in capsys.readouterr().out


def test_oras_push_tls_eof_is_never_retried(tmp_path, monkeypatch):
    import subprocess
    from scripts.release_network_retry import RetryBudget

    monkeypatch.setenv("GITHUB_ACTOR", "github-actions[bot]")
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-secret-token")
    monkeypatch.setattr(artifacts, "NETWORK_BUDGET", RetryBudget(seconds=5))
    retry = artifacts.retry_network_read
    monkeypatch.setattr(artifacts, "retry_network_read", lambda *args, **kwargs: retry(
        *args, **kwargs, sleep=lambda _seconds: None, jitter=lambda _low, _high: 0,
        emit=lambda _event: None))
    path = tmp_path / "receipt.json"
    path.write_bytes(b"payload")
    calls = []

    def run(args, input=None, **kwargs):
        calls.append(args[1])
        if args[1] == "login":
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        raise subprocess.CalledProcessError(
            1, args, stderr='failed to do request: Head "https://ghcr.io/v2/": EOF')

    monkeypatch.setattr(artifacts.subprocess, "run", run)
    with pytest.raises(artifacts.ArtifactError):
        artifacts.publish_oci_artifact(
            artifacts.PREPARE_REPOSITORY, "prepare", SHA, RUN_ID, ATTEMPT, path,
            artifacts.PREPARE_ARTIFACT_TYPE, artifacts.PREPARE_LAYER_MEDIA_TYPE,
            fetch_factory=lambda *_args: pytest.fail("push failure must not read back"))

    assert calls == ["login", "push"]


def test_oras_login_timeout_uses_and_exhausts_one_shared_retry_budget(monkeypatch):
    import subprocess
    from scripts.release_network_retry import RetryBudget

    now = [0.0]
    budget = RetryBudget(seconds=2, clock=lambda: now[0])
    monkeypatch.setattr(artifacts, "NETWORK_BUDGET", budget)
    calls = []

    def run(args, **kwargs):
        calls.append(kwargs["timeout"])
        now[0] += kwargs["timeout"]
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(artifacts.subprocess, "run", run)
    original_retry_network_read = artifacts.retry_network_read
    monkeypatch.setattr(artifacts, "retry_network_read", lambda *args, **kwargs: original_retry_network_read(
        *args, **kwargs, clock=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        jitter=lambda _low, _high: 0, emit=lambda _event: None))

    with pytest.raises(artifacts.ArtifactError, match="NETWORK_UNAVAILABLE"):
        artifacts._run_oras(["oras", "login", "ghcr.io"], input="fixture-secret-token")
    with pytest.raises(artifacts.ArtifactError, match="NETWORK_UNAVAILABLE"):
        artifacts._run_oras(["oras", "login", "ghcr.io"], input="fixture-secret-token")

    assert len(calls) > 1
    assert calls[0] < 2
    assert sum(calls) == pytest.approx(2)
    assert budget.spent == 2
    assert "fixture-secret-token" not in str(calls)


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


def test_ghcr_token_and_exact_digest_reads_retry_transient_tls_eof(monkeypatch):
    import ssl
    import urllib.error
    from scripts.release_network_retry import RetryBudget

    payload = b'exact receipt bytes'
    manifest, digest = oci_manifest(artifacts.PREPARE_ARTIFACT_TYPE,
        artifacts.PREPARE_LAYER_MEDIA_TYPE, payload)
    calls = {"token": 0, "tag": 0, "digest": 0, "blob": 0}

    class Response:
        def __init__(self, body, headers=None):
            self.body, self.headers = body, headers or {}
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self, _limit=-1): return self.body

    def opener(request, timeout):
        url = request.full_url
        if "/token?" in url:
            calls["token"] += 1
            if calls["token"] == 1:
                raise urllib.error.URLError(ssl.SSLEOFError("transient EOF"))
            return Response(b'{"token":"short-lived-test-token"}')
        if url.endswith(f"/manifests/{artifacts.artifact_tag('prepare', SHA, RUN_ID, ATTEMPT)}"):
            calls["tag"] += 1
            if calls["tag"] == 1:
                raise urllib.error.URLError(ssl.SSLEOFError("transient EOF"))
            return Response(manifest, {"Docker-Content-Digest": digest})
        if url.endswith(f"/manifests/{digest}"):
            calls["digest"] += 1
            return Response(manifest, {"Docker-Content-Digest": digest})
        if url.endswith("/blobs/" + json.loads(manifest)["layers"][0]["digest"]):
            calls["blob"] += 1
            return Response(payload)
        raise AssertionError("unexpected GHCR URL")

    retry = artifacts.retry_network_read
    monkeypatch.setattr(artifacts, "retry_network_read", lambda *args, **kwargs: retry(
        *args, **kwargs, sleep=lambda _seconds: None, jitter=lambda _low, _high: 0,
        emit=lambda _event: None))
    fetch = artifacts._registry_fetch(artifacts.PREPARE_REPOSITORY, "test-user", "secret-test",
        budget=RetryBudget(seconds=10), opener=opener)

    assert artifacts.resolve_oci_artifact(artifacts.PREPARE_REPOSITORY,
        artifacts.artifact_tag("prepare", SHA, RUN_ID, ATTEMPT),
        artifacts.PREPARE_ARTIFACT_TYPE, artifacts.PREPARE_LAYER_MEDIA_TYPE, fetch) == (digest, payload)
    assert calls == {"token": 2, "tag": 2, "digest": 1, "blob": 1}


def test_ghcr_authorization_failure_is_not_retried_or_disclosed(monkeypatch):
    import urllib.error
    from scripts.release_network_retry import RetryBudget

    calls = []
    def opener(request, timeout):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, 403, "private body", {}, None)

    fetch = artifacts._registry_fetch(artifacts.PREPARE_REPOSITORY, "test-user", "secret-test",
        budget=RetryBudget(seconds=10), opener=opener)
    with pytest.raises(artifacts.ArtifactError, match="HTTP 403") as error:
        fetch(f"https://ghcr.io/v2/akhripunov/maxibot-vps-api/manifests/prepare-{SHA}-{RUN_ID}-{ATTEMPT}",
            artifacts.OCI_ACCEPT)
    assert len(calls) == 1
    assert "private body" not in str(error.value)
    assert "secret-test" not in str(error.value)


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
    config_digest = "sha256:" + "c" * 64
    image_manifest = json.dumps({
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"mediaType": "application/vnd.oci.image.config.v1+json",
                    "digest": config_digest, "size": 1},
        "layers": [],
    }, separators=(",", ":")).encode()
    image_digest = "sha256:" + hashlib.sha256(image_manifest).hexdigest()
    image_reference = f"{artifacts.IMAGE_REPOSITORY}@{image_digest}"
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
        if fmt == "{{.Id}}": return image_digest
        if fmt == "{{range .RepoDigests}}{{println .}}{{end}}": return image_reference + "\n"
        if fmt == "{{.Os}}/{{.Architecture}}": return "linux/amd64"
        return json.dumps({"org.opencontainers.image.revision": SHA,
            "org.opencontainers.image.source-sha": SHA, "org.maxibot.release.sha": SHA})
    def registry_fetch(_repository, _username, _token):
        def fetch(url, _accept):
            assert url.endswith(f"/manifests/{image_digest}")
            return image_manifest, {"Docker-Content-Digest": image_digest}
        return fetch
    monkeypatch.setattr("scripts.ghcr_prepare_cache.subprocess.run", run)
    monkeypatch.setattr("scripts.ghcr_prepare_cache.subprocess.check_output", check_output)
    monkeypatch.setattr("scripts.ghcr_prepare_cache._registry_fetch", registry_fetch)
    image = {"repository": artifacts.IMAGE_REPOSITORY, "digest": "sha256:" + "b" * 64,
        "config_digest": config_digest, "platform": "linux/amd64", "labels": {
            "org.opencontainers.image.revision": SHA, "org.opencontainers.image.source-sha": SHA,
            "org.maxibot.release.sha": SHA}}
    image["digest"] = image_digest
    pull_verified_image(image, SHA, "owner", "secret-token")
    login = calls[0]
    assert login[0][1] == "login" and login[1]["input"] == "secret-token\n"
    assert "secret-token" not in " ".join(login[0])
    assert calls[-1][0][1] == "logout"
    assert all(call[1]["env"]["DOCKER_CONFIG"] == str(docker_config) for call in calls)
    assert not Path(docker_config).exists()


def test_ghcr_image_manifest_must_bind_receipt_config_digest(monkeypatch):
    config_digest = "sha256:" + "c" * 64
    manifest_bytes = json.dumps({
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"mediaType": "application/vnd.oci.image.config.v1+json",
                    "digest": config_digest, "size": 1},
        "layers": [],
    }, separators=(",", ":")).encode()
    image_digest = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    monkeypatch.setattr("scripts.ghcr_prepare_cache._registry_fetch", lambda *_args: (
        lambda *_request: (manifest_bytes, {"Docker-Content-Digest": image_digest})))
    image = {"repository": artifacts.IMAGE_REPOSITORY, "digest": image_digest,
        "config_digest": "sha256:" + "d" * 64}
    with pytest.raises(artifacts.ArtifactError, match="config descriptor"):
        verify_image_config_digest(image, "owner", "token")


def test_read_deploy_evidence_writes_verified_payload_to_destination(tmp_path, monkeypatch):
    evidence = {
        "schema": "MAXIBOT_RELEASE_WORKFLOW_RESULT_V1",
        "candidate_sha": SHA,
        "github_run_id": RUN_ID,
        "github_run_attempt": ATTEMPT,
    }
    payload = json.dumps(evidence).encode()
    digest = "sha256:" + "e" * 64
    monkeypatch.setattr(artifacts.sys, "argv", ["ghcr_release_artifacts.py", "read-evidence"])
    for key, value in {
        "CANDIDATE_SHA": SHA,
        "GITHUB_RUN_ID": RUN_ID,
        "GITHUB_RUN_ATTEMPT": ATTEMPT,
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "GITHUB_ACTOR": "owner",
        "GITHUB_TOKEN": "workflow-token",
        "DEPLOY_EVIDENCE_DIGEST": digest,
        "EVIDENCE_DESTINATION": str(tmp_path / "evidence"),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(artifacts, "_registry_fetch", lambda *_args: object())
    monkeypatch.setattr(artifacts, "resolve_oci_artifact", lambda *_args: (digest, payload))

    assert artifacts.main() == 0
    assert (tmp_path / "evidence" / "result.json").read_bytes() == payload


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
