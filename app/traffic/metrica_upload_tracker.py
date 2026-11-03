"""Reconcile asynchronous Metrica offline-upload statuses without re-uploading."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import and_, or_, select, update

from app.db.models import Admin, Channel, OfflineConversion, TrafficMembership
from app.integrations.yandex_metrica import YandexMetricaClient
from app.integrations.yandex_oauth import get_encrypted_token
from app.security_tokens import decrypt_token


logger = logging.getLogger(__name__)

MAX_CHECKS_PER_RUN = 100
OBSERVATION_PERIOD = timedelta(days=30)
UPLOADED_BACKOFF_SECONDS = (60, 300, 900, 3600, 21600, 43200)
LINKAGE_FAILURE_BACKOFF_SECONDS = (21600, 43200, 86400)
TECHNICAL_ERROR_BACKOFF_SECONDS = (300, 900, 3600, 21600, 86400)


def _backoff_seconds(status: str, attempts: int, *, technical_error: bool = False) -> int:
    schedule = (
        TECHNICAL_ERROR_BACKOFF_SECONDS if technical_error
        else LINKAGE_FAILURE_BACKOFF_SECONDS if status == "linkage_failure"
        else UPLOADED_BACKOFF_SECONDS
    )
    return schedule[min(max(attempts - 1, 0), len(schedule) - 1)]


def _error_category(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP_{exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "TIMEOUT"
    if isinstance(exc, httpx.RequestError):
        return "NETWORK_ERROR"
    return "CLIENT_ERROR"


def _utc_datetime(value: datetime) -> datetime:
    """Normalize SQLite's naive timestamp round-trip to the application's UTC convention."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def _save_check_result(
    session_factory,
    *,
    conversion_id: str,
    previous_status: str,
    upload_id: str,
    counter_id: str,
    checked_at: datetime,
    previous_checked_at: datetime | None,
    observation_started_at: datetime | None,
    attempts: int,
    provider_status: str | None = None,
) -> bool:
    """Compare-and-set so a stale poller cannot overwrite a newer result."""
    next_attempt = attempts + 1
    effective_observation_start = observation_started_at or checked_at
    values = {
        OfflineConversion.metrica_upload_checked_at: checked_at,
        OfflineConversion.metrica_upload_observation_started_at: effective_observation_start,
        OfflineConversion.metrica_upload_check_attempts: next_attempt,
        OfflineConversion.metrica_upload_check_error: None,
    }

    if provider_status == "PROCESSED":
        values.update({
            OfflineConversion.status: "sent",
            OfflineConversion.metrica_upload_status: provider_status,
            OfflineConversion.metrica_upload_next_check_at: None,
            OfflineConversion.last_error: None,
            OfflineConversion.sent_at: checked_at,
        })
        if previous_status == "linkage_failure":
            values[OfflineConversion.metrica_upload_recovered_at] = checked_at
    elif provider_status == "LINKAGE_FAILURE":
        values.update({
            OfflineConversion.status: "linkage_failure",
            OfflineConversion.metrica_upload_status: provider_status,
            OfflineConversion.metrica_upload_next_check_at: checked_at + timedelta(
                seconds=_backoff_seconds("linkage_failure", next_attempt)
            ),
            OfflineConversion.last_error: "metrica_linkage_failure",
        })
    elif provider_status in {"UPLOADED", "PROCESSING"}:
        # A provider-side transition away from LINKAGE_FAILURE is not a
        # conversion success, but it does make the previous error stale.
        values.update({
            OfflineConversion.status: "uploaded",
            OfflineConversion.metrica_upload_status: provider_status,
            OfflineConversion.metrica_upload_next_check_at: checked_at + timedelta(
                seconds=_backoff_seconds("uploaded", next_attempt)
            ),
            OfflineConversion.last_error: None,
        })
    else:
        values.update({
            OfflineConversion.metrica_upload_status: (provider_status or "UNKNOWN")[:32],
            OfflineConversion.metrica_upload_next_check_at: checked_at + timedelta(
                seconds=_backoff_seconds(previous_status, next_attempt, technical_error=True)
            ),
            OfflineConversion.metrica_upload_check_error: "UNKNOWN_PROVIDER_STATUS",
        })

    checked_at_predicate = (
        OfflineConversion.metrica_upload_checked_at.is_(None)
        if previous_checked_at is None
        else OfflineConversion.metrica_upload_checked_at == previous_checked_at
    )
    async with session_factory() as db:
        result = await db.execute(
            update(OfflineConversion)
            .where(
                OfflineConversion.id == conversion_id,
                OfflineConversion.status == previous_status,
                OfflineConversion.metrica_upload_id == upload_id,
                OfflineConversion.metrica_upload_counter_id == counter_id,
                checked_at_predicate,
            )
            .values(**{
                column.key: value for column, value in values.items()
            })
        )
        await db.commit()
        return result.rowcount == 1


