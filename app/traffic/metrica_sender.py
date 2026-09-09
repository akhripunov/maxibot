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
    client_id: str | None,
    conversion_datetime: str,
    verified_membership: bool,
) -> ConversionSendResult:
    """Send only a verified Direct conversion with a Metrica session ID."""
    if not verified_membership:
        return ConversionSendResult("blocked", "verified_membership_required")
    if not yclid:
        return ConversionSendResult("blocked", "yclid_required")
    if not client_id:
        return ConversionSendResult("blocked", "metrica_client_id_required")
    if settings.yandex_conversion_dry_run:
        return ConversionSendResult("dry_run", "YANDEX_CONVERSION_DRY_RUN=true")
    goal_identifier = await client.ensure_action_goal_identifier(counter_id, "userDidSubscribe")
    response = await client.upload_offline_conversion(counter_id, yclid, client_id, goal_identifier, conversion_datetime)
    return ConversionSendResult("sent", response=response)


async def send_max_subscriber(
    client: YandexMetricaClient,
    *,
    counter_id: int,
    yclid: str | None,
    client_id: str | None,
    conversion_datetime: str,
    exact_membership: bool,
) -> ConversionSendResult:
    """Backward-compatible name; always send the userDidSubscribe goal."""
    return await send_user_subscribed(
        client,
        counter_id=counter_id,
        yclid=yclid,
        client_id=client_id,
        conversion_datetime=conversion_datetime,
        verified_membership=exact_membership,
    )
