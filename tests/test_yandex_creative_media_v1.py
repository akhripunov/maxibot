import json

import httpx
import pytest

from app.ads.yandex_creative_media import classify_media_source, extract_media_references, resolve_media, summarize_retrieval
from app.integrations.yandex_direct import YandexDirectClient


def test_retrieval_summary_is_dry_run_and_counts_media_classes():
    summary = summarize_retrieval(
        campaigns=[{"Id": 1}],
        ads=[{"Id": 7, "AdImageHash": "h"}, {"Id": 8, "CreativeId": 9}, {"Id": 10}],
        resolved=[
            {"ad_image_hash": "h", "result": "AVAILABLE", "media_source_type": "ORIGINAL", "content": b"12"},
            {"provider_creative_id": 9, "result": "UNAVAILABLE", "content": None},
            {"result": "METADATA_ONLY", "content": None},
        ],
    )
    assert summary["campaigns"] == 1
    assert summary["image_ads"] == 1
    assert summary["video_ads"] == 1
    assert summary["media_retrievable"] == 1
    assert summary["estimated_bytes"] == 2
    assert summary["mutations_performed"] == 0


@pytest.mark.asyncio
async def test_direct_media_methods_use_read_only_get_payloads():
    requests = []

    def handler(request):
        requests.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith("/adimages"):
            return httpx.Response(200, json={"result": {"AdImages": [{"AdImageHash": "h", "OriginalUrl": "https://media/original"}]}})
        return httpx.Response(200, json={"result": {"Creatives": [{"Id": 9, "Type": "VIDEO_EXT"}]}})

    client = YandexDirectClient("redacted", base_url="https://direct.invalid")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://direct.invalid")
    try:
        images = await client.get_ad_images(["h"])
        creatives = await client.get_creatives([9])
    finally:
        await client.close()
    assert images[0]["OriginalUrl"] == "https://media/original"
    assert creatives[0]["Id"] == 9
    assert [path for path, _ in requests] == ["/adimages", "/creatives"]
    assert all(payload["method"] == "get" for _, payload in requests)


@pytest.mark.asyncio
async def test_ads_media_fields_are_type_specific_not_invalid_top_level_fields():
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"result": {"Ads": [{"Id": 1, "CampaignId": 2, "Type": "TEXT_IMAGE_AD", "TextImageAd": {"AdImageHash": "h"}}]}})

    client = YandexDirectClient("redacted", base_url="https://direct.invalid")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://direct.invalid")
    try:
        ads = await client.get_ads([2])
    finally:
        await client.close()
    params = captured[0]["params"]
    assert "AdImageHash" not in params["FieldNames"]
    assert "OriginalUrl" not in params["FieldNames"]
    assert "AdImageHash" in params["TextImageAdFieldNames"]
    assert "AdImageHash" in params["TextAdFieldNames"]
    assert ads[0]["TextImageAd"]["AdImageHash"] == "h"


def test_ads_get_image_hash_is_resolved_as_image_reference():
    refs = extract_media_references({"Id": 7, "Type": "TEXT_IMAGE_AD", "AdImageHash": "hash-1"})
    assert refs["ad_id"] == 7
    assert refs["ad_image_hash"] == "hash-1"
    assert refs["media_kind"] == "IMAGE"


def test_responsive_ad_image_hashes_are_resolved_as_image_references():
    refs = extract_media_references({"Id": 8, "Type": "RESPONSIVE_AD", "ResponsiveAd": {"AdImageHashes": ["hash-1", "hash-2"]}})
    assert refs["ad_image_hash"] == "hash-1"
    assert refs["ad_image_hashes"] == ["hash-1", "hash-2"]
    assert refs["media_kind"] == "IMAGE"


def test_responsive_ad_images_items_are_resolved_as_image_references():
    refs = extract_media_references({"Id": 9, "Type": "RESPONSIVE_AD", "ResponsiveAd": {"AdImages": {"Items": [{"ImageHash": "hash-3", "Status": "ACCEPTED"}]}}})
    assert refs["ad_image_hash"] == "hash-3"
    assert refs["ad_image_hashes"] == ["hash-3"]
    assert refs["media_kind"] == "IMAGE"


def test_media_source_precedence_is_original_then_preview_then_thumbnail():
    assert classify_media_source(original_url="https://x/original", preview_url="https://x/preview", thumbnail_url=None) == "ORIGINAL"
    assert classify_media_source(original_url=None, preview_url="https://x/preview", thumbnail_url="https://x/thumb") == "PREVIEW"
    assert classify_media_source(original_url=None, preview_url=None, thumbnail_url="https://x/thumb") == "THUMBNAIL"
    assert classify_media_source(original_url=None, preview_url=None, thumbnail_url=None) == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_adimages_original_is_downloaded_and_hashed():
    class Provider:
        async def get_ad_images(self, hashes):
            return [{"AdImageHash": hashes[0], "OriginalUrl": "https://media/original.png", "PreviewUrl": "https://media/preview.png"}]

        async def get_creatives(self, ids):
            return []

    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"original-bytes"))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await resolve_media(Provider(), {"Id": 7, "AdImageHash": "hash-1"}, http_client=client)
    assert result["media_source_type"] == "ORIGINAL"
    assert result["original_retrievable"] is True
    assert result["content"] == b"original-bytes"
    assert result["content_hash"]


@pytest.mark.asyncio
async def test_preview_fallback_is_not_called_original():
    class Provider:
        async def get_ad_images(self, hashes):
            return [{"AdImageHash": hashes[0], "PreviewUrl": "https://media/preview.png"}]

        async def get_creatives(self, ids):
            return []

    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"preview-bytes"))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await resolve_media(Provider(), {"Id": 8, "AdImageHash": "hash-2"}, http_client=client)
    assert result["media_source_type"] == "PREVIEW"
    assert result["original_retrievable"] is False
    assert result["preview_retrievable"] is True


@pytest.mark.asyncio
async def test_creative_id_metadata_only_is_not_fake_video_original():
    class Provider:
        async def get_ad_images(self, hashes):
            return []

        async def get_creatives(self, ids):
            return [{"Id": ids[0], "Type": "VIDEO_EXT"}]

    result = await resolve_media(Provider(), {"Id": 9, "CreativeId": 123, "VideoExtension": {"Id": 123}})
    assert result["media_source_type"] == "UNAVAILABLE"
    assert result["result"] == "METADATA_ONLY"
    assert result["original_retrievable"] is False