async def _record_unverifiable(
    session_factory,
    *,
    conversion_id: str,
    previous_status: str,
    upload_id: str,
    expected_counter_id: str | None,
    previous_checked_at: datetime | None,
    category: str,
    checked_at: datetime,
) -> None:
    checked_at_predicate = (
        OfflineConversion.metrica_upload_checked_at.is_(None)
        if previous_checked_at is None
        else OfflineConversion.metrica_upload_checked_at == previous_checked_at
    )
    async with session_factory() as db:
        await db.execute(
            update(OfflineConversion)
            .where(
                OfflineConversion.id == conversion_id,
                OfflineConversion.status == previous_status,
                OfflineConversion.metrica_upload_id == upload_id,
                (
                    OfflineConversion.metrica_upload_counter_id.is_(None)
                    if expected_counter_id is None
                    else OfflineConversion.metrica_upload_counter_id == expected_counter_id
                ),
                checked_at_predicate,
            )
            .values(
                metrica_upload_checked_at=checked_at,
                metrica_upload_next_check_at=None,
                metrica_upload_check_error=category,
            )
        )
        await db.commit()


async def refresh_metrica_upload_statuses(session_factory, *, limit: int = MAX_CHECKS_PER_RUN) -> int:
    """Recheck bounded upload states via GET only; never resend conversion CSV."""
    batch_limit = min(max(int(limit), 0), MAX_CHECKS_PER_RUN)
    if not batch_limit:
        return 0

    now = datetime.now(timezone.utc)
    due = or_(
        OfflineConversion.metrica_upload_next_check_at <= now,
        and_(
            OfflineConversion.metrica_upload_next_check_at.is_(None),
            OfflineConversion.metrica_upload_check_error.is_(None),
        ),
        and_(
            OfflineConversion.metrica_upload_next_check_at.is_(None),
            OfflineConversion.metrica_upload_check_error.in_(("ORIGINAL_COUNTER_UNKNOWN", "ORIGINAL_COUNTER_INVALID")),
            OfflineConversion.metrica_upload_counter_id.is_not(None),
        ),
    )
    async with session_factory() as db:
        rows = list((await db.execute(
            select(
                OfflineConversion.id,
                Channel.id,
                Admin.external_id,
                OfflineConversion.metrica_upload_counter_id,
                OfflineConversion.metrica_upload_id,
                OfflineConversion.status,
                OfflineConversion.metrica_upload_checked_at,
                OfflineConversion.metrica_upload_observation_started_at,
                OfflineConversion.metrica_upload_check_attempts,
                OfflineConversion.created_at,
            )
            .join(TrafficMembership, TrafficMembership.id == OfflineConversion.membership_id)
            .join(Channel, Channel.id == TrafficMembership.channel_id)
            .join(Admin, Admin.id == Channel.admin_id)
            .where(
                OfflineConversion.status.in_(("uploaded", "linkage_failure")),
                OfflineConversion.metrica_upload_id.is_not(None),
                due,
            )
            .order_by(OfflineConversion.metrica_upload_next_check_at.asc().nullsfirst(), OfflineConversion.created_at.asc())
            .limit(batch_limit)
        )).all())

    refreshed = 0
    checks = 0
    for (
        conversion_id,
        channel_id,
        admin_id,
        counter_id,
        upload_id,
        previous_status,
        previous_checked_at,
        observation_started_at,
        attempts,
        created_at,
    ) in rows:
        checked_at = datetime.now(timezone.utc)
        if not counter_id or not str(counter_id).isdigit():
            category = "ORIGINAL_COUNTER_UNKNOWN" if not counter_id else "ORIGINAL_COUNTER_INVALID"
            await _record_unverifiable(
                session_factory,
                conversion_id=conversion_id,
                previous_status=previous_status,
                upload_id=str(upload_id),
                expected_counter_id=counter_id,
                previous_checked_at=previous_checked_at,
                category=category,
                checked_at=checked_at,
            )
            continue

        counter_id = str(counter_id)
        observation_started_at = (
            _utc_datetime(observation_started_at)
            if observation_started_at
            else _utc_datetime(created_at)
        )
        if observation_started_at <= checked_at - OBSERVATION_PERIOD:
            await _record_unverifiable(
                session_factory,
                conversion_id=conversion_id,
                previous_status=previous_status,
                upload_id=str(upload_id),
                expected_counter_id=counter_id,
                previous_checked_at=previous_checked_at,
                category="OBSERVATION_WINDOW_ENDED",
                checked_at=checked_at,
            )
            continue

        client = None
        checks += 1
        provider_status = None
        check_error = None
        try:
            async with session_factory() as db:
                encrypted = await get_encrypted_token(admin_id, channel_id, db=db)
            if not encrypted:
                check_error = "OAUTH_TOKEN_UNAVAILABLE"
            else:
                client = YandexMetricaClient(decrypt_token(encrypted))
                payload = await client.get_offline_conversion_upload(int(counter_id), str(upload_id))
                uploading = payload.get("uploading") if isinstance(payload, dict) else None
                provider_status = str((uploading or {}).get("status") or "UNKNOWN").upper()
        except Exception as exc:
            check_error = _error_category(exc)
            logger.warning("METRICA_UPLOAD_STATUS_CHECK_FAILED category=%s", check_error)
        finally:
            if client:
                await client.close()

        if check_error:
            # Preserve provider status and delivery state on transport/auth
            # failures; record only a bounded category, never exception text.
            check_error_delay = _backoff_seconds(previous_status, int(attempts or 0) + 1, technical_error=True)
            async with session_factory() as db:
                checked_at_predicate = (
                    OfflineConversion.metrica_upload_checked_at.is_(None)
                    if previous_checked_at is None
                    else OfflineConversion.metrica_upload_checked_at == previous_checked_at
                )
                await db.execute(
                    update(OfflineConversion)
                    .where(
                        OfflineConversion.id == conversion_id,
                        OfflineConversion.status == previous_status,
                        OfflineConversion.metrica_upload_id == upload_id,
                        OfflineConversion.metrica_upload_counter_id == counter_id,
                        checked_at_predicate,
                    )
                    .values(
                        metrica_upload_checked_at=checked_at,
                        metrica_upload_observation_started_at=observation_started_at,
                        metrica_upload_check_attempts=int(attempts or 0) + 1,
                        metrica_upload_next_check_at=checked_at + timedelta(seconds=check_error_delay),
                        metrica_upload_check_error=check_error,
                    )
                )
                await db.commit()
            continue

        if await _save_check_result(
            session_factory,
            conversion_id=conversion_id,
            previous_status=previous_status,
            upload_id=str(upload_id),
            counter_id=counter_id,
            checked_at=checked_at,
            previous_checked_at=previous_checked_at,
            observation_started_at=observation_started_at,
            attempts=int(attempts or 0),
            provider_status=provider_status,
        ):
            refreshed += 1

    if checks >= batch_limit:
        logger.info("METRICA_UPLOAD_STATUS_CHECK_LIMIT_REACHED limit=%s", batch_limit)
    return refreshed
