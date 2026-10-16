import pytest

from scripts.maxibot_ghcr import (validate_oci_image, create_vps_pull_script,
                                  select_image_transport, validate_rollback_image_identity,
                                  verify_ghcr_digest_access, REPOSITORY)


SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
CONFIG = "sha256:" + "c" * 64


def proof():
    return {
        "schema": "MAXIBOT_PREPARED_IMAGE_V2",
        "candidate_sha": SHA,
        "prepare_run_id": "123",
        "prepare_run_attempt": "1",
        "repository": "ghcr.io/akhripunov/maxibot-vps-api",
        "manifest_digest": DIGEST,
        "config_digest": CONFIG,
        "source_sha": SHA,
        "platform": "linux/amd64",
        "labels": {"org.opencontainers.image.revision": SHA,
                   "org.opencontainers.image.source-sha": SHA,
                   "org.maxibot.release.sha": SHA},
    }


def test_receipt_requires_exact_sha_attempt_digest_and_linux_amd64():
    assert validate_oci_image(proof(), SHA, "123", "1")["reference"].endswith("@" + DIGEST)
    with pytest.raises(ValueError, match="identity"):
        validate_oci_image(proof(), "d" * 40, "123", "1")
    bad = proof(); bad["manifest_digest"] = "latest"
    with pytest.raises(ValueError, match="digest"):
        validate_oci_image(bad, SHA, "123", "1")
    bad = proof(); bad["platform"] = "linux/arm64"
    with pytest.raises(ValueError, match="platform"):
        validate_oci_image(bad, SHA, "123", "1")


def test_pull_script_uses_digest_and_stdin_auth_without_secrets():
    script = create_vps_pull_script(validate_oci_image(proof(), SHA, "123", "1"), SHA)
    assert f"ghcr.io/akhripunov/maxibot-vps-api@{DIGEST}" in script
    assert "--password-stdin" in script
    assert "docker pull" in script
    assert "token" not in script.lower()
    assert "docker login ghcr.io --username 'akhripunov' --password-stdin" in script


def test_oci_archive_fallback_requires_explicit_selection_and_verified_archive_identity():
    manifest = proof() | {"transport": "ghcr-oci", "image_id": CONFIG,
                          "archive_sha256": "d" * 64}
    assert select_image_transport(manifest, "prepared") == "ghcr-oci"
    assert select_image_transport(manifest, "archive") == "archive"
    assert select_image_transport(manifest, "ghcr-oci") == "ghcr-oci"
    for key in ("image_id", "archive_sha256"):
        invalid = dict(manifest); invalid[key] = "missing"
        with pytest.raises(ValueError, match="fallback"):
            select_image_transport(invalid, "archive")


def test_archive_receipt_does_not_claim_ghcr_transport():
    manifest = {"transport": "archive"}
    assert select_image_transport(manifest, "prepared") == "archive"
    assert select_image_transport(manifest, "archive") == "archive"
    with pytest.raises(ValueError, match="proven"):
        select_image_transport(manifest, "ghcr-oci")


def test_rollback_requires_the_running_image_and_preserved_local_image_to_match():
    proof = validate_rollback_image_identity(
        CONFIG, CONFIG, "linux/amd64", [REPOSITORY_DIGEST := f"{REPOSITORY}@{DIGEST}"])
    assert proof["rollback_identity"] == "OCI_DIGEST_AND_CONFIG"
    assert proof["repo_digests"] == [REPOSITORY_DIGEST]
    with pytest.raises(ValueError, match="config digest"):
        validate_rollback_image_identity(CONFIG, DIGEST, "linux/amd64", [])
    with pytest.raises(ValueError, match="linux/amd64"):
        validate_rollback_image_identity(CONFIG, CONFIG, "linux/arm64", [])
    with pytest.raises(ValueError, match="RepoDigest"):
        validate_rollback_image_identity(CONFIG, CONFIG, "linux/amd64", ["maxibot:latest"])


