"""Track the final asynchronous state of Metrica offline uploads."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import Admin, Channel, OfflineConversion, TrafficMembership, TrafficProfile
from app.integrations.yandex_metrica import YandexMetricaClient
from app.integrations.yandex_oauth import get_encrypted_token
from app.security_tokens import decrypt_token


logger = logging.getLogger(__name__)


async def refresh_metrica_upload_statuses(session_factory, *, limit: int = 100) -> int:
    """Promote only Metrica-processed uploads to delivered conversions."""
    async with session_factory() as db:
        rows = list((await db.execute(
            select(OfflineConversion.id, Channel.id, Admin.external_id, TrafficProfile.yandex_counter_id,
                   OfflineConversion.metrica_upload_id)
            .join(TrafficMembership, TrafficMembership.id == OfflineConversion.membership_id)
            .join(Channel, Channel.id == TrafficMembership.channel_id)
            .join(Admin, Admin.id == Channel.admin_id)
            .outerjoin(TrafficProfile, TrafficProfile.channel_id == Channel.id)
            .where(OfflineConversion.status == "uploaded", OfflineConversion.metrica_upload_id.is_not(None))
            .order_by(OfflineConversion.created_at.asc())
            .limit(limit)
        )).all())
    updated = 0
    for conversion_id, channel_id, admin_id, counter_id, upload_id in rows:
        if not counter_id:
            continue
        client = None
        try:
            async with session_factory() as db:
                encrypted = await get_encrypted_token(admin_id, channel_id, db=db)
            if not encrypted:
                continue
            client = YandexMetricaClient(decrypt_token(encrypted))
            payload = await client.get_offline_conversion_upload(int(counter_id), str(upload_id))
            uploading = payload.get("uploading") or {}
            remote_status = str(uploading.get("status") or "UNKNOWN")
            async with session_factory() as db:
                conversion = await db.get(OfflineConversion, conversion_id)
                if not conversion:
                    continue
                conversion.metrica_upload_status = remote_status
                conversion.metrica_upload_checked_at = datetime.now(timezone.utc)
                if remote_status == "PROCESSED":
                    conversion.status = "sent"
                    conversion.sent_at = datetime.now(timezone.utc)
                    conversion.last_error = None
                elif remote_status == "LINKAGE_FAILURE":
                    conversion.status = "linkage_failure"
                    conversion.last_error = "metrica_linkage_failure"
                await db.commit()
                updated += 1
        except Exception as exc:
            logger.warning("METRICA_UPLOAD_STATUS_CHECK_FAILED category=%s", type(exc).__name__)
        finally:
            if client:
                await client.close()
    return updated
