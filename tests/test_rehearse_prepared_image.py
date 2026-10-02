import json
from pathlib import Path

import scripts.rehearse_prepared_image as rehearsal


def test_rehearsal_rejects_native_store_identity_mismatch(monkeypatch, tmp_path: Path):
    archive = tmp_path / "image.tar.gz"
    manifest = tmp_path / "manifest.json"
    archive.write_bytes(b"archive")
    manifest.write_text(json.dumps({
        "image": "maxibot-vps-api:" + "a" * 40,
        "image_id": "sha256:" + "1" * 64,
        "archive_sha256": rehearsal.checksum(archive),
    }))
    monkeypatch.setenv("IMAGE_ARCHIVE", str(archive))
    monkeypatch.setenv("IMAGE_MANIFEST", str(manifest))
    monkeypatch.setenv("CANDIDATE_SHA", "a" * 40)
    monkeypatch.setattr(rehearsal, "inspect_prepared_archive", lambda *_: ("sha256:" + "1" * 64, "linux/amd64"))
    monkeypatch.setattr(rehearsal, "docker", lambda *args: "sha256:" + "2" * 64 if args[:3] == ("image", "inspect", manifest.read_text() and "maxibot-vps-api:" + "a" * 40) else "")
    monkeypatch.setattr(rehearsal.subprocess, "run", lambda *args, **kwargs: None)
    assert rehearsal.main() == 1
