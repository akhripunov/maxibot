#!/usr/bin/env python3
"""Resolve the exact MAXIBOT Prepare OCI receipt by its verified digest."""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    from scripts.ghcr_release_artifacts import (PREPARE_ARTIFACT_TYPE, PREPARE_LAYER_MEDIA_TYPE,
        PREPARE_REPOSITORY, IMAGE_REPOSITORY, OCI_ACCEPT, DIGEST, ArtifactError, NETWORK_BUDGET,
        _header_value, _registry_fetch, artifact_tag,
        resolve_oci_artifact, validate_prepare_receipt)
    from scripts.release_network_retry import NetworkUnavailable, RetryBudget, retry_network_read
except ModuleNotFoundError:  # Direct script execution sets sys.path to scripts/.
    from ghcr_release_artifacts import (PREPARE_ARTIFACT_TYPE, PREPARE_LAYER_MEDIA_TYPE,
        PREPARE_REPOSITORY, IMAGE_REPOSITORY, OCI_ACCEPT, DIGEST, ArtifactError, NETWORK_BUDGET,
        _header_value, _registry_fetch, artifact_tag,
        resolve_oci_artifact, validate_prepare_receipt)
    from release_network_retry import NetworkUnavailable, RetryBudget, retry_network_read


class _TransientDockerPullError(RuntimeError):
    pass


def _pull_image_by_digest(docker: str, reference: str, env: dict, *,
                          budget: RetryBudget | None = None,
                          run_command=None,
                          clock=None,
                          retry_options: dict | None = None) -> None:
    """Retry only transient failures while reading an immutable GHCR image digest."""
    retry_budget = budget or NETWORK_BUDGET
    run_command = run_command or subprocess.run
    clock = clock or time.monotonic
    deadline = clock() + retry_budget.remaining_seconds()
    transient_markers = (
        "tls handshake timeout", "ssl connection timeout", "unexpected eof",
        "connection reset", "connection refused", "connection timed out",
        "temporary failure in name resolution", "no such host", "network is unreachable",
        "no route to host", "context deadline exceeded", "i/o timeout",
    )

    def pull_once() -> None:
        remaining = min(retry_budget.remaining_seconds(), deadline - clock())
        if remaining <= 0:
            raise _TransientDockerPullError()
        try:
            run_command([docker, "pull", reference], env=env, check=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                        timeout=remaining)
        except subprocess.TimeoutExpired:
            raise _TransientDockerPullError() from None
        except subprocess.CalledProcessError as error:
            diagnostic = error.stderr or ""
            if any(marker in diagnostic.lower() for marker in transient_markers):
                raise _TransientDockerPullError() from None
            raise ArtifactError("GHCR image pull failed") from None

    retry_kwargs = dict(retry_options or {})
    retry_kwargs.setdefault("clock", clock)
    retry_kwargs.setdefault("deadline", deadline)
    try:
        retry_network_read(pull_once, "ghcr_image_pull",
            lambda error: isinstance(error, _TransientDockerPullError),
            budget=retry_budget, **retry_kwargs)
    except NetworkUnavailable:
        raise ArtifactError("NETWORK_UNAVAILABLE stage=ghcr_image_pull") from None


def resolve_prepare(sha: str, run_id: str, attempt: str, expected_digest: str,
                    username: str, token: str, destination: Path) -> dict:
    tag = artifact_tag("prepare", sha, run_id, attempt)
    digest, payload = resolve_oci_artifact(PREPARE_REPOSITORY, tag,
        PREPARE_ARTIFACT_TYPE, PREPARE_LAYER_MEDIA_TYPE,
        _registry_fetch(PREPARE_REPOSITORY, username, token))
    if digest != expected_digest:
        raise ArtifactError("GHCR Prepare receipt digest differs from trusted selection")
    try:
        receipt = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactError("GHCR Prepare receipt is invalid JSON") from exc
    validate_prepare_receipt(receipt, sha, run_id, attempt)
    verify_image_config_digest(receipt["image"], username, token, sha)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "maxibot-prepare-result.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (destination / "maxibot-image-manifest.json").write_text(json.dumps({
        "schema": "MAXIBOT_PREPARED_IMAGE_V2", "candidate_sha": sha,
        "prepare_run_id": run_id, "prepare_run_attempt": attempt,
        "image": receipt["image"],
    }, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"tag": tag, "digest": digest, "image": receipt["image"]}


