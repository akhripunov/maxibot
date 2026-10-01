#!/usr/bin/env python3
"""Verify the immutable image artifact produced by the selected Prepare run."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    archive = Path(os.environ.get("IMAGE_ARCHIVE", ""))
    manifest_path = Path(os.environ.get("IMAGE_MANIFEST", ""))
    sha = os.environ.get("CANDIDATE_SHA", "")
    run_id = os.environ.get("PREPARE_RUN_ID", "")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        actual_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
        expected_labels = {
            "org.opencontainers.image.revision": sha,
            "org.opencontainers.image.source-sha": sha,
            "org.maxibot.release.sha": sha,
        }
        if (manifest.get("schema") != "MAXIBOT_PREPARED_IMAGE_V1"
                or manifest.get("candidate_sha") != sha
                or manifest.get("prepare_run_id") != run_id
                or manifest.get("platform") != "linux/amd64"
                or manifest.get("labels") != expected_labels
                or manifest.get("archive_sha256") != actual_hash):
            raise ValueError("prepared image manifest/archive provenance mismatch")
        image = manifest.get("image", "")
        if image != f"maxibot-vps-api:{sha}":
            raise ValueError("prepared image tag does not match candidate SHA")
        subprocess.run(["docker", "load", "--input", str(archive)], check=True)
        fmt = (
            "{{.Os}}/{{.Architecture}}|{{.Id}}|"
            "{{index .Config.Labels \"org.opencontainers.image.revision\"}}|"
            "{{index .Config.Labels \"org.opencontainers.image.source-sha\"}}|"
            "{{index .Config.Labels \"org.maxibot.release.sha\"}}"
        )
        inspected = subprocess.check_output(["docker", "image", "inspect", image, "--format", fmt], text=True).strip()
        if inspected != f"linux/amd64|{manifest['image_id']}|{sha}|{sha}|{sha}":
            raise ValueError("loaded image digest/platform/source labels do not match Prepare receipt")
        print(f"PREPARED_IMAGE=PASS SHA={sha} PREPARE_RUN={run_id} IMAGE_ID={manifest['image_id']} ARCHIVE_SHA256={actual_hash}")
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"PREPARED_IMAGE=BLOCKED REASON={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