def test_ghcr_readiness_authenticates_exact_digest_without_returning_token(monkeypatch):
    import json
    import urllib.error

    class Response:
        def __init__(self, status, headers, body=b"", url=""):
            self.headers, self.status, self.body, self.url = headers, status, body, url
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def geturl(self): return self.url
        def read(self, size=-1): return self.body[:size]

    seen = {}
    def opener(request, timeout):
        seen.setdefault("requests", []).append((request.full_url, request.get_header("Authorization"), timeout))
        if "/manifests/" in request.full_url and request.get_header("Authorization") is None:
            raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", {
                "WWW-Authenticate": 'Bearer realm="https://ghcr.io/token",service="ghcr.io",scope="repository:akhripunov/maxibot-vps-api:pull"'
            }, None)
        if request.full_url.startswith("https://ghcr.io/token?"):
            return Response(200, {}, json.dumps({"token": "bearer-secret"}).encode(), request.full_url)
        return Response(200, {"Docker-Content-Digest": DIGEST}, b"", request.full_url)

    assert verify_ghcr_digest_access(REPOSITORY, DIGEST, "akhripunov", "read-token", opener) == "PASS"
    requests = seen["requests"]
    assert len(requests) == 3
    assert requests[0][0] == f"https://ghcr.io/v2/akhripunov/maxibot-vps-api/manifests/{DIGEST}"
    assert requests[0][1] is None
    assert requests[1][0].startswith("https://ghcr.io/token?") and requests[1][1].startswith("Basic ")
    assert "read-token" not in requests[1][1]
    assert requests[2][0] == requests[0][0] and requests[2][1] == "Bearer bearer-secret"


def test_ghcr_readiness_rejects_unexpected_bearer_realm_or_scope():
    import urllib.error
    def wrong_scope(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", {
            "WWW-Authenticate": 'Bearer realm="https://evil.example/token",service="ghcr.io",scope="repository:akhripunov/maxibot-vps-api:pull"'
        }, None)
    with pytest.raises(RuntimeError, match="GHCR exact-digest read check failed"):
        verify_ghcr_digest_access(REPOSITORY, DIGEST, "akhripunov", "secret", wrong_scope)


def test_ghcr_readiness_fails_closed_for_auth_or_digest_mismatch():
    class Response:
        status = 200
        headers = {"Docker-Content-Digest": "sha256:" + "d" * 64}
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def geturl(self): return f"https://ghcr.io/v2/akhripunov/maxibot-vps-api/manifests/{DIGEST}"
        def read(self, _size=-1): return b""

    with pytest.raises(RuntimeError, match="GHCR exact-digest read check failed"):
        verify_ghcr_digest_access(REPOSITORY, DIGEST, "akhripunov", "secret", lambda *_a, **_k: Response())

    import urllib.error
    def denied(*_args, **_kwargs):
        raise urllib.error.HTTPError("url", 401, "unauthorized", {}, None)
    with pytest.raises(RuntimeError, match="GHCR exact-digest read check failed") as error:
        verify_ghcr_digest_access(REPOSITORY, DIGEST, "akhripunov", "secret", denied)
    assert "secret" not in str(error.value)


def test_default_ghcr_opener_rejects_redirects_before_following_them(monkeypatch):
    import urllib.error
    import urllib.request

    captured = {}

    class NoRedirectOpener:
        def __init__(self, handlers):
            captured["handler"] = handlers[0]

        def open(self, request, timeout):
            assert timeout == 10
            raise urllib.error.HTTPError(request.full_url, 302, "redirect", {}, None)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: NoRedirectOpener(handlers))
    with pytest.raises(RuntimeError, match="GHCR exact-digest read check failed"):
        verify_ghcr_digest_access(REPOSITORY, DIGEST, "akhripunov", "secret")
    assert captured["handler"].redirect_request(None, None, 302, "redirect", {}, None) is None
