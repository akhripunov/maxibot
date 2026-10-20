#!/usr/bin/env python3
"""Resolve the exact MAXIBOT Prepare OCI receipt by its verified digest."""

from __future__ import annotations

import json
import hashlib
import os
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
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "maxibot-prepare-result.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (destination / "maxibot-image-manifest.json").write_text(json.dumps({
        "schema": "MAXIBOT_PREPARED_IMAGE_V2", "candidate_sha": sha,
        "prepare_run_id": run_id, "prepare_run_attempt": attempt,
        "image": receipt["image"],
    }, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"tag": tag, "digest": digest, "image": receipt["image"]}


def verify_image_config_digest(image: dict, username: str, token: str) -> None:
    """Verify the OCI config descriptor; Docker image IDs vary by image store."""
    repository = image.get("repository")
    digest = image.get("digest")
    expected_config = image.get("config_digest")
    if (repository != IMAGE_REPOSITORY or not DIGEST.fullmatch(str(digest or ""))
            or not DIGEST.fullmatch(str(expected_config or ""))):
        raise ArtifactError("Prepare receipt has an invalid immutable image identity")
    repository_path = repository.split("/", 1)[1]
    manifest_url = f"https://ghcr.io/v2/{repository_path}/manifests/{digest}"
    manifest_bytes, headers = _registry_fetch(repository, username, token)(manifest_url, OCI_ACCEPT)
    if (hashlib.sha256(manifest_bytes).hexdigest() != digest[7:]
            or _header_value(headers, "Docker-Content-Digest") != digest):
        raise ArtifactError("GHCR image manifest digest differs from Prepare receipt")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactError("GHCR image manifest is invalid JSON") from exc
    if (not isinstance(manifest, dict) or manifest.get("schemaVersion") != 2
            or manifest.get("mediaType") not in OCI_ACCEPT.split(", ")
            or (manifest.get("config") or {}).get("digest") != expected_config):
        raise ArtifactError("GHCR image manifest config descriptor differs from Prepare receipt")


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


def main() -> int:
    try:
        result = resolve_prepare(os.environ["CANDIDATE_SHA"], os.environ["PREPARE_RUN_ID"],
            os.environ["PREPARE_RUN_ATTEMPT"], os.environ["PREPARE_RECEIPT_DIGEST"],
            os.environ["GITHUB_ACTOR"], os.environ["GITHUB_TOKEN"],
            Path(os.environ["ARTIFACT_DESTINATION"]))
        pull_verified_image(result["image"], os.environ["CANDIDATE_SHA"],
                            os.environ["GITHUB_ACTOR"], os.environ["GITHUB_TOKEN"])
        output = Path(os.environ["GITHUB_OUTPUT"])
        with output.open("a", encoding="utf-8") as stream:
            stream.write(f"prepare_receipt_digest={result['digest']}\n")
            stream.write(f"image_digest={result['image']['digest']}\n")
            stream.write(f"image_config_digest={result['image']['config_digest']}\n")
        print(f"MAXIBOT_GHCR_PREPARE_READBACK=PASS RECEIPT={result['digest']} IMAGE={result['image']['digest']}")
        return 0
    except (KeyError, OSError, ArtifactError) as exc:
        print(f"MAXIBOT_GHCR_PREPARE_READBACK=BLOCKED REASON={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
