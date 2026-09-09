"""Read-only Yandex Direct creative/media reference resolution."""
from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any
from urllib.parse import urlparse

import httpx

from app.ads.creative_storage import store_media_if_available

MEDIA_FIELDS = ("AdImageHash", "AdImageHashes", "CreativeId", "VideoExtension", "ThumbnailUrl", "PreviewUrl", "OriginalUrl")


def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def extract_media_references(ad: dict[str, Any]) -> dict[str, Any]:
    """Extract documented references without treating a URL as canonical storage."""
    refs: dict[str, Any] = {key: None for key in MEDIA_FIELDS}
    refs["ad_id"] = ad.get("Id")
    refs["ad_type"] = ad.get("Type") or ad.get("Subtype")
    refs["provider_ad_id"] = ad.get("Id")
    refs["provider_creative_id"] = ad.get("CreativeId")
    refs["ad_image_hash"] = ad.get("AdImageHash")
    refs["video_extension"] = ad.get("VideoExtension")
    containers = (ad, "TextAd", "TextImageAd", "MobileAppAd", "MobileAppImageAd", "DynamicTextAd", "ResponsiveAd", "CpcVideoAd")
    for container in (ad if isinstance(ad, dict) else {}, *(ad.get(key) if isinstance(ad.get(key), dict) else {} for key in containers[1:])):
        if not isinstance(container, dict):
            continue
        image_hashes = container.get("AdImageHashes")
        if isinstance(image_hashes, list):
            refs["ad_image_hashes"] = [str(value) for value in image_hashes if value]
            if refs.get("ad_image_hash") is None and refs["ad_image_hashes"]:
                refs["ad_image_hash"] = refs["ad_image_hashes"][0]
        for key in MEDIA_FIELDS:
            if refs.get(key) is None and container.get(key) is not None:
                refs[key] = _first(container.get(key))
        ad_images = container.get("AdImages")
        if isinstance(ad_images, dict):
            ad_images = ad_images.get("Items")
        if isinstance(ad_images, list):
            hashes = [item.get("AdImageHash") or item.get("ImageHash") for item in ad_images if isinstance(item, dict) and (item.get("AdImageHash") or item.get("ImageHash"))]
            if hashes and refs.get("ad_image_hash") is None:
                refs["ad_image_hash"] = str(hashes[0])
                refs["ad_image_hashes"] = [str(value) for value in hashes]
        if refs.get("ad_image_hash") is None and container.get("AdImageHash"):
            refs["ad_image_hash"] = str(container["AdImageHash"])
    if refs.get("ad_image_hash"):
        refs["media_kind"] = "IMAGE"
    elif refs.get("provider_creative_id") or refs.get("video_extension"):
        refs["media_kind"] = "VIDEO_OR_CREATIVE"
    else:
        refs["media_kind"] = "OTHER"
    return refs


def classify_media_source(*, original_url: str | None, preview_url: str | None, thumbnail_url: str | None) -> str:
    if original_url:
        return "ORIGINAL"
    if preview_url:
        return "PREVIEW"
    if thumbnail_url:
        return "THUMBNAIL"
    return "UNAVAILABLE"


