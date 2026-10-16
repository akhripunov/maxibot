#!/usr/bin/env python3
"""Publish the already-built MAXIBOT image and bind its exact GHCR digest."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

SHA40 = re.compile(r"^[a-f0-9]{40}$")
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
REPOSITORY = "ghcr.io/akhripunov/maxibot-vps-api"


def parse_push_digest(output: str) -> str:
    matches = set(re.findall(r"\bdigest:\s*(sha256:[a-f0-9]{64})\b", output))
    if len(matches) != 1:
        raise ValueError("GHCR push did not return one exact manifest digest")
    return matches.pop()


def validate_remote_metadata(metadata: dict, sha: str) -> str:
    if (metadata.get("Os") != "linux" or metadata.get("Architecture") != "amd64"
            or not DIGEST.fullmatch(metadata.get("Id", ""))):
        raise ValueError("GHCR image metadata is not the exact linux/amd64 config")
    labels = (metadata.get("Config") or {}).get("Labels") or {}
    expected = {"org.opencontainers.image.revision": sha,
                "org.opencontainers.image.source-sha": sha,
                "org.maxibot.release.sha": sha}
    if any(labels.get(key) != value for key, value in expected.items()):
        raise ValueError("GHCR image labels do not match the candidate SHA")
    return metadata["Id"]


def augment_manifest(path: Path, sha: str, run_id: str, attempt: str,
                     digest: str, config_digest: str) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if (manifest.get("schema") != "MAXIBOT_PREPARED_IMAGE_V1"
            or manifest.get("candidate_sha") != sha
            or str(manifest.get("prepare_run_id")) != str(run_id)
            or str(manifest.get("prepare_run_attempt")) != str(attempt)):
        raise ValueError("Prepare manifest identity changed before GHCR publication")
    if not DIGEST.fullmatch(digest) or not DIGEST.fullmatch(config_digest):
        raise ValueError("GHCR digest is invalid")
    updated = {**manifest, "schema": "MAXIBOT_PREPARED_IMAGE_V2",
               "repository": REPOSITORY, "manifest_digest": digest,
               "config_digest": config_digest, "source_sha": sha,
               "platform": "linux/amd64", "transport": "ghcr-oci",
               "labels": manifest.get("labels", {})}
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(updated, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return updated


def run(*args: str, capture: bool = False) -> str:
    result = subprocess.run(args, check=True, text=True,
                            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                            stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
                            timeout=900)
    return (result.stdout or "") + (result.stderr or "")


def publish() -> None:
    sha = os.environ.get("CANDIDATE_SHA", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    if (os.environ.get("GITHUB_REPOSITORY") != "akhripunov/maxibot"
            or os.environ.get("GITHUB_SHA") != sha or not SHA40.fullmatch(sha)
            or not run_id.isdigit() or not attempt.isdigit()):
        raise ValueError("GHCR publication requires exact trusted MAXIBOT Prepare identity")
    image = f"maxibot-vps-api:{sha}"
    tag = f"prepare-{sha}-{run_id}-{attempt}"
    remote_tag = f"{REPOSITORY}:{tag}"
    run("docker", "tag", image, remote_tag)
    digest = parse_push_digest(run("docker", "push", remote_tag, capture=True))
    reference = f"{REPOSITORY}@{digest}"
    run("docker", "pull", reference, capture=True)
    metadata = json.loads(run("docker", "image", "inspect", reference, capture=True))[0]
    config_digest = validate_remote_metadata(metadata, sha)
    manifest = Path(os.environ["RUNNER_TEMP"]) / "maxibot-image" / "maxibot-image-manifest.json"
    augment_manifest(manifest, sha, run_id, attempt, digest, config_digest)
    print("MAXIBOT_GHCR_PROVENANCE=PASS platform=linux/amd64")


if __name__ == "__main__":
    try:
        publish()
    except Exception as error:
        # Docker output may contain registry metadata; keep the public failure terse.
        print(f"MAXIBOT_GHCR_PROVENANCE=BLOCKED error={type(error).__name__}", file=sys.stderr)
        raise SystemExit(1)
