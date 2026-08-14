"""Feature-gated sender for verified subscription conversions."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.integrations.yandex_metrica import YandexMetricaClient


@dataclass(frozen=True)
class ConversionSendResult:
    status: str
    reason: str | None = None
    response: dict | None = None


async def send_user_subscribed(
    client: YandexMetricaClient,
    *,
    counter_id: int,
    yclid: str | None,
    conversion_datetime: str,
    verified_membership: bool,
) -> ConversionSendResult:
    """Send only a verified, yclid-backed userDidSubscribe conversion."""
    if not verified_membership:
        return ConversionSendResult("blocked", "verified_membership_required")
    if not yclid:
        return ConversionSendResult("blocked", "yclid_required")
    if settings.yandex_conversion_dry_run:
        return ConversionSendResult("dry_run", "YANDEX_CONVERSION_DRY_RUN=true")
    response = await client.upload_offline_conversion(counter_id, yclid, "userDidSubscribe", conversion_datetime)
    return ConversionSendResult("sent", response=response)
