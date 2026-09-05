"""Deliver system-scoped MAXIBOT conversions to Yandex Metrica."""
from __future__ import annotations

import logging
from datetime import timezone

from sqlalchemy import select

from app.core.config import settings
from app.db.models import BotConversion, BotTrafficVisit, SystemPromotionProfile
from app.integrations.yandex_metrica import YandexMetricaClient
from app.security_tokens import decrypt_token
from app.traffic.system_conversion import system_conversion_goal


logger = logging.getLogger(__name__)


async def process_system_conversions(session_factory, *, limit: int = 50) -> int:
    """Send pending system conversions once the visit has both identifiers."""
    if settings.yandex_conversion_dry_run:
        return 0
    async with session_factory() as db:
        profile = await db.scalar(select(SystemPromotionProfile).where(
            SystemPromotionProfile.system_key == "maxibot",
        ))
        if not profile or not profile.yandex_oauth_token_encrypted or not profile.yandex_counter_id:
            return 0
        rows = (await db.execute(
            select(BotConversion, BotTrafficVisit)
            .join(BotTrafficVisit, BotTrafficVisit.id == BotConversion.bot_visit_id)
            .where(BotConversion.status == "pending")
            .order_by(BotConversion.created_at)
            .limit(limit)
        )).all()
        token = decrypt_token(profile.yandex_oauth_token_encrypted)
        counter_id = int(profile.yandex_counter_id)

    if not rows:
        return 0

    client = YandexMetricaClient(token)
    processed = 0
    try:
        for conversion, visit in rows:
            if not visit.yclid or not visit.metrica_client_id:
                continue
            async with session_factory() as db:
                current = await db.get(BotConversion, conversion.id)
                if not current or current.status != "pending":
                    continue
                current.status = "sending"
                await db.commit()
            try:
                goal = await client.ensure_action_goal_identifier(
                    counter_id, system_conversion_goal(conversion.internal_event)
                )
                event_time = visit.started_at or conversion.created_at
                if event_time.tzinfo is None:
                    event_time = event_time.replace(tzinfo=timezone.utc)
                await client.upload_offline_conversion(
                    counter_id,
                    visit.yclid,
                    visit.metrica_client_id,
                    goal,
                    str(int(event_time.timestamp())),
                )
            except Exception:
                logger.exception("BOT_CONVERSION_SEND_FAILED internal_event=%s", conversion.internal_event)
                async with session_factory() as db:
                    current = await db.get(BotConversion, conversion.id)
                    if current:
                        current.status = "pending"
                        await db.commit()
                continue
            async with session_factory() as db:
                current = await db.get(BotConversion, conversion.id)
                if current:
                    current.status = "sent"
                    await db.commit()
            processed += 1
    finally:
        await client.close()
    return processed
