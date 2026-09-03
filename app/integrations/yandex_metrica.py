from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
from typing import Any
from urllib.parse import urlsplit

import httpx
from redis.asyncio import Redis

from app.core.config import settings
from app.security_tokens import decrypt_token

logger = logging.getLogger(__name__)


def _safe_response_headers(response: httpx.Response) -> dict[str, str]:
    return {
        key: value
        for key, value in response.headers.items()
        if key.lower() in {"request-id", "x-request-id", "x-yandex-request-id", "retry-after"}
    }


def _log_yandex_http_error(response: httpx.Response, *, operation: str, yclid: str | None = None) -> None:
    safe_details = {
        "operation": operation,
        "http_status": response.status_code,
        "response_body": response.text[:2000],
        "response_headers": _safe_response_headers(response),
        "yclid_hash": hashlib.sha256(yclid.encode()).hexdigest() if yclid else None,
    }
    logger.error(
        "Yandex Metrica request failed: %s",
        json.dumps(safe_details, ensure_ascii=False, sort_keys=True),
        extra=safe_details,
    )


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
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            _log_yandex_http_error(response, operation=f"{method} {path}")
            raise
        return response.json()

    async def list_counters(self) -> list[dict]:
        return list((await self._request("GET", "/management/v1/counters")).get("counters") or [])

    async def get_counter(self, counter_id: int) -> dict:
        data = await self._request("GET", f"/management/v1/counter/{counter_id}")
        return dict(data.get("counter") or data)

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

    async def upload_offline_conversion(
        self,
        counter_id: int,
        yclid: str | None,
        client_id: str | None,
        goal_identifier: str,
        timestamp: str,
    ) -> dict:
        """Upload one CSV row with verified Direct and Metrica identifiers."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ClientId", "Yclid", "DateTime", "Target"])
        writer.writerow([client_id or "", yclid or "", timestamp, goal_identifier])
        response = await self._client.post(
            f"/management/v1/counter/{counter_id}/offline_conversions/upload",
            files={"file": ("conversion.csv", output.getvalue(), "text/csv")},
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            _log_yandex_http_error(response, operation="POST offline_conversions/upload", yclid=yclid)
            raise
        return response.json()

    async def get_offline_conversion_upload(self, counter_id: int, upload_id: str) -> dict:
        """Return Metrica's asynchronous processing state for one upload."""
        return await self._request(
            "GET",
            f"/management/v1/counter/{counter_id}/offline_conversions/uploading/{upload_id}",
        )

    async def goal_id_by_name(self, counter_id: int, goal_name: str) -> int | None:
        goals = await self.list_goals(counter_id)
        row = next((goal for goal in goals if goal.get("name") == goal_name), None)
        return int(row["id"]) if row and row.get("id") is not None else None

    async def ensure_action_goal(self, counter_id: int, goal_name: str) -> int:
        existing = await self.goal_id_by_name(counter_id, goal_name)
        return existing if existing is not None else await self.create_action_goal(counter_id, goal_name)

    async def ensure_action_goal_identifier(self, counter_id: int, goal_name: str) -> str:
        """Return the identifier required in offline CSV Target."""
        if await self.goal_id_by_name(counter_id, goal_name) is None:
            await self.create_action_goal(counter_id, goal_name)
        return goal_name


async def ensure_channel_setup(admin_external_id: str, channel_id: str, encrypted_token: str, channel_title: str) -> dict[str, int | str]:
    client = YandexMetricaClient(decrypt_token(encrypted_token), settings.yandex_metrica_api_url)
    try:
        name = f"MAXIBOT · {channel_title} · {channel_id}"
        counter = next((row for row in await client.list_counters() if row.get("name") == name), None)
        counter_id = int(counter["id"]) if counter else await client.create_counter(name, settings.traffic_public_base_url)
        if not counter:
            counter = next((row for row in await client.list_counters() if int(row.get("id", 0)) == counter_id), None)
        if not (counter or {}).get("owner_login"):
            counter = await client.get_counter(counter_id)
        goals = await client.list_goals(counter_id)
        ids = {"counter_id": counter_id}
        for key, goal_name in (
            ("to_channel_goal_id", "toChannel"),
            ("user_open_max_goal_id", "userDidOpenMax"),
            ("user_subscribe_goal_id", "userDidSubscribe"),
        ):
            goal = next((row for row in goals if row.get("name") == goal_name), None)
            ids[key] = int(goal["id"]) if goal else await client.create_action_goal(counter_id, goal_name)
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            mapping = {**ids, "admin_external_id": admin_external_id, "channel_id": channel_id}
            owner_login = (counter or {}).get("owner_login")
            if isinstance(owner_login, str) and owner_login.strip():
                mapping["owner_login"] = owner_login.strip()
            await redis.hset(f"yandex:metrica:{admin_external_id}:{channel_id}", mapping=mapping)
        finally:
            await redis.aclose()
        return ids
    finally:
        await client.close()


async def ensure_system_setup(encrypted_token: str) -> dict[str, int | str]:
    client = YandexMetricaClient(decrypt_token(encrypted_token), settings.yandex_metrica_api_url)
    try:
        name = "MAXIBOT · System"
        counter = next((row for row in await client.list_counters() if row.get("name") == name), None)
        counter_id = int(counter["id"]) if counter else await client.create_counter(name, settings.traffic_public_base_url)
        if not (counter or {}).get("owner_login"):
            counter = await client.get_counter(counter_id)
        goal_name = "bot_started"
        goal_id = await client.ensure_action_goal(counter_id, goal_name)
        channel_goal_name = "maxibot_channel_connected"
        channel_goal_id = await client.ensure_action_goal(counter_id, channel_goal_name)
        result: dict[str, int | str] = {
            "counter_id": counter_id,
            "goal_id": goal_id,
            "goal_name": goal_name,
            "channel_goal_id": channel_goal_id,
            "channel_goal_name": channel_goal_name,
        }
        owner_login = (counter or {}).get("owner_login")
        if isinstance(owner_login, str) and owner_login.strip():
            result["owner_login"] = owner_login.strip()
        return result
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
            if key in {
                "counter_id",
                "to_channel_goal_id",
                "user_open_max_goal_id",
                "user_subscribe_goal_id",
                "owner_login",
            }
        }
    except Exception:
        return {}
    finally:
        await redis.aclose()
