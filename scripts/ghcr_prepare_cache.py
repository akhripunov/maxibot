#!/usr/bin/env python3
"""Resolve the exact MAXIBOT Prepare OCI receipt by its verified digest."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from scripts.ghcr_release_artifacts import (PREPARE_ARTIFACT_TYPE, PREPARE_LAYER_MEDIA_TYPE,
        PREPARE_REPOSITORY, ArtifactError, _registry_fetch, artifact_tag,
        resolve_oci_artifact, validate_prepare_receipt)
except ModuleNotFoundError:  # Direct script execution sets sys.path to scripts/.
    from ghcr_release_artifacts import (PREPARE_ARTIFACT_TYPE, PREPARE_LAYER_MEDIA_TYPE,
        PREPARE_REPOSITORY, ArtifactError, _registry_fetch, artifact_tag,
        resolve_oci_artifact, validate_prepare_receipt)


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
        subprocess.run([docker, "pull", reference], env=env, check=True)
        config = subprocess.check_output([docker, "image", "inspect", reference,
            "--format", "{{.Id}}"], env=env, text=True).strip()
        platform = subprocess.check_output([docker, "image", "inspect", reference,
            "--format", "{{.Os}}/{{.Architecture}}"], env=env, text=True).strip()
        labels = json.loads(subprocess.check_output([docker, "image", "inspect", reference,
            "--format", "{{json .Config.Labels}}"], env=env, text=True))
        expected_labels = {key: image.get("labels", {}).get(key)
            for key in ("org.opencontainers.image.revision", "org.opencontainers.image.source-sha",
                        "org.maxibot.release.sha")}
        if (config != image["config_digest"] or platform != "linux/amd64"
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
