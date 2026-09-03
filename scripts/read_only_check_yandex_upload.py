from __future__ import annotations

import asyncio
import json

from app.db.models import Admin, Channel
from app.db.session import SessionLocal
from app.integrations.yandex_metrica import YandexMetricaClient, channel_setup_status
from app.integrations.yandex_oauth import get_encrypted_token
from app.security_tokens import decrypt_token

UPLOAD_ID = 1186156497

async def main() -> None:
    async with SessionLocal() as db:
        channel = await db.scalar(__import__("sqlalchemy").select(Channel).where(Channel.title == "Москва 365"))
        admin_external_id = await db.scalar(__import__("sqlalchemy").select(Admin.external_id).where(Admin.id == channel.admin_id))
        setup = await channel_setup_status(admin_external_id, channel.id)
        token = await get_encrypted_token(admin_external_id, channel.id)
        client = YandexMetricaClient(decrypt_token(token))
        try:
            response = await client._request("GET", f"/management/v1/counter/{int(setup['counter_id'])}/offline_conversions/uploading/{UPLOAD_ID}")
            uploading = dict(response.get("uploading") or {})
            print(json.dumps({
                "upload_id": UPLOAD_ID,
                "status": uploading.get("status"),
                "counter_id": int(setup["counter_id"]),
                "error": uploading.get("error") or uploading.get("error_message"),
                "created": uploading.get("created_at"),
            }, ensure_ascii=False))
        finally:
            await client.close()

asyncio.run(main())
