#!/usr/bin/env python3
"""Import a prepared archive and prove Docker's native runtime identity."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

try:
    from scripts.prepared_artifact_cache import checksum
    from scripts.prepared_image_archive import inspect_prepared_archive
except ModuleNotFoundError:
    from prepared_artifact_cache import checksum
    from prepared_image_archive import inspect_prepared_archive


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True, stderr=subprocess.STDOUT).strip()


def main() -> int:
    archive = Path(os.environ.get("IMAGE_ARCHIVE", ""))
    manifest_path = Path(os.environ.get("IMAGE_MANIFEST", ""))
    sha = os.environ.get("CANDIDATE_SHA", "")
    container = "maxibot-release-rehearsal"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        image = manifest["image"]
        image_id, platform = inspect_prepared_archive(archive, image, sha)
        actual_archive_sha = checksum(archive)
        if actual_archive_sha != manifest["archive_sha256"]:
            raise ValueError("prepared archive checksum differs from receipt")
        if image_id != manifest["image_id"]:
            raise ValueError("prepared archive config digest differs from receipt")
        docker("load", "--input", str(archive))
        native_id = docker("image", "inspect", image, "--format", "{{.Id}}")
        native_platform = docker("image", "inspect", image, "--format", "{{.Os}}/{{.Architecture}}")
        if native_id != image_id or native_platform != platform:
            raise ValueError("Docker native image identity differs from archive receipt")
        docker("create", "--name", container, "--network", "none", "--entrypoint", "python", image, "-c", "print('MAXIBOT_NATIVE_RUNTIME=PASS')")
        created_id = docker("inspect", container, "--format", "{{.Image}}")
        if created_id != native_id:
            raise ValueError("created container image identity differs from native image")
        print(f"PREPARED_IMAGE_REHEARSAL=PASS IMAGE_ID={native_id} PLATFORM={native_platform}")
        return 0
    except (OSError, KeyError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        reason = str(exc).replace("\n", " ")[:240]
        print(f"PREPARED_IMAGE_REHEARSAL=BLOCKED REASON={reason}", file=sys.stderr)
        return 1
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, text=True)


if __name__ == "__main__":
    raise SystemExit(main())
