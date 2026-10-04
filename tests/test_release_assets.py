from __future__ import annotations

import hashlib
import io
import json
import tarfile
import zipfile

import pytest

from scripts import release_assets


SHA = "a" * 40


def prepare_members(sha=SHA, run_id="1234", attempt="2", embedded_labels=None,
                    platform=("linux", "amd64")):
    image = f"maxibot-vps-api:{sha}"
    labels = {
        "org.opencontainers.image.revision": sha,
        "org.opencontainers.image.source-sha": sha,
        "org.maxibot.release.sha": sha,
    }
    config = json.dumps({"os": platform[0], "architecture": platform[1],
                         "config": {"Labels": embedded_labels or labels}},
                        sort_keys=True).encode()
    config_hash = hashlib.sha256(config).hexdigest()
    image_tar = io.BytesIO()
    with tarfile.open(fileobj=image_tar, mode="w:gz") as archive:
        manifest = json.dumps([{"Config": f"{config_hash}.json", "RepoTags": [image],
                               "Layers": []}]).encode()
        for name, body in (("manifest.json", manifest), (f"{config_hash}.json", config)):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    image_bytes = image_tar.getvalue()
    archive_digest = hashlib.sha256(image_bytes).hexdigest()
    manifest = {
        "schema": "MAXIBOT_PREPARED_IMAGE_V1", "candidate_sha": sha,
        "prepare_run_id": run_id, "prepare_run_attempt": attempt,
        "image": image, "image_id": "sha256:" + config_hash,
        "native_image_id": "sha256:" + "b" * 64,
        "archive_sha256": archive_digest, "platform": "linux/amd64", "labels": labels,
    }
    receipt = {
        "schema": "MAXIBOT_PREPARE_RECEIPT_V1", "candidate_sha": sha,
        "github_run_id": run_id, "github_run_attempt": attempt, "status": "READY",
        "tests": "success", "frozen_path_guard": "success", "linux_amd64_image": "success",
        "private_docker_rehearsal": "PASS", "rehearsal_candidate_sha": sha,
        "rehearsal_prepare_run_id": run_id, "rehearsal_archive_sha256": archive_digest,
    }
    return {
        f"maxibot-image-{sha}.tar.gz": image_bytes,
        "maxibot-image-manifest.json": json.dumps(manifest).encode(),
        "maxibot-prepare-result.json": json.dumps(receipt).encode(),
    }


def zipped(members):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return result.getvalue()


@pytest.mark.parametrize("kind", ["prepare", "evidence"])
def test_release_tag_binds_project_sha_run_and_attempt(kind):
    assert release_assets.release_tag(kind, SHA, 1234, 2) == f"maxibot-{kind}-{SHA}-1234-2"


@pytest.mark.parametrize(
    ("kind", "sha", "run_id", "attempt"),
    [("gpoint", SHA, 1, 1), ("prepare", "bad", 1, 1),
     ("prepare", SHA, 0, 1), ("prepare", SHA, 1, 0)],
)
def test_release_tag_rejects_invalid_or_foreign_identity(kind, sha, run_id, attempt):
    with pytest.raises(release_assets.ReleaseAssetError):
        release_assets.release_tag(kind, sha, run_id, attempt)


def test_resolve_asset_accepts_only_published_immutable_exact_commit_release():
    tag = release_assets.release_tag("prepare", SHA, 1234, 2)
    body = zipped(prepare_members())
    calls = []

    def get(route):
        calls.append(route)
        if route == f"releases/tags/{tag}":
            return {"tag_name": tag, "draft": False, "immutable": True, "assets": [{
                "id": 17, "name": "bundle.zip", "state": "uploaded", "size": len(body),
                "digest": "sha256:" + hashlib.sha256(body).hexdigest(),
            }]}
        if route == f"git/ref/tags/{tag}":
            return {"object": {"type": "commit", "sha": SHA}}
        raise AssertionError(route)

    asset = release_assets.resolve_asset("prepare", SHA, 1234, 2, "bundle.zip", get)
    assert asset["id"] == 17
    assert calls == [f"releases/tags/{tag}", f"git/ref/tags/{tag}"]


@pytest.mark.parametrize(
    "changes",
    [{"draft": True}, {"immutable": False},
     {"tag_name": "maxibot-prepare-" + "b" * 40 + "-1234-2"}],
)
def test_resolve_asset_rejects_draft_mutable_or_wrong_tag(changes):
    tag = release_assets.release_tag("prepare", SHA, 1234, 2)

    def get(route):
        if route == f"releases/tags/{tag}":
            value = {"tag_name": tag, "draft": False, "immutable": True, "assets": [{
                "id": 1, "name": "bundle.zip", "state": "uploaded", "size": 5,
                "digest": "sha256:" + "a" * 64,
            }]}
            value.update(changes)
            return value
        return {"object": {"type": "commit", "sha": SHA}}

    with pytest.raises(release_assets.ReleaseAssetError):
        release_assets.resolve_asset("prepare", SHA, 1234, 2, "bundle.zip", get)


def test_release_setting_must_be_confirmed_enabled_before_publish():
    release_assets.verify_immutable_releases_enabled(lambda _route: {"enabled": True})
    for value in ({"enabled": False}, {}, None):
        with pytest.raises(release_assets.ReleaseAssetError, match="not confirmed enabled"):
            release_assets.verify_immutable_releases_enabled(lambda _route, value=value: value)
    with pytest.raises(release_assets.ReleaseAssetError, match="unavailable"):
        release_assets.verify_immutable_releases_enabled(lambda _route: (_ for _ in ()).throw(PermissionError()))


