"""Read one Yandex Direct campaign using the token stored by MAXIBOT OAuth."""

from __future__ import annotations

import argparse
import asyncio
import json

from app.integrations.yandex_direct import YandexDirectClient
from app.integrations.yandex_oauth import get_encrypted_token
from app.security_tokens import decrypt_token


async def run(admin_id: str, channel_id: str, campaign_id: int) -> int:
    encrypted = await get_encrypted_token(admin_id, channel_id)
    if not encrypted:
        raise RuntimeError("No stored Yandex OAuth token for this admin/channel")
    client = YandexDirectClient(decrypt_token(encrypted))
    try:
        campaign = await client.get_campaign(campaign_id)
    finally:
        await client.close()
    print(json.dumps({"campaign_id": campaign_id, "campaign": campaign}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if campaign else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-id", required=True)
    parser.add_argument("--channel-id", required=True)
    parser.add_argument("--campaign-id", type=int, default=713735649)
    args = parser.parse_args()
    return asyncio.run(run(args.admin_id, args.channel_id, args.campaign_id))


if __name__ == "__main__":
    raise SystemExit(main())
