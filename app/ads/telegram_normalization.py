"""Provider-neutral Telegram Ads normalization helpers.

Telegram self-serve exposes an ad as the smallest actionable optimization
scope.  Therefore the ad is represented by ``AdsCampaign``; ``AdsEntity`` is
reserved for subordinate inventory (targeting/placement/topic) when the
provider actually exposes one.  The same external ad is never duplicated as
both campaign and entity.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def campaign_name(raw: dict[str, Any]) -> str:
    return str(raw.get("Name") or raw.get("title") or raw.get("Id") or "").strip()


def normalize_creative(raw: dict[str, Any]) -> dict[str, Any]:
    observed = {"title": raw.get("title") or raw.get("Title"), "body": raw.get("text") or raw.get("Text"), "href": raw.get("promote_url") or raw.get("PromoteUrl"), "display_url": raw.get("website_name") or raw.get("WebsiteName"), "image_ref": raw.get("media") or raw.get("picture"), "provider_state": raw.get("Status") or raw.get("ModerationStatus")}
    observed = {key: value for key, value in observed.items() if value not in (None, "")}
    canonical = json.dumps(observed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**observed, "content_hash": hashlib.sha256(canonical.encode()).hexdigest(), "provider_metadata_json": json.dumps({"provider": "TELEGRAM_ADS", "observed_fields": sorted(observed)}, ensure_ascii=False, sort_keys=True)}


def normalize_stats(raw: dict[str, Any]) -> dict[str, Any]:
    """Preserve unavailable metrics as NULL rather than inventing zero."""
    mapping = {"impressions": "Impressions", "views": "Views", "clicks": "Clicks", "cost": "Cost", "spend": "Cost", "conversions": "Conversions", "cpm": "CPM"}
    result = {"impressions": None, "views": None, "clicks": None, "cost": None, "conversions": None, "cpm": None}
    for target, source in mapping.items():
        if source in raw and raw[source] is not None:
            result[target] = Decimal(str(raw[source]))
    return result


def logical_observation_key(*, account_id: str, external_scope_id: str, report_type: str, date_from: date | None, date_to: date | None) -> str:
    value = f"TELEGRAM_ADS|{account_id}|{external_scope_id}|{report_type}|{date_from}|{date_to}"
    return hashlib.sha256(value.encode()).hexdigest()[:64]


def data_health_for_error(error_code: str) -> tuple[str, str]:
    mapping = {"TELEGRAM_ADS_REAUTH_REQUIRED": ("DEGRADED", "SESSION_REAUTH_REQUIRED"), "TELEGRAM_ADS_OWNER_MISMATCH": ("DEGRADED", "OWNER_MISMATCH"), "TELEGRAM_ADS_OWNER_MARKER_MISSING": ("DEGRADED", "SESSION_UNKNOWN"), "TELEGRAM_ADS_ACCOUNT_SELECTOR_CHANGED": ("DEGRADED", "PROVIDER_CONTRACT_CHANGED"), "TELEGRAM_ADS_AD_SELECTOR_CHANGED": ("DEGRADED", "PROVIDER_CONTRACT_CHANGED")}
    return mapping.get(str(error_code), ("DEGRADED", "COLLECTION_FAILED"))
