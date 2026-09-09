"""Retry the exact batch blocked only by the resolved OAuth cache incident.

This is intentionally not a bulk sender.  It refuses to run unless the caller
supplies the exact expected batch size, and every row goes through the normal
EXACT/Metrica gate in ``_send_exact_conversion``.
"""
from __future__ import annotations

import asyncio
import json
import os

from sqlalchemy import select

from app.bot.menu import _send_exact_conversion
from app.db.models import Channel, OfflineConversion, TrafficMembership
from app.db.session import SessionLocal


async def main() -> None:
    expected = int(os.environ["EXPECTED_PENDING_CONVERSIONS"])
    async with SessionLocal() as db:
        rows = list((await db.scalars(
            select(OfflineConversion)
            .join(TrafficMembership, TrafficMembership.id == OfflineConversion.membership_id)
            .where(
                OfflineConversion.target == "YANDEX",
                OfflineConversion.status == "pending",
                OfflineConversion.last_error == "KeyError",
                TrafficMembership.attribution_status == "EXACT",
            )
            .order_by(OfflineConversion.created_at.asc())
        )).all())
        if len(rows) != expected:
            raise RuntimeError(f"expected {expected} OAuth-recovery rows, found {len(rows)}")

        outcomes: dict[str, int] = {}
        for conversion in rows:
            membership = await db.get(TrafficMembership, conversion.membership_id)
            if not membership:
                raise RuntimeError("membership missing")
            channel = await db.get(Channel, membership.channel_id)
            if not channel:
                raise RuntimeError("channel missing")
            outcome = await _send_exact_conversion(db, conversion, membership, channel)
            outcomes[outcome] = outcomes.get(outcome, 0) + 1

    print(json.dumps({"expected": expected, "outcomes": outcomes}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
