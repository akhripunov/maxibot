from pathlib import Path

from app.ads.v2 import _media_identity
from app.ads.yandex_creative_media import archive_resolved_media


def test_periodic_entity_collection_uses_canonical_media_resolver():
    source = Path("app/ads/periodic.py").read_text()
    assert "enrich_periodic_ad_media" in source
    assert "resolve_media" not in source
    assert "_upsert_creative" in source


def test_media_identity_is_provider_reference_only():
    identity = _media_identity({"Id": 7, "Type": "TEXT_AD", "TextAd": {"AdImageHash": "img-1"}})
    assert identity == {"ad_image_hash": "img-1", "ad_image_hashes": [], "provider_creative_id": None, "video_extension": None, "media_kind": "IMAGE"}


async def _metadata_only_archive():
    return await archive_resolved_media({"result": "METADATA_ONLY", "content": None, "content_hash": None})


def test_metadata_only_media_is_not_marked_as_source_unavailable():
    import asyncio

    result = asyncio.run(_metadata_only_archive())
    assert result["state"] == "AVAILABLE_NOT_STORED"


def test_legacy_layout_is_migration_candidate_not_new_layout():
    source = Path("app/ads/v2.py").read_text()
    assert "copy_legacy_verified" in source
    assert "storage_key" in source
