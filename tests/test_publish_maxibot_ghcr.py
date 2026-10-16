import json
from pathlib import Path

import pytest

from scripts.publish_maxibot_ghcr import augment_manifest, parse_push_digest, validate_remote_metadata


SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
CONFIG = "sha256:" + "c" * 64


def metadata():
    return {"Id": CONFIG, "Os": "linux", "Architecture": "amd64", "Config": {"Labels": {
        "org.opencontainers.image.revision": SHA,
        "org.opencontainers.image.source-sha": SHA,
        "org.maxibot.release.sha": SHA,
    }}}


def test_push_digest_requires_single_exact_digest():
    assert parse_push_digest(f"digest: {DIGEST}") == DIGEST
    with pytest.raises(ValueError):
        parse_push_digest("digest: latest")
    with pytest.raises(ValueError):
        parse_push_digest(f"digest: {DIGEST}\ndigest: sha256:{'d' * 64}")


def test_remote_metadata_binds_registry_candidate_and_platform():
    assert validate_remote_metadata(metadata(), SHA) == CONFIG
    bad = metadata()
    bad["Architecture"] = "arm64"
    with pytest.raises(ValueError, match="metadata"):
        validate_remote_metadata(bad, SHA)
    bad = metadata()
    bad["Config"]["Labels"]["org.opencontainers.image.revision"] = "d" * 40
    with pytest.raises(ValueError, match="labels"):
        validate_remote_metadata(bad, SHA)


def test_manifest_augmentation_keeps_archive_fallback_and_exact_prepare_identity(tmp_path: Path):
    path = tmp_path / "manifest.json"
    source = {"schema": "MAXIBOT_PREPARED_IMAGE_V1", "candidate_sha": SHA,
              "prepare_run_id": "123", "prepare_run_attempt": "1",
              "archive_sha256": "e" * 64, "image_id": CONFIG}
    path.write_text(json.dumps(source))
    result = augment_manifest(path, SHA, "123", "1", DIGEST, CONFIG)
    assert result["schema"] == "MAXIBOT_PREPARED_IMAGE_V2"
    assert result["repository"] == "ghcr.io/akhripunov/maxibot-vps-api"
    assert result["manifest_digest"] == DIGEST
    assert result["archive_sha256"] == "e" * 64
    assert json.loads(path.read_text()) == result
    with pytest.raises(ValueError, match="identity"):
        augment_manifest(path, "d" * 40, "123", "1", DIGEST, CONFIG)