def test_publish_stops_before_tag_or_draft_if_immutable_setting_is_unavailable(monkeypatch, tmp_path):
    asset = tmp_path / "bundle.zip"
    asset.write_bytes(b"bundle")
    monkeypatch.setenv("GITHUB_TOKEN", "contents-only-token")
    attempted_writes = []
    monkeypatch.setattr(release_assets, "_github_json", lambda _route: {"enabled": False})
    monkeypatch.setattr(release_assets, "_gh", lambda *args: attempted_writes.append(args))
    with pytest.raises(release_assets.ReleaseAssetError, match="not confirmed enabled"):
        release_assets._publish_release(
            release_assets.release_tag("prepare", SHA, 1234, 2), SHA, "1234", "2",
            asset, "bundle.zip", "prepare",
        )
    assert attempted_writes == []


def test_prepare_bundle_validates_exact_sha_attempt_digest_labels_and_platform():
    members = prepare_members()
    verified = release_assets.validate_prepare_bundle(members, SHA, "1234", "2")
    assert verified["manifest"]["platform"] == "linux/amd64"
    assert verified["image_sha256"] == hashlib.sha256(members[f"maxibot-image-{SHA}.tar.gz"]).hexdigest()


@pytest.mark.parametrize(
    ("mutation", "run_id", "attempt"),
    [("archive", "1234", "2"), ("platform", "1234", "2"),
     ("sha", "1234", "2"), ("attempt", "1234", "2"),
     ("image_id", "1234", "2"), ("corrupt_receipt", "1234", "2")],
)
def test_prepare_bundle_rejects_altered_bundle_and_forged_provenance(mutation, run_id, attempt):
    members = prepare_members()
    if mutation == "platform":
        value = json.loads(members["maxibot-image-manifest.json"])
        value["platform"] = "linux/arm64"
        members["maxibot-image-manifest.json"] = json.dumps(value).encode()
    elif mutation == "sha":
        value = json.loads(members["maxibot-image-manifest.json"])
        value["candidate_sha"] = "b" * 40
        members["maxibot-image-manifest.json"] = json.dumps(value).encode()
    elif mutation == "attempt":
        value = json.loads(members["maxibot-prepare-result.json"])
        value["github_run_attempt"] = "1"
        members["maxibot-prepare-result.json"] = json.dumps(value).encode()
    elif mutation == "image_id":
        value = json.loads(members["maxibot-image-manifest.json"])
        value["image_id"] = "sha256:" + "b" * 64
        members["maxibot-image-manifest.json"] = json.dumps(value).encode()
    elif mutation == "corrupt_receipt":
        members["maxibot-prepare-result.json"] = b"{broken json"
    else:
        members[f"maxibot-image-{SHA}.tar.gz"] = b"tampered"
    with pytest.raises(release_assets.ReleaseAssetError):
        release_assets.validate_prepare_bundle(members, SHA, run_id, attempt)


def test_prepare_bundle_rejects_forged_archive_image_labels_and_provider_platform():
    expected_labels = {
        "org.opencontainers.image.revision": SHA,
        "org.opencontainers.image.source-sha": SHA,
        "org.maxibot.release.sha": SHA,
    }
    forged = dict(expected_labels, **{"org.maxibot.release.sha": "b" * 40})
    with pytest.raises(release_assets.ReleaseAssetError, match="labels"):
        release_assets.validate_prepare_bundle(prepare_members(embedded_labels=forged), SHA, "1234", "2")
    with pytest.raises(release_assets.ReleaseAssetError, match="platform"):
        release_assets.validate_prepare_bundle(prepare_members(platform=("linux", "arm64")), SHA, "1234", "2")


def test_bundle_rejects_traversal_duplicate_and_unexpected_members():
    members = prepare_members()
    payload = zipped({**members, "../escape": b"bad"})
    with pytest.raises(release_assets.ReleaseAssetError, match="members"):
        release_assets._safe_read_zip(io.BytesIO(payload), tuple(members))
    duplicate = io.BytesIO()
    with zipfile.ZipFile(duplicate, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr(next(iter(members)), b"duplicate")
    with pytest.raises(release_assets.ReleaseAssetError, match="duplicate"):
        release_assets._safe_read_zip(io.BytesIO(duplicate.getvalue()), tuple(members))


def test_published_asset_size_constraints_fail_closed():
    tag = release_assets.release_tag("prepare", SHA, 1234, 2)

    def get(route):
        if route.startswith("releases/"):
            return {"tag_name": tag, "draft": False, "immutable": True, "assets": [{
                "id": 1, "name": "bundle.zip", "state": "uploaded",
                "size": release_assets.MAX_RELEASE_ASSET_BYTES,
                "digest": "sha256:" + "a" * 64,
            }]}
        return {"object": {"type": "commit", "sha": SHA}}

    with pytest.raises(release_assets.ReleaseAssetError, match="size"):
        release_assets.resolve_asset("prepare", SHA, 1234, 2, "bundle.zip", get)


def test_checksum_helper_streams_file(tmp_path):
    path = tmp_path / "bundle"
    path.write_bytes(b"release bytes")
    assert release_assets.checksum(path) == hashlib.sha256(b"release bytes").hexdigest()
