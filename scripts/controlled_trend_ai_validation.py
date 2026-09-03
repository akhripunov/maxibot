#!/usr/bin/env python3
"""Create the single explicitly approved Trend shadow AI run."""
from __future__ import annotations

import asyncio
import json

from sqlalchemy import select

from app.ads.service import run_check
from app.db.models import Admin, AdsCampaign, AdsChannelCampaignLink, Channel
from app.db.session import SessionLocal


async def main() -> None:
    async with SessionLocal() as db:
        row = await db.execute(
            select(Admin.external_id, Channel.id)
            .select_from(Channel)
            .join(Admin, Channel.admin_id == Admin.id)
            .join(AdsChannelCampaignLink, AdsChannelCampaignLink.channel_id == Channel.id)
            .join(AdsCampaign, AdsCampaign.id == AdsChannelCampaignLink.campaign_id)
            .where(AdsCampaign.external_id == "713798021", AdsChannelCampaignLink.status == "ACTIVE")
            .limit(1)
        )
        target = row.one_or_none()
        if not target:
            raise RuntimeError("TARGET_CAMPAIGN_CHANNEL_NOT_FOUND")
        result = await run_check(db, admin_external_id=str(target[0]), channel_id=target[1])
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