def verify_image_config_digest(image: dict, username: str, token: str,
                               expected_sha: str | None = None) -> str:
    """Verify image manifest/config metadata without downloading image layers."""
    repository = image.get("repository")
    digest = image.get("digest")
    expected_config = image.get("config_digest")
    if (repository != IMAGE_REPOSITORY or not DIGEST.fullmatch(str(digest or ""))
            or not DIGEST.fullmatch(str(expected_config or ""))
            or image.get("platform") != "linux/amd64"):
        raise ArtifactError("Prepare receipt has an invalid immutable image identity")
    sha = expected_sha or image.get("labels", {}).get("org.opencontainers.image.revision", "")
    expected_labels = {
        "org.opencontainers.image.revision": sha,
        "org.opencontainers.image.source-sha": sha,
        "org.maxibot.release.sha": sha,
    }
    if (not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha)
            or image.get("labels") != expected_labels):
        raise ArtifactError("Prepare receipt image labels do not bind the exact candidate SHA")
    repository_path = repository.split("/", 1)[1]
    manifest_url = f"https://ghcr.io/v2/{repository_path}/manifests/{digest}"
    fetch = _registry_fetch(repository, username, token)
    manifest_bytes, headers = fetch(manifest_url, OCI_ACCEPT)
    if (hashlib.sha256(manifest_bytes).hexdigest() != digest[7:]
            or _header_value(headers, "Docker-Content-Digest") != digest):
        raise ArtifactError("GHCR image manifest digest differs from Prepare receipt")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactError("GHCR image manifest is invalid JSON") from exc
    config_descriptor = manifest.get("config") if isinstance(manifest, dict) else None
    layers = manifest.get("layers") if isinstance(manifest, dict) else None
    if (not isinstance(manifest, dict) or manifest.get("schemaVersion") != 2
            or manifest.get("mediaType") not in OCI_ACCEPT.split(", ")
            or not isinstance(config_descriptor, dict)
            or config_descriptor.get("mediaType") not in {
                "application/vnd.oci.image.config.v1+json",
                "application/vnd.docker.container.image.v1+json"}
            or config_descriptor.get("digest") != expected_config
            or type(config_descriptor.get("size")) is not int
            or not 0 < config_descriptor["size"] <= 2 * 1024 * 1024
            or not isinstance(layers, list)):
        raise ArtifactError("GHCR image manifest config descriptor differs from Prepare receipt")
    for layer in layers:
        if (not isinstance(layer, dict) or not DIGEST.fullmatch(str(layer.get("digest", "")))
                or type(layer.get("size")) is not int or layer.get("size", 0) <= 0
                or not isinstance(layer.get("mediaType"), str) or not layer["mediaType"]):
            raise ArtifactError("GHCR image manifest contains an invalid layer descriptor")
    config_url = f"https://ghcr.io/v2/{repository_path}/blobs/{expected_config}"
    config_bytes, _ = fetch(config_url, "application/octet-stream")
    if (len(config_bytes) != config_descriptor["size"]
            or hashlib.sha256(config_bytes).hexdigest() != expected_config[7:]):
        raise ArtifactError("GHCR image config blob digest or size differs from manifest")
    try:
        image_config = json.loads(config_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactError("GHCR image config blob is invalid JSON") from exc
    if (not isinstance(image_config, dict) or image_config.get("os") != "linux"
            or image_config.get("architecture") != "amd64"):
        raise ArtifactError("GHCR image config platform differs from linux/amd64")
    runtime_config = image_config.get("config")
    labels = runtime_config.get("Labels") if isinstance(runtime_config, dict) else None
    if labels != expected_labels:
        raise ArtifactError("GHCR image config source labels differ from Prepare receipt")
    rootfs = image_config.get("rootfs")
    diff_ids = rootfs.get("diff_ids") if isinstance(rootfs, dict) else None
    if (not isinstance(rootfs, dict) or rootfs.get("type") != "layers"
            or not isinstance(diff_ids, list) or len(diff_ids) != len(layers)
            or any(not isinstance(value, str) or not DIGEST.fullmatch(value) for value in diff_ids)):
        raise ArtifactError("GHCR image config rootfs metadata differs from image manifest")
    return "linux/amd64"


def pull_verified_image(image: dict, sha: str, username: str, token: str) -> None:
    """Pull the immutable image on the trusted Deploy runner with temporary auth."""
    docker = shutil.which("docker")
    if not docker or not username or not token:
        raise ArtifactError("Docker and scoped GHCR read credentials are required")
    docker_config = Path(tempfile.mkdtemp(prefix="maxibot-ghcr-auth-"))
    os.chmod(docker_config, 0o700)
    # An empty Docker config makes the macOS CLI select its default Keychain
    # helper. Headless Actions runners cannot unlock that helper; seed an auth
    # entry so Docker writes credentials only inside this private temp config.
    config_path = docker_config / "config.json"
    env = os.environ.copy()
    env["DOCKER_CONFIG"] = str(docker_config)
    reference = f"{image['repository']}@{image['digest']}"
    try:
        config_fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(config_fd, "w", encoding="utf-8") as config_file:
            json.dump({"auths": {"ghcr.io": {}}}, config_file)
        subprocess.run([docker, "login", "ghcr.io", "--username", username,
                        "--password-stdin"], input=token + "\n", text=True,
                       env=env, check=True, stdout=subprocess.DEVNULL)
        _pull_image_by_digest(docker, reference, env)
        verify_image_config_digest(image, username, token)
        repo_digests = subprocess.check_output([docker, "image", "inspect", reference,
            "--format", "{{range .RepoDigests}}{{println .}}{{end}}"], env=env, text=True).splitlines()
        platform = subprocess.check_output([docker, "image", "inspect", reference,
            "--format", "{{.Os}}/{{.Architecture}}"], env=env, text=True).strip()
        labels = json.loads(subprocess.check_output([docker, "image", "inspect", reference,
            "--format", "{{json .Config.Labels}}"], env=env, text=True))
        expected_labels = {key: image.get("labels", {}).get(key)
            for key in ("org.opencontainers.image.revision", "org.opencontainers.image.source-sha",
                        "org.maxibot.release.sha")}
        if (reference not in repo_digests or platform != "linux/amd64"
                or expected_labels != {key: sha for key in expected_labels}
                or any(labels.get(key) != sha for key in expected_labels)):
            raise ArtifactError("Pulled MAXIBOT image config, platform, or source labels differ from Prepare receipt")
        subprocess.run([docker, "tag", reference, f"maxibot-vps-api:{sha}"], env=env, check=True)
    finally:
        subprocess.run([docker, "logout", "ghcr.io"], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(docker_config, ignore_errors=True)


def image_preflight(sha: str, api_image: str, worker_image: str) -> int:
    try:
        if os.environ.get("MAXIBOT_IMAGE_TRANSPORT") != "GHCR_VPS_DIRECT":
            raise ArtifactError("Direct GHCR image transport was not authorized by the release wrapper")
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ArtifactError("Candidate SHA is invalid")
        expected_tag = f"maxibot-vps-api:{sha}"
        if api_image != expected_tag or worker_image != expected_tag:
            raise ArtifactError("API and worker must use the exact candidate image tag")
        receipt_path = Path(os.environ["RELEASE_PREPARE_RECEIPT"])
        if receipt_path.is_symlink() or not receipt_path.is_file() or not 0 < receipt_path.stat().st_size <= 2 * 1024 * 1024:
            raise ArtifactError("Exact Prepare receipt file is unavailable or unbounded")
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactError("Exact Prepare receipt file is invalid JSON") from exc
        run_id, attempt = os.environ["PREPARE_RUN_ID"], os.environ["PREPARE_RUN_ATTEMPT"]
        validate_prepare_receipt(receipt, sha, run_id, attempt)
        image = receipt["image"]
        expected = {
            "repository": os.environ["RELEASE_IMAGE_REPOSITORY"],
            "digest": os.environ["RELEASE_IMAGE_DIGEST"],
            "config_digest": os.environ["RELEASE_IMAGE_ID"],
        }
        if any(image.get(key) != value for key, value in expected.items()):
            raise ArtifactError("Selected GHCR image identity differs from exact Prepare receipt")
        verify_image_config_digest(image, os.environ["GITHUB_ACTOR"],
                                   os.environ["GITHUB_TOKEN"], sha)
        print("IMAGE_READY=YES")
        print(f"API_IMAGE_SHA={sha}")
        print(f"WORKER_IMAGE_SHA={sha}")
        print(f"IMAGE_ID={image['config_digest']}")
        print("IMAGE_PLATFORM=linux/amd64")
        return 0
    except (KeyError, OSError, ArtifactError, ValueError):
        print("IMAGE_PREFLIGHT=NO\nIMAGE_PREFLIGHT_REASON=trusted_oci_metadata_validation_failed", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "image-preflight":
        if len(argv) != 4:
            print("usage: ghcr_prepare_cache.py image-preflight SHA API_IMAGE WORKER_IMAGE", file=sys.stderr)
            return 2
        return image_preflight(argv[1], argv[2], argv[3])
    try:
        result = resolve_prepare(os.environ["CANDIDATE_SHA"], os.environ["PREPARE_RUN_ID"],
            os.environ["PREPARE_RUN_ATTEMPT"], os.environ["PREPARE_RECEIPT_DIGEST"],
            os.environ["GITHUB_ACTOR"], os.environ["GITHUB_TOKEN"],
            Path(os.environ["ARTIFACT_DESTINATION"]))
        output = Path(os.environ["GITHUB_OUTPUT"])
        with output.open("a", encoding="utf-8") as stream:
            stream.write(f"prepare_receipt_digest={result['digest']}\n")
            stream.write(f"image_digest={result['image']['digest']}\n")
            stream.write(f"image_config_digest={result['image']['config_digest']}\n")
        print(f"MAXIBOT_GHCR_PREPARE_READBACK=PASS RECEIPT={result['digest']} IMAGE={result['image']['digest']} CONFIG={result['image']['config_digest']} PLATFORM=linux/amd64")
        return 0
    except (KeyError, OSError, ArtifactError) as exc:
        print(f"MAXIBOT_GHCR_PREPARE_READBACK=BLOCKED REASON={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
