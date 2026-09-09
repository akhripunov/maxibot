import pytest
import httpx

from app.ads.creative_backfill import build_backfill_plan
from app.ads import creative_storage
from app.ads.creative_storage import describe_media, media_key, store_media_if_available


def test_media_key_is_content_addressed_and_bounded(monkeypatch):
    monkeypatch.setattr(creative_storage.settings, "mail_cloud_creative_root", "/MaxiBot ADS Creatives")
    digest = "a" * 64
    assert media_key(digest, ".jpg", mime_type="image/jpeg") == f"/MaxiBot ADS Creatives/images/{digest}.jpg"
    assert media_key(digest, ".mp4", mime_type="video/mp4") == f"/MaxiBot ADS Creatives/videos/{digest}.mp4"
    assert "/sha256/" not in media_key(digest, ".jpg", mime_type="image/jpeg")
    assert "/aa/" not in media_key(digest, ".jpg", mime_type="image/jpeg")
    assert media_key(digest, ".bin") == f"/MaxiBot ADS Creatives/other/{digest}.bin"
    for category in ("images", "videos", "other"):
        assert f"/{category}/" in media_key(digest, ".bin", mime_type={"images": "image/png", "videos": "video/mp4", "other": "application/octet-stream"}[category])
    with pytest.raises(ValueError):
        media_key("../" + "a" * 61, "jpg")


def test_describe_media_uses_sha256_and_size():
    descriptor = describe_media(b"creative", mime_type="image/png", extension="png")
    assert descriptor.content_hash
    assert descriptor.size == 8
    assert descriptor.key.endswith(".png")


def test_backfill_is_explicit_dry_run_and_never_invents_media():
    plan = build_backfill_plan([{"image_ref": "provider-ref"}, {"title": "text"}])
    assert plan["mode"] == "DRY_RUN"
    assert plan["mutations_performed"] == 0
    assert plan["source_unavailable"] == 1


@pytest.mark.asyncio
async def test_mail_failure_is_isolated_as_failed_media_state(monkeypatch):
    class BrokenStorage:
        enabled = True

        async def put_verified(self, *args, **kwargs):
            raise creative_storage.CreativeStorageError("temporary")

        async def close(self):
            return None

    monkeypatch.setattr(creative_storage, "MailCloudWebDav", BrokenStorage)
    result = await store_media_if_available(b"bytes", mime_type="image/png", extension="png")
    assert result["state"] == "FAILED"
    assert result["file_size"] == 5


@pytest.mark.asyncio
async def test_webdav_existing_directories_are_checked_before_mkcol(monkeypatch):
    monkeypatch.setattr(creative_storage.settings, "mail_cloud_enabled", True)
    monkeypatch.setattr(creative_storage.settings, "mail_cloud_webdav_url", "https://webdav.example")
    monkeypatch.setattr(creative_storage.settings, "mail_cloud_login", "owner@example.test")
    monkeypatch.setattr(creative_storage.settings, "mail_cloud_app_password", "redacted-test-secret")
    monkeypatch.setattr(creative_storage.settings, "mail_cloud_creative_root", "/MaxiBot ADS Creatives")
    uploaded = set()
    calls = []

    async def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "PROPFIND":
            return httpx.Response(207 if request.url.path in uploaded or not request.url.path.endswith(".png") else 404)
        if request.method == "PUT":
            uploaded.add(request.url.path)
            return httpx.Response(201)
        if request.method == "GET":
            return httpx.Response(200, content=b"payload")
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    storage = creative_storage.MailCloudWebDav(client=client)
    descriptor = await storage.put_verified(
        creative_storage.media_key("a" * 64, "png", mime_type="image/png"), b"payload", content_type="image/png"
    )
    await client.aclose()
    assert descriptor.key.endswith("/images/" + descriptor.content_hash + ".png")
    assert not any(method == "MKCOL" for method, _ in calls)
    assert [method for method, _ in calls].count("PROPFIND") >= 4
    assert all("%20" in storage._url(key) for method, key in [("PROPFIND", "/MaxiBot ADS Creatives")])


@pytest.mark.asyncio
async def test_webdav_upload_diagnostics_are_safe_and_verify_hash(monkeypatch):
    monkeypatch.setattr(creative_storage.settings, "mail_cloud_enabled", True)
    monkeypatch.setattr(creative_storage.settings, "mail_cloud_webdav_url", "https://webdav.example")
    monkeypatch.setattr(creative_storage.settings, "mail_cloud_login", "owner@example.test")
    monkeypatch.setattr(creative_storage.settings, "mail_cloud_app_password", "redacted-test-secret")
    monkeypatch.setattr(creative_storage.settings, "mail_cloud_creative_root", "/MaxiBot ADS Creatives")
    created = set()
    uploaded = set()

    async def handler(request):
        if request.method == "PROPFIND":
            return httpx.Response(207 if request.url.path in created or request.url.path in uploaded else 404)
        if request.method == "MKCOL":
            created.add(request.url.path)
            return httpx.Response(201)
        if request.method == "PUT":
            uploaded.add(request.url.path)
            return httpx.Response(201)
        if request.method == "GET":
            return httpx.Response(200, content=b"payload")
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    storage = creative_storage.MailCloudWebDav(client=client)
    await storage.put_verified(creative_storage.media_key("a" * 64, "png", mime_type="image/png"), b"payload", content_type="image/png")
    await client.aclose()
    assert {entry["operation"] for entry in storage.diagnostics} >= {"PROPFIND", "MKCOL", "PUT", "GET"}
    assert all("password" not in str(entry).lower() for entry in storage.diagnostics)
    assert all("Authorization" not in str(entry) for entry in storage.diagnostics)


@pytest.mark.asyncio
async def test_webdav_put_failure_exposes_safe_http_classification(monkeypatch):
    monkeypatch.setattr(creative_storage.settings, "mail_cloud_enabled", True)
    monkeypatch.setattr(creative_storage.settings, "mail_cloud_webdav_url", "https://webdav.example")
    monkeypatch.setattr(creative_storage.settings, "mail_cloud_login", "owner@example.test")
    monkeypatch.setattr(creative_storage.settings, "mail_cloud_app_password", "redacted-test-secret")
    monkeypatch.setattr(creative_storage.settings, "mail_cloud_creative_root", "/MaxiBot ADS Creatives")
    created = set()

    async def handler(request):
        if request.method == "PROPFIND":
            return httpx.Response(207 if request.url.path in created else 404)
        if request.method == "MKCOL":
            created.add(request.url.path)
            return httpx.Response(201)
        if request.method == "PUT":
            return httpx.Response(403)
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    storage = creative_storage.MailCloudWebDav(client=client)
    with pytest.raises(creative_storage.WebDavOperationError) as error:
        await storage.put_verified(creative_storage.media_key("b" * 64, "png", mime_type="image/png"), b"payload", content_type="image/png")
    await client.aclose()
    assert error.value.reason == "AUTH_FAILED"
    assert error.value.details["http_status"] == 403
    assert error.value.details["retryable"] is False
