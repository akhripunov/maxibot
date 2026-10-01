import hashlib
import io
import json
import tarfile

import pytest

from scripts.prepared_image_archive import inspect_prepared_archive


SHA = "a" * 40
IMAGE = f"maxibot-vps-api:{SHA}"


def make_archive(path, *, platform="linux/amd64", labels=None, tag=IMAGE):
    labels = labels or {
        "org.opencontainers.image.revision": SHA,
        "org.opencontainers.image.source-sha": SHA,
        "org.maxibot.release.sha": SHA,
    }
    os_name, architecture = platform.split("/", 1)
    config = json.dumps({
        "os": os_name,
        "architecture": architecture,
        "config": {"Labels": labels},
    }, separators=(",", ":")).encode()
    config_name = f"{hashlib.sha256(config).hexdigest()}.json"
    manifest = json.dumps([{
        "Config": config_name,
        "RepoTags": [tag],
        "Layers": [],
    }]).encode()
    with tarfile.open(path, "w:") as archive:
        for name, data in (("manifest.json", manifest), (config_name, config)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return "sha256:" + hashlib.sha256(config).hexdigest()


def test_prepared_archive_verifies_config_digest_platform_and_source_labels(tmp_path):
    archive = tmp_path / "image.tar"
    expected_id = make_archive(archive)

    image_id, platform = inspect_prepared_archive(archive, IMAGE, SHA)

    assert image_id == expected_id
    assert platform == "linux/amd64"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"platform": "linux/arm64"}, "platform"),
        ({"tag": "maxibot-vps-api:" + "b" * 40}, "tag"),
        ({"labels": {"org.opencontainers.image.revision": "b" * 40}}, "label"),
    ],
)
def test_prepared_archive_fails_closed_on_platform_tag_or_label_mismatch(tmp_path, kwargs, message):
    archive = tmp_path / "image.tar"
    make_archive(archive, **kwargs)

    with pytest.raises(ValueError, match=message):
        inspect_prepared_archive(archive, IMAGE, SHA)


def test_prepared_archive_rejects_multiple_images(tmp_path):
    archive = tmp_path / "image.tar"
    make_archive(archive)
    with tarfile.open(archive, "r:") as source:
        config = next(member for member in source.getmembers()
                      if member.name.endswith(".json") and member.name != "manifest.json")
        config_bytes = source.extractfile(config).read()
        manifest_bytes = source.extractfile("manifest.json").read()
    entries = json.loads(manifest_bytes)
    entries.append(entries[0])
    with tarfile.open(archive, "w:") as target:
        for name, data in (("manifest.json", json.dumps(entries).encode()), (config.name, config_bytes)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            target.addfile(info, io.BytesIO(data))

    with pytest.raises(ValueError, match="exactly one image"):
        inspect_prepared_archive(archive, IMAGE, SHA)
