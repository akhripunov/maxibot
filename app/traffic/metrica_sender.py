"""Feature-gated sender for verified subscription conversions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.integrations.yandex_metrica import YandexMetricaClient


@dataclass(frozen=True)
class ConversionSendResult:
    status: str
    reason: str | None = None
    response: dict | None = None


METRICA_VISIT_LINKAGE_WINDOW = timedelta(days=21)


def _visit_window_expired(visit_at: datetime | None, *, now: datetime | None = None) -> bool:
    """A missing timestamp is not proof of expiry; otherwise compare UTC instants."""
    if visit_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    visit_time = visit_at
    if visit_time.tzinfo is None:
        visit_time = visit_time.replace(tzinfo=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc) - visit_time.astimezone(timezone.utc) > METRICA_VISIT_LINKAGE_WINDOW


async def send_user_subscribed(
    client: YandexMetricaClient,
    *,
    counter_id: int,
    yclid: str | None,
    client_id: str | None,
    conversion_datetime: str,
    verified_membership: bool,
    visit_at: datetime | None,
) -> ConversionSendResult:
    """Send only a verified Direct conversion with a Metrica session ID."""
    if not verified_membership:
        return ConversionSendResult("blocked", "verified_membership_required")
    if not yclid:
        return ConversionSendResult("blocked", "yclid_required")
    if not client_id:
        return ConversionSendResult("blocked", "metrica_client_id_required")
    if visit_at is None:
        return ConversionSendResult("blocked", "metrica_visit_timestamp_missing")
    if _visit_window_expired(visit_at):
        return ConversionSendResult("blocked", "metrica_visit_window_expired")
    if settings.yandex_conversion_dry_run:
        return ConversionSendResult("dry_run", "YANDEX_CONVERSION_DRY_RUN=true")
    goal_identifier = await client.ensure_action_goal_identifier(counter_id, "userDidSubscribe")
    # Goal resolution can itself take time. Recheck immediately before the
    # conversion POST so the guard reflects the outbound upload boundary.
    if _visit_window_expired(visit_at):
        return ConversionSendResult("blocked", "metrica_visit_window_expired")
    response = await client.upload_offline_conversion(counter_id, yclid, client_id, goal_identifier, conversion_datetime)
    return ConversionSendResult("sent", response=response)


async def send_user_unsubscribed(
    client: YandexMetricaClient, *, counter_id: int, yclid: str | None,
    client_id: str | None, conversion_datetime: str, verified_membership: bool,
    visit_at: datetime | None,
) -> ConversionSendResult:
    """Send only an exact, previously confirmed membership removal."""
    if not verified_membership:
        return ConversionSendResult("blocked", "verified_membership_required")
    if not yclid:
        return ConversionSendResult("blocked", "yclid_required")
    if not client_id:
        return ConversionSendResult("blocked", "metrica_client_id_required")
    if visit_at is None:
        return ConversionSendResult("blocked", "metrica_visit_timestamp_missing")
    if _visit_window_expired(visit_at):
        return ConversionSendResult("blocked", "metrica_visit_window_expired")
    if settings.yandex_conversion_dry_run:
        return ConversionSendResult("dry_run", "YANDEX_CONVERSION_DRY_RUN=true")
    goal_identifier = await client.ensure_action_goal_identifier(counter_id, "userDidUnsubscribe")
    if _visit_window_expired(visit_at):
        return ConversionSendResult("blocked", "metrica_visit_window_expired")
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
    visit_at: datetime | None,
) -> ConversionSendResult:
    """Backward-compatible name; always send the userDidSubscribe goal."""
    return await send_user_subscribed(
        client,
        counter_id=counter_id,
        yclid=yclid,
        client_id=client_id,
        conversion_datetime=conversion_datetime,
        verified_membership=exact_membership,
        visit_at=visit_at,
    )
