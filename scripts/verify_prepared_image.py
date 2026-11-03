#!/usr/bin/env python3
"""Verify the immutable image artifact produced by the selected Prepare run."""

from __future__ import annotations

try:
    from scripts.prepared_artifact_cache import checksum
except ModuleNotFoundError:
    from prepared_artifact_cache import checksum
import json
import os
import sys
import tarfile
from pathlib import Path

try:
    from scripts.prepared_image_archive import inspect_prepared_archive
except ModuleNotFoundError:  # Direct script execution sets sys.path to scripts/.
    from prepared_image_archive import inspect_prepared_archive


def main() -> int:
    archive = Path(os.environ.get("IMAGE_ARCHIVE", ""))
    manifest_path = Path(os.environ.get("IMAGE_MANIFEST", ""))
    sha = os.environ.get("CANDIDATE_SHA", "")
    run_id = os.environ.get("PREPARE_RUN_ID", "")
    attempt = os.environ.get("PREPARE_RUN_ATTEMPT", "")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        actual_hash = checksum(archive)
        expected_labels = {
            "org.opencontainers.image.revision": sha,
            "org.opencontainers.image.source-sha": sha,
            "org.maxibot.release.sha": sha,
        }
        if (manifest.get("schema") != "MAXIBOT_PREPARED_IMAGE_V1"
                or manifest.get("candidate_sha") != sha
                or manifest.get("prepare_run_id") != run_id
                or str(manifest.get("prepare_run_attempt")) != attempt
                or manifest.get("platform") != "linux/amd64"
                or manifest.get("labels") != expected_labels
                or manifest.get("archive_sha256") != actual_hash):
            raise ValueError("prepared image manifest/archive provenance mismatch")
        image = manifest.get("image", "")
        if image != f"maxibot-vps-api:{sha}":
            raise ValueError("prepared image tag does not match candidate SHA")
        image_id, platform = inspect_prepared_archive(archive, image, sha)
        if image_id != manifest.get("image_id"):
            raise ValueError(
                f"archive config digest differs from Prepare receipt: "
                f"expected={manifest.get('image_id')!r} actual={image_id!r}"
            )
        if platform != manifest["platform"]:
            raise ValueError("archive platform differs from Prepare receipt")
        print(f"PREPARED_IMAGE=PASS SHA={sha} PREPARE_RUN={run_id} IMAGE_ID={manifest['image_id']} ARCHIVE_SHA256={actual_hash}")
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError, tarfile.TarError) as exc:
        print(f"PREPARED_IMAGE=BLOCKED REASON={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
