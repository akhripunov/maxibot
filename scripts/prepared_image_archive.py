"""Read and verify image identity directly from a Docker save archive."""

from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path


IMAGE_LABELS = (
    "org.opencontainers.image.revision",
    "org.opencontainers.image.source-sha",
    "org.maxibot.release.sha",
)


def inspect_prepared_archive(archive: Path, image: str, sha: str) -> tuple[str, str]:
    """Return config digest and platform, rejecting any archive provenance mismatch."""
    with tarfile.open(archive, "r:*") as image_tar:
        try:
            manifest_member = image_tar.getmember("manifest.json")
        except KeyError as exc:
            raise ValueError("Docker archive has no manifest.json") from exc
        manifest_file = image_tar.extractfile(manifest_member)
        if manifest_file is None:
            raise ValueError("Docker archive manifest.json is unreadable")
        entries = json.load(manifest_file)
        if not isinstance(entries, list) or len(entries) != 1:
            raise ValueError("Docker archive must contain exactly one image")
        entry = entries[0]
        if image not in (entry.get("RepoTags") or []):
            raise ValueError("Docker archive tag does not match candidate image")
        config_name = entry.get("Config")
        if not isinstance(config_name, str):
            raise ValueError("Docker archive has no config reference")
        try:
            config_member = image_tar.getmember(config_name)
        except KeyError as exc:
            raise ValueError("Docker archive config is missing") from exc
        config_file = image_tar.extractfile(config_member)
        if config_file is None:
            raise ValueError("Docker archive config is unreadable")
        config_bytes = config_file.read()
        config_id = "sha256:" + hashlib.sha256(config_bytes).hexdigest()
        config = json.loads(config_bytes)

    platform = f"{config.get('os', '')}/{config.get('architecture', '')}"
    if platform != "linux/amd64":
        raise ValueError(f"Docker archive platform is {platform!r}, expected 'linux/amd64'")
    labels = (config.get("config") or {}).get("Labels") or {}
    for label in IMAGE_LABELS:
        if labels.get(label) != sha:
            raise ValueError(f"Docker archive label {label!r} does not match candidate SHA")
    return config_id, platform