def summarize_retrieval(*, campaigns: list[dict[str, Any]], ads: list[dict[str, Any]], resolved: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a bounded, mutation-free media retrieval dry-run summary."""
    kinds = Counter()
    for ad in ads:
        kind = extract_media_references(ad).get("media_kind")
        kinds["image_ads" if kind == "IMAGE" else "video_ads" if kind == "VIDEO_OR_CREATIVE" else "other_creative_types"] += 1
    counts = Counter(item.get("result") for item in resolved)
    return {
        "campaigns": len(campaigns),
        "ads": len(ads),
        "image_ads": kinds["image_ads"],
        "video_ads": kinds["video_ads"],
        "other_creative_types": kinds["other_creative_types"],
        "adimage_hashes_found": sum(bool(item.get("ad_image_hash")) for item in resolved),
        "creative_ids_found": sum(bool(item.get("provider_creative_id")) for item in resolved),
        "original_urls_found": sum(item.get("media_source_type") == "ORIGINAL" for item in resolved),
        "preview_urls_found": sum(item.get("media_source_type") == "PREVIEW" for item in resolved),
        "media_retrievable": counts["AVAILABLE"],
        "media_metadata_only": counts["METADATA_ONLY"],
        "media_unavailable": counts["UNAVAILABLE"],
        "media_failed": counts["FAILED"],
        "estimated_files": counts["AVAILABLE"],
        "estimated_bytes": sum(len(item.get("content") or b"") for item in resolved),
        "mode": "DRY_RUN",
        "mutations_performed": 0,
    }


async def resolve_media(provider_client: Any, ad: dict[str, Any], *, http_client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    refs = extract_media_references(ad)
    image_rows = await provider_client.get_ad_images([refs["ad_image_hash"]]) if refs.get("ad_image_hash") else []
    creative_rows = await provider_client.get_creatives([refs["provider_creative_id"]]) if refs.get("provider_creative_id") else []
    provider_media = next(
        (row for row in [*image_rows, *creative_rows] if any(row.get(key) for key in ("OriginalUrl", "PreviewUrl", "ThumbnailUrl"))),
        (image_rows[0] if image_rows else creative_rows[0] if creative_rows else {}),
    )
    original_url = provider_media.get("OriginalUrl") or refs.get("OriginalUrl")
    preview_url = provider_media.get("PreviewUrl") or refs.get("PreviewUrl")
    thumbnail_url = provider_media.get("ThumbnailUrl") or refs.get("ThumbnailUrl")
    source_type = classify_media_source(original_url=original_url, preview_url=preview_url, thumbnail_url=thumbnail_url)
    selected_url = original_url or preview_url or thumbnail_url
    result: dict[str, Any] = {
        **refs,
        "provider": "YANDEX_DIRECT",
        "provider_media_reference": selected_url,
        "media_source_type": source_type,
        "original_retrievable": False,
        "preview_retrievable": False,
        "result": "METADATA_ONLY" if not selected_url and (refs.get("ad_image_hash") or refs.get("provider_creative_id") or refs.get("video_extension")) else "UNAVAILABLE" if not selected_url else "METADATA_ONLY",
        "content": None,
    }
    if not selected_url:
        return result
    client = http_client or httpx.AsyncClient(timeout=20, follow_redirects=True)
    owned = http_client is None
    try:
        response = await client.get(selected_url)
        response.raise_for_status()
        content = response.content
        if not content:
            result["result"] = "FAILED"
            result["error"] = "EMPTY_MEDIA"
            return result
        result["content"] = content
        result["mime_type"] = response.headers.get("content-type", "").split(";", 1)[0] or "application/octet-stream"
        result["result"] = "AVAILABLE"
        result["content_hash"] = hashlib.sha256(content).hexdigest()
        result["original_retrievable"] = bool(original_url)
        result["preview_retrievable"] = bool(preview_url)
        return result
    except (httpx.HTTPError, ValueError) as exc:
        result["result"] = "FAILED"
        result["error"] = type(exc).__name__
        return result
    finally:
        if owned:
            await client.aclose()


async def archive_resolved_media(resolved: dict[str, Any]) -> dict[str, Any]:
    content = resolved.get("content") if isinstance(resolved.get("content"), bytes) else None
    if not content:
        state = {"METADATA_ONLY": "AVAILABLE_NOT_STORED", "UNAVAILABLE": "SOURCE_UNAVAILABLE"}.get(resolved.get("result"), "FAILED")
        return {"state": state, "storage_key": None, "file_size": None, "content_hash": resolved.get("content_hash")}
    extension = "bin"
    parsed = urlparse(str(resolved.get("provider_media_reference") or ""))
    if "." in parsed.path.rsplit("/", 1)[-1]:
        extension = parsed.path.rsplit("/", 1)[-1].rsplit(".", 1)[-1][:12]
    if extension == "bin":
        extension = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp", "video/mp4": "mp4", "video/webm": "webm"}.get(str(resolved.get("mime_type") or "").lower(), extension)
    if resolved.get("result") not in {"AVAILABLE"}:
        return {"state": "FAILED" if resolved.get("result") == "FAILED" else "SOURCE_UNAVAILABLE", "storage_key": None, "file_size": None, "content_hash": resolved.get("content_hash")}
    return await store_media_if_available(content, mime_type=resolved.get("mime_type") or "application/octet-stream", extension=extension)
