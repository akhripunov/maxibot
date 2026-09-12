"""Destination-platform contracts for the provider-neutral ADS core.

The advertising provider (for example Yandex Direct) is deliberately kept
separate from the destination platform (MAX, Telegram, ...).  Platform
adapters translate their native identity/events into these small immutable
contracts before they enter the decision, experiment, or memory engines.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

DESTINATION_PLATFORMS = {"MAX", "TELEGRAM", "WEBSITE", "APP"}
PROMOTION_OBJECT_TYPES = {"CHANNEL", "BOT"}


@dataclass(frozen=True)
class PromotionObjectRef:
    platform: str
    object_type: str
    object_id: str

    def __post_init__(self) -> None:
        platform = str(self.platform or "").strip().upper()
        object_type = str(self.object_type or "").strip().upper()
        object_id = str(self.object_id or "").strip()
        if platform not in DESTINATION_PLATFORMS:
            raise ValueError("ADS_DESTINATION_PLATFORM_UNSUPPORTED")
        if object_type not in PROMOTION_OBJECT_TYPES:
            raise ValueError("ADS_PROMOTION_OBJECT_TYPE_UNSUPPORTED")
        if not object_id:
            raise ValueError("ADS_PROMOTION_OBJECT_ID_REQUIRED")
        object.__setattr__(self, "platform", platform)
        object.__setattr__(self, "object_type", object_type)
        object.__setattr__(self, "object_id", object_id)


@dataclass(frozen=True)
class BusinessOutcome:
    """Normalized business result consumed by ADS decision/evaluation code."""

    platform: str
    object_type: str
    object_id: str
    outcome_type: str
    occurred_at: datetime | None = None
    attributed_campaign: str | None = None
    value: float | int = 1
    attribution_quality: str = "UNRESOLVED"

    def __post_init__(self) -> None:
        ref = PromotionObjectRef(self.platform, self.object_type, self.object_id)
        object.__setattr__(self, "platform", ref.platform)
        object.__setattr__(self, "object_type", ref.object_type)
        object.__setattr__(self, "object_id", ref.object_id)
        if not str(self.outcome_type or "").strip():
            raise ValueError("ADS_BUSINESS_OUTCOME_TYPE_REQUIRED")


def outcome_type_for(platform: str, object_type: str) -> str:
    """Return the platform adapter's canonical outcome name."""
    key = (str(platform or "").strip().upper(), str(object_type or "").strip().upper())
    values = {
        ("MAX", "CHANNEL"): "max_subscriber",
        ("MAX", "BOT"): "bot_started",
        ("TELEGRAM", "CHANNEL"): "telegram_subscriber",
        ("TELEGRAM", "BOT"): "telegram_bot_started",
    }
    try:
        return values[key]
    except KeyError as exc:
        raise ValueError("ADS_BUSINESS_OUTCOME_UNSUPPORTED") from exc


def promotion_ref_from_link(link: Any) -> PromotionObjectRef:
    """Resolve a persisted link without assuming its provider is its platform."""
    platform = getattr(link, "destination_platform", None) or "MAX"
    object_type = str(getattr(link, "object_type", "") or "").upper()
    object_id = getattr(link, "channel_id", None) if object_type == "CHANNEL" else getattr(link, "bot_promotion_profile_id", None)
    return PromotionObjectRef(platform, object_type, str(object_id or ""))


def normalize_max_outcome(*, object_type: str, object_id: str, occurred_at: datetime | None = None,
                          attributed_campaign: str | None = None, value: int = 1,
                          attribution_quality: str = "EXACT") -> BusinessOutcome:
    """MAX adapter boundary; source events remain in their existing tables."""
    return BusinessOutcome(
        platform="MAX", object_type=object_type, object_id=object_id,
        outcome_type=outcome_type_for("MAX", object_type), occurred_at=occurred_at,
        attributed_campaign=attributed_campaign, value=value,
        attribution_quality=attribution_quality,
    )

