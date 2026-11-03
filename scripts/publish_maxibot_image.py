"""Publish the already rehearsed MAXIBOT image and verify its GHCR digest."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path
from typing import Callable


IMAGE_REPOSITORY = "ghcr.io/akhripunov/maxibot-vps-api"
LABELS = ("org.opencontainers.image.revision", "org.opencontainers.image.source-sha",
          "org.maxibot.release.sha")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class PublishError(RuntimeError):
    pass


def _run(args: list[str], *, input: str | None = None, env: dict | None = None) -> str:
    try:
        result = subprocess.run(args, input=input, text=True, capture_output=True,
                                check=True, timeout=600, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        # Do not echo subprocess output or exception strings: docker auth/network
        # diagnostics can contain private registry data.
        raise PublishError(f"GHCR image {args[1] if len(args) > 1 else 'operation'} failed") from exc
    return result.stdout


def parse_push_digest(output: str) -> str:
    found = re.findall(r"\bdigest:\s*(sha256:[0-9a-f]{64})\b", output)
    if len(found) != 1:
        raise PublishError("GHCR push did not return one exact manifest digest")
    return found[0]


def _image_metadata(reference: str, run: Callable[..., str]) -> dict:
    try:
        value = json.loads(run(["docker", "image", "inspect", reference]))
        if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
            raise ValueError
        metadata = value[0]
        labels = metadata.get("Config", {}).get("Labels") or {}
        if (metadata.get("Os") != "linux" or metadata.get("Architecture") != "amd64"
                or not DIGEST.fullmatch(str(metadata.get("Id", "")))
                or not isinstance(labels, dict)):
            raise ValueError
        return {"config_digest": metadata["Id"], "platform": "linux/amd64",
                "labels": {label: labels.get(label) for label in LABELS}}
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise PublishError("GHCR image metadata is invalid or not linux/amd64") from exc


def publish_image(sha: str, run_id: str, attempt: str, manifest_path: Path,
                  output_path: Path, *, run: Callable[..., str] = _run,
                  environ: dict[str, str] | None = None) -> dict:
    env = dict(os.environ if environ is None else environ)
    token, username = env.get("GITHUB_TOKEN", ""), env.get("GITHUB_ACTOR", "")
    if (not SHA40.fullmatch(sha) or not re.fullmatch(r"[1-9][0-9]{0,19}", str(run_id))
            or not re.fullmatch(r"[1-9][0-9]{0,5}", str(attempt))):
        raise PublishError("Invalid exact MAXIBOT Prepare identity")
    if not token or not username:
        raise PublishError("Scoped workflow GHCR credentials are unavailable")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublishError("Built MAXIBOT image manifest is unavailable") from exc
    if (manifest.get("candidate_sha") != sha
            or str(manifest.get("prepare_run_id")) != str(run_id)
            or str(manifest.get("prepare_run_attempt")) != str(attempt)):
        raise PublishError("Built image manifest does not bind the exact Prepare run")
    local_reference = f"maxibot-vps-api:{sha}"
    local = _image_metadata(local_reference, run)
    if local["labels"] != {label: sha for label in LABELS}:
        raise PublishError("Built image source labels do not match the candidate SHA")
    tag = f"prepare-{sha}-{run_id}-{attempt}"
    reference = f"{IMAGE_REPOSITORY}:{tag}"
    docker_config = Path(tempfile.mkdtemp(prefix="maxibot-ghcr-publish-"))
    os.chmod(docker_config, 0o700)
    env["DOCKER_CONFIG"] = str(docker_config)
    try:
        run(["docker", "login", "ghcr.io", "--username", username, "--password-stdin"],
            input=token, env=env)
        run(["docker", "tag", local_reference, reference], env=env)
        digest = parse_push_digest(run(["docker", "push", reference], env=env))
        digest_reference = f"{IMAGE_REPOSITORY}@{digest}"
        run(["docker", "pull", digest_reference], env=env)
        remote = _image_metadata(digest_reference, run)
        if remote != local:
            raise PublishError("GHCR digest readback differs from the rehearsed MAXIBOT image")
        manifest["image"] = {"repository": IMAGE_REPOSITORY, "tag": tag,
                              "digest": digest, **remote}
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    finally:
        run(["docker", "logout", "ghcr.io"], env=env)
        shutil.rmtree(docker_config, ignore_errors=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as output:
        output.write(f"MAXIBOT_IMAGE_DIGEST={digest}\n")
        output.write(f"MAXIBOT_IMAGE_CONFIG_DIGEST={remote['config_digest']}\n")
        output.write(f"MAXIBOT_IMAGE_REPOSITORY={IMAGE_REPOSITORY}\n")
    print(f"MAXIBOT_GHCR_IMAGE=PASS DIGEST={digest} CONFIG={remote['config_digest']}")
    return manifest["image"]


def main() -> int:
    try:
        sha = os.environ["CANDIDATE_SHA"]
        manifest = Path(os.environ["IMAGE_MANIFEST"])
        output = Path(os.environ["GITHUB_OUTPUT"])
        publish_image(sha, os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"],
                      manifest, output)
        return 0
    except (KeyError, OSError, PublishError) as exc:
        print(f"MAXIBOT_GHCR_IMAGE=BLOCKED REASON={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
