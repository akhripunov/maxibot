"""Send exactly one fresh exact pending conversion after a sender fix.

This is an owner-controlled diagnostic, not a bulk sender. It refuses to run
when the latest pending exact row is absent, stale, or ambiguous.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.bot.menu import _send_exact_conversion
from app.core.config import settings
from app.db.models import Admin, Channel, OfflineConversion, TrafficMembership
from app.db.session import SessionLocal


async def main() -> None:
    async with SessionLocal() as db:
        rows = list((await db.scalars(
            select(OfflineConversion)
            .join(TrafficMembership)
            .where(
                OfflineConversion.target == "YANDEX",
                OfflineConversion.status == "pending",
                TrafficMembership.attribution_status == "EXACT",
                OfflineConversion.created_at >= datetime.now(timezone.utc) - timedelta(minutes=30),
            )
            .order_by(OfflineConversion.created_at.desc())
            .limit(2)
        )).all())
        if len(rows) != 1:
            raise RuntimeError(f"expected exactly one fresh exact pending conversion, found {len(rows)}")
        conversion = rows[0]
        membership = await db.get(TrafficMembership, conversion.membership_id)
        if not membership or membership.attribution_status != "EXACT":
            raise RuntimeError("exact membership gate missing")
        channel = await db.scalar(select(Channel).join(TrafficMembership, TrafficMembership.channel_id == Channel.id).where(TrafficMembership.id == membership.id))
        if not channel:
            raise RuntimeError("channel missing")
        result = await _send_exact_conversion(db, conversion, membership, channel)
        print(json.dumps({
            "status": result,
            "queue_status": conversion.status,
            "attempts": conversion.attempts,
            "goal": conversion.goal,
            "membership_hash": hashlib.sha256(membership.id.encode()).hexdigest()[:16],
            "dry_run": settings.yandex_conversion_dry_run,
        }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
