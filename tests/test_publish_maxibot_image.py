from __future__ import annotations

import json
import subprocess

import pytest

from scripts import publish_maxibot_image as publisher


SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
CONFIG = "sha256:" + "c" * 64


def metadata(image_id=CONFIG, architecture="amd64", labels=None):
    return [{"Id": image_id, "Os": "linux", "Architecture": architecture,
                 "Config": {"Labels": labels if labels is not None else {
                 "org.opencontainers.image.revision": SHA,
                 "org.opencontainers.image.source-sha": SHA,
                 "org.maxibot.release.sha": SHA,
             }}}]


def test_publish_pushes_unique_tag_pulls_digest_and_checks_remote_identity(tmp_path):
    calls = []
    inspections = iter([metadata(), metadata()])

    def run(args, *, input=None, env=None):
        calls.append((args, input))
        if args[:3] == ["docker", "image", "inspect"]:
            return json.dumps(next(inspections))
        if args[:2] == ["docker", "push"]:
            return f"latest: digest: {DIGEST} size: 123\n"
        return ""

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"candidate_sha": SHA, "prepare_run_id": "123",
                                    "prepare_run_attempt": "2", "image": f"maxibot-vps-api:{SHA}"}))
    output = tmp_path / "github-output"
    result = publisher.publish_image(SHA, "123", "2", manifest, output, run=run,
        environ={"GITHUB_TOKEN": "secret", "GITHUB_ACTOR": "owner"})

    assert result["digest"] == DIGEST
    assert result["config_digest"] == CONFIG
    logins = [c for c in calls if c[0][:2] == ["docker", "login"]]
    assert logins[0][0] == ["docker", "login", "ghcr.io", "--username", "owner", "--password-stdin"]
    assert logins[0][1] == "secret"
    assert ["docker", "pull", f"{publisher.IMAGE_REPOSITORY}@{DIGEST}"] in [c[0] for c in calls]
    assert "MAXIBOT_IMAGE_DIGEST=" + DIGEST in output.read_text()
    assert json.loads(manifest.read_text())["image"]["digest"] == DIGEST


@pytest.mark.parametrize("remote", [metadata(architecture="arm64"),
    metadata(image_id="sha256:" + "d" * 64), metadata(labels={})])
def test_publish_rejects_remote_image_that_differs_from_exact_linux_amd64_candidate(tmp_path, remote):
    inspections = iter([metadata(), remote])

    def run(args, *, input=None, env=None):
        if args[:3] == ["docker", "image", "inspect"]:
            return json.dumps(next(inspections))
        if args[:2] == ["docker", "push"]:
            return f"digest: {DIGEST}\n"
        return ""

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"candidate_sha": SHA, "prepare_run_id": "123",
                                    "prepare_run_attempt": "2", "image": f"maxibot-vps-api:{SHA}"}))
    with pytest.raises(publisher.PublishError):
        publisher.publish_image(SHA, "123", "2", manifest, tmp_path / "out", run=run,
            environ={"GITHUB_TOKEN": "secret", "GITHUB_ACTOR": "owner"})


def test_publish_requires_workflow_credentials_without_attempting_login(tmp_path):
    called = False

    def run(*_args, **_kwargs):
        nonlocal called
        called = True

    with pytest.raises(publisher.PublishError):
        publisher.publish_image(SHA, "123", "2", tmp_path / "manifest", tmp_path / "out",
            run=run, environ={})
    assert not called
