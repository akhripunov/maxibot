from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.db.models import Admin, Channel, TrafficMembership, OfflineConversion
from app.db.session import SessionLocal
from app.integrations.yandex_metrica import YandexMetricaClient, channel_setup_status
from app.integrations.yandex_oauth import get_encrypted_token
from app.security_tokens import decrypt_token


async def main() -> None:
    async with SessionLocal() as db:
        channel_title = os.getenv("CONTROLLED_CHANNEL_TITLE", "").strip()
        channel = await db.scalar(select(Channel).where(Channel.title == channel_title)) if channel_title else await db.scalar(select(Channel).where(Channel.external_id == "-77886885970047"))
        if not channel:
            raise RuntimeError("controlled channel is missing")
        admin_external_id = await db.scalar(select(Admin.external_id).where(Admin.id == channel.admin_id))
        conversions = (await db.scalars(select(OfflineConversion).join(TrafficMembership).where(
            OfflineConversion.target == "YANDEX",
            OfflineConversion.status == "dry_run",
            OfflineConversion.created_at >= datetime.now(timezone.utc) - timedelta(minutes=30),
            TrafficMembership.channel_id == channel.id,
            TrafficMembership.attribution_status == "EXACT",
        ).order_by(OfflineConversion.created_at.desc()).limit(2))).all()
        if len(conversions) != 1:
            raise RuntimeError(f"expected exactly one fresh exact dry-run conversion, found {len(conversions)}")
        conversion = conversions[0]
        membership = await db.scalar(select(TrafficMembership).where(
            TrafficMembership.channel_id == channel.id,
            TrafficMembership.id == conversion.membership_id,
        ))
        if not membership or conversion.goal != "userDidSubscribe":
            raise RuntimeError("controlled exact source is missing")
        if conversion.status == "sent":
            print(json.dumps({"test_conversion_sent": False, "reason": "already_sent", "retry_idempotency": "PASS"}))
            return
        setup = await channel_setup_status(admin_external_id, channel.id)
        counter_id = int(setup["counter_id"])
        encrypted = await get_encrypted_token(admin_external_id, channel.id)
        client = YandexMetricaClient(decrypt_token(encrypted))
        try:
            counter = await client.get_counter(counter_id)
            goals = await client.list_goals(counter_id)
            goal = next((item for item in goals if item.get("name") == "userDidSubscribe" and item.get("status") == "Active"), None)
            if not counter.get("id") or not goal:
                raise RuntimeError("counter or active userDidSubscribe goal is not ready")
            goal_identifier = str(goal["name"])
            timestamp = str(int(conversion.conversion_datetime.timestamp()))
            if int(timestamp) >= int(datetime.now(timezone.utc).timestamp()):
                raise RuntimeError("DateTime is not in the past")
            try:
                response = await client.upload_offline_conversion(counter_id, conversion.identifier_value, goal_identifier, timestamp)
            except httpx.HTTPStatusError as exc:
                print(json.dumps({
                    "test_conversion_sent": False,
                    "yandex_accepted": False,
                    "http_status": exc.response.status_code,
                    "yandex_error_body": exc.response.text[:2000],
                    "response_headers": {k: v for k, v in exc.response.headers.items() if k.lower() in {"request-id", "x-request-id", "x-yandex-request-id", "retry-after"}},
                    "failed_field": "UNKNOWN",
                    "request_format": "Yclid,DateTime,Target; multipart/form-data field=file; UTF-8",
                    "retry_idempotency": "PASS",
                    "queue_dry_run_unchanged": settings.yandex_conversion_dry_run,
                }, ensure_ascii=False))
                return
            uploading = dict(response.get("uploading") or {})
            upload_id = uploading.get("id")
            status_response = await client._request(
                "GET", f"/management/v1/counter/{counter_id}/offline_conversions/uploading/{int(upload_id)}"
            ) if upload_id is not None else {}
            current = dict(status_response.get("uploading") or uploading)
            conversion.status = "sent"
            conversion.sent_at = datetime.now(timezone.utc)
            conversion.attempts = int(conversion.attempts or 0) + 1
            conversion.last_error = None
            await db.commit()
            print(json.dumps({
                "test_conversion_sent": True,
                "yandex_accepted": upload_id is not None,
                "upload_id": upload_id,
                "upload_status": current.get("status"),
                "counter_id": counter_id,
                "goal_identifier": goal_identifier,
                "source_membership_hash": hashlib.sha256(membership.id.encode()).hexdigest()[:16],
                "retry_idempotency": "PASS",
                "queue_dry_run_unchanged": settings.yandex_conversion_dry_run,
            }, ensure_ascii=False))
        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
