from __future__ import annotations

import csv
import io
from typing import Any
from urllib.parse import urlsplit

import httpx
from redis.asyncio import Redis

from app.core.config import settings
from app.security_tokens import decrypt_token


class YandexMetricaClient:
    def __init__(self, token: str, base_url: str = "https://api-metrika.yandex.net"):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=20,
            headers={"Authorization": f"OAuth {token}"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        response = await self._client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    async def list_counters(self) -> list[dict]:
        return list((await self._request("GET", "/management/v1/counters")).get("counters") or [])

    async def create_counter(self, name: str, site: str) -> int:
        parsed = urlsplit(site)
        domain = parsed.netloc or parsed.path
        domain = domain.split("/", 1)[0]
        data = await self._request("POST", "/management/v1/counters", json={"counter": {"name": name, "site": domain}})
        return int((data.get("counter") or {}).get("id") or data["id"])

    async def list_goals(self, counter_id: int) -> list[dict]:
        return list((await self._request("GET", f"/management/v1/counter/{counter_id}/goals")).get("goals") or [])

    async def create_action_goal(self, counter_id: int, name: str) -> int:
        data = await self._request(
            "POST",
            f"/management/v1/counter/{counter_id}/goals",
            json={"goal": {"name": name, "type": "action", "conditions": [{"type": "exact", "url": name}]}},
        )
        return int((data.get("goal") or {}).get("id") or data["id"])

    async def upload_offline_conversion(self, counter_id: int, yclid: str, goal_name: str, timestamp: str) -> dict:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["yclid", "Target", "DateTime"])
        writer.writerow([yclid, goal_name, timestamp])
        response = await self._client.post(
            f"/management/v1/counter/{counter_id}/offline_conversions/upload",
            files={"file": ("conversion.csv", output.getvalue(), "text/csv")},
        )
        response.raise_for_status()
        return response.json()


async def ensure_channel_setup(admin_external_id: str, channel_id: str, encrypted_token: str, channel_title: str) -> dict[str, int]:
    client = YandexMetricaClient(decrypt_token(encrypted_token), settings.yandex_metrica_api_url)
    try:
        name = f"MAXIBOT · {channel_title} · {channel_id}"
        counter = next((row for row in await client.list_counters() if row.get("name") == name), None)
        counter_id = int(counter["id"]) if counter else await client.create_counter(name, settings.traffic_public_base_url)
        goals = await client.list_goals(counter_id)
        ids = {"counter_id": counter_id}
        for key, goal_name in (("to_channel_goal_id", "toChannel"), ("user_subscribe_goal_id", "userDidSubscribe")):
            goal = next((row for row in goals if row.get("name") == goal_name), None)
            ids[key] = int(goal["id"]) if goal else await client.create_action_goal(counter_id, goal_name)
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            await redis.hset(f"yandex:metrica:{admin_external_id}:{channel_id}", mapping={**ids, "admin_external_id": admin_external_id, "channel_id": channel_id})
        finally:
            await redis.aclose()
        return ids
    finally:
        await client.close()


async def channel_setup_status(admin_external_id: str, channel_id: str) -> dict[str, str]:
    """Return non-secret Metrica IDs for one owner/channel pair only."""
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        values = await redis.hgetall(f"yandex:metrica:{admin_external_id}:{channel_id}")
        return {
            key: value
            for key, value in values.items()
            if key in {"counter_id", "to_channel_goal_id", "user_subscribe_goal_id"}
        }
    except Exception:
        return {}
    finally:
        await redis.aclose()
