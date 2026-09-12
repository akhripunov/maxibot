from __future__ import annotations

import json
import logging
import csv
import io
import asyncio
from typing import Any, Iterable

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class YandexDirectAPIError(RuntimeError):
    """An API-level error returned in an otherwise successful HTTP response."""


def _safe_response_headers(response: httpx.Response) -> dict[str, str]:
    return {
        key: value
        for key, value in response.headers.items()
        if key.lower() in {"request-id", "x-request-id", "retry-after"}
    }


def _log_api_error(response: httpx.Response, *, operation: str) -> None:
    details = {
        "operation": operation,
        "http_status": response.status_code,
        "response_body": response.text[:2000],
        "response_headers": _safe_response_headers(response),
    }
    logger.error("Yandex Direct API request failed: %s", json.dumps(details, ensure_ascii=False, sort_keys=True))


class YandexDirectClient:
    """Yandex Direct API v5 client.

    Read-only is the default. Provider writes can only be enabled by the
    bounded Autopilot Control Plane, which passes ``allow_write=True`` to an
    isolated adapter. No regular UI path should construct such an adapter.
    """

    def __init__(self, token: str, base_url: str | None = None, api_version: str | None = None, *, allow_write: bool = False, client_login: str | None = None):
        self._allow_write = bool(allow_write)
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept-Language": "ru",
            "Content-Type": "application/json; charset=utf-8",
        }
        if client_login:
            headers["Client-Login"] = str(client_login).strip()
        self._client = httpx.AsyncClient(
            base_url=(base_url or settings.yandex_direct_api_url).rstrip("/") + "/" + (api_version or settings.yandex_direct_api_version).strip("/"),
            timeout=20,
            headers=headers,
        )

    async def mutate(self, resource: str, *, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send one provider mutation; unavailable to the default client."""
        if not self._allow_write:
            raise PermissionError("YANDEX_DIRECT_READ_ONLY_CLIENT")
        if method not in {"add", "update", "delete", "set", "suspend", "resume"}:
            raise ValueError("YANDEX_DIRECT_MUTATION_METHOD_UNSUPPORTED")
        response = await self._client.post(
            f"/{resource}", json={"method": method, "params": params}
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            _log_api_error(response, operation=f"{method.upper()} /{resource}")
            raise
        data = response.json()
        if data.get("error"):
            details = data["error"] if isinstance(data["error"], dict) else {"error": data["error"]}
            raise YandexDirectAPIError(str(details.get("Description") or details.get("error_code") or details))
        return data.get("result") if isinstance(data.get("result"), dict) else {}

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, resource: str, *, selection: dict[str, Any], fields: Iterable[str], extra_params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        payload = {
            "method": "get",
            "params": {
                "SelectionCriteria": selection,
                "FieldNames": list(fields),
            },
        }
        if extra_params:
            payload["params"].update(extra_params)
        response = await self._client.post(f"/{resource}", json=payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            _log_api_error(response, operation=f"POST /{resource}")
            raise
        data = response.json()
        if data.get("error"):
            details = data["error"] if isinstance(data["error"], dict) else {"error": data["error"]}
            logger.error("Yandex Direct API returned an error: %s", json.dumps(details, ensure_ascii=False, sort_keys=True)[:2000])
            raise YandexDirectAPIError(str(details.get("Description") or details.get("error_code") or details))
        return list(data.get("result", {}).get("Campaigns") or data.get("result", {}).get("Ads") or [])

    async def _request_result(self, resource: str, *, selection: dict[str, Any], fields: Iterable[str], result_keys: tuple[str, ...]) -> list[dict[str, Any]]:
        payload = {"method": "get", "params": {"SelectionCriteria": selection, "FieldNames": list(fields)}}
        response = await self._client.post(f"/{resource}", json=payload)
        response.raise_for_status()
        data = response.json()
        if data.get("error"):
            details = data["error"] if isinstance(data["error"], dict) else {"error": data["error"]}
            raise YandexDirectAPIError(str(details.get("Description") or details.get("error_code") or details))
        result = data.get("result", {})
        for key in result_keys:
            if isinstance(result.get(key), list):
                return list(result[key])
        return []

    async def get_campaigns(self, campaign_ids: Iterable[int] | None = None) -> list[dict[str, Any]]:
        selection = {"Ids": [int(value) for value in campaign_ids]} if campaign_ids else {}
        return await self._request(
            "campaigns",
            selection=selection,
            fields=("Id", "Name", "Type", "State", "Status", "StatusPayment", "StartDate", "EndDate"),
        )

    async def get_campaign(self, campaign_id: int) -> dict[str, Any] | None:
        rows = await self.get_campaigns([campaign_id])
        return next((row for row in rows if int(row.get("Id", 0)) == int(campaign_id)), None)

    async def get_ads(self, campaign_ids: Iterable[int] | None = None) -> list[dict[str, Any]]:
        """Read-only ad destinations used to scope campaigns to a landing."""
        ids = [int(value) for value in campaign_ids] if campaign_ids else []
        # Direct limits CampaignIds in SelectionCriteria; keep each request
        # small and read-only while supporting large accounts.
        requests = []
        for start in range(0, len(ids), 10):
            requests.append(self._request(
                "ads",
                selection={"CampaignIds": ids[start:start + 10]},
                fields=("Id", "CampaignId", "Type", "Subtype", "State", "Status", "StatusClarification"),
                extra_params={"TextAdFieldNames": ["Title", "Title2", "Text", "Href", "DisplayUrlPath", "AdImageHash", "AdImageModeration", "VideoExtension"], "TextImageAdFieldNames": ["Title", "Text", "Href", "AdImageHash"], "MobileAppAdFieldNames": ["Title", "Text", "AdImageHash", "AdImageModeration", "VideoExtension"], "MobileAppImageAdFieldNames": ["AdImageHash"], "DynamicTextAdFieldNames": ["AdImageHash"], "ResponsiveAdFieldNames": ["AdImages"]},
            ))
        batches = await asyncio.gather(*requests) if requests else []
        return [ad for batch in batches for ad in batch]

    async def get_ads_by_ids(self, ad_ids: Iterable[int | str]) -> list[dict[str, Any]]:
        """Read exact ads before/after a bounded Autopilot mutation."""
        ids = [int(value) for value in ad_ids if str(value).isdigit()]
        if not ids:
            return []
        return await self._request(
            "ads", selection={"Ids": ids},
            fields=("Id", "CampaignId", "AdGroupId", "Type", "Subtype", "State", "Status", "StatusClarification"),
            extra_params={"TextAdFieldNames": ["Title", "Title2", "Text", "Href", "DisplayUrlPath", "AdImageHash", "VideoExtension"],
                          "TextImageAdFieldNames": ["AdImageHash", "Href"],
                          "TextAdBuilderAdFieldNames": ["Creative", "Href"]},
        )

    async def get_campaign_configuration(self, campaign_id: int | str) -> dict[str, Any] | None:
        """Return fields that V1 may change and must verify afterwards."""
        ident = int(campaign_id)
        rows = await self._request(
            "campaigns", selection={"Ids": [ident]},
            fields=("Id", "Name", "Type", "State", "Status", "ExcludedSites", "DailyBudget"),
        )
        return next((row for row in rows if int(row.get("Id", 0)) == ident), None)

    async def get_ad_images(self, image_hashes: Iterable[str]) -> list[dict[str, Any]]:
        hashes = [str(value) for value in image_hashes if value]
        if not hashes:
            return []
        return await self._request_result(
            "adimages", selection={"AdImageHashes": hashes},
            fields=("AdImageHash", "OriginalUrl", "PreviewUrl", "Name", "Type", "Subtype"),
            result_keys=("AdImages",),
        )

    async def get_creatives(self, creative_ids: Iterable[int | str]) -> list[dict[str, Any]]:
        ids = [int(value) for value in creative_ids if str(value).isdigit()]
        if not ids:
            return []
        return await self._request_result(
            "creatives", selection={"Ids": ids},
            fields=("Id", "Type", "Name", "OriginalUrl", "PreviewUrl", "ThumbnailUrl", "VideoExtension"),
            result_keys=("Creatives",),
        )

    async def get_campaign_report(
        self,
        campaign_id: int,
        *,
        date_from: str,
        date_to: str,
        goal_id: int | None = None,
    ) -> list[dict[str, str]]:
        """Read campaign statistics from Reports API; never mutates Direct."""
        params: dict[str, Any] = {
            "SelectionCriteria": {
                "DateFrom": date_from,
                "DateTo": date_to,
                "Filter": [{"Field": "CampaignId", "Operator": "EQUALS", "Values": [str(campaign_id)]}],
            },
            "FieldNames": ["Date", "CampaignId", "CampaignName", "Impressions", "Clicks", "Cost", "Conversions"],
            "ReportName": f"MAXIBOT read-only campaign {campaign_id} {date_from} {date_to}",
            "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
            "DateRangeType": "CUSTOM_DATE",
            "Format": "TSV",
            "IncludeVAT": "NO",
        }
        if goal_id is not None:
            params["Goals"] = [str(goal_id)]
        response = await self._client.post(
            "/reports",
            json={"params": params},
            headers={
                "skipReportHeader": "true",
                "skipColumnHeader": "false",
                "skipReportSummary": "true",
                "returnMoneyInMicros": "false",
            },
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            _log_api_error(response, operation="POST /reports")
            raise
        text = response.text.strip()
        if not text:
            return []
        # Direct normally answers TSV. If it returns a JSON error with HTTP 200,
        # fail closed instead of treating the error as an empty report.
        if text.startswith("{"):
            try:
                data = response.json()
            except ValueError:
                data = {}
            if data.get("error"):
                details = data["error"] if isinstance(data["error"], dict) else {"error": data["error"]}
                raise YandexDirectAPIError(str(details.get("Description") or details.get("error_code") or details))
        rows = list(csv.reader(io.StringIO(text), delimiter="\t"))
        if not rows:
            return []
        headers = [header.strip() for header in rows[0]]
        return [dict(zip(headers, row)) for row in rows[1:] if row and any(cell.strip() for cell in row)]

    async def get_campaign_catalog_report(
        self, *, date_from: str, date_to: str,
    ) -> list[dict[str, str]]:
        """Discover report-only campaign types (for example Master campaigns).

        The Campaigns service does not expose every campaign created by the
        Direct web wizard. The documented performance report still includes
        CampaignId/CampaignName for campaigns that had delivery in the window,
        so this remains a read-only, bounded discovery fallback.
        """
        params: dict[str, Any] = {
            "SelectionCriteria": {"DateFrom": date_from, "DateTo": date_to},
            "FieldNames": ["Date", "CampaignId", "CampaignName", "Impressions", "Clicks", "Cost", "Conversions"],
            "ReportName": f"MAXIBOT read-only campaign catalog {date_from} {date_to}",
            "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
            "DateRangeType": "CUSTOM_DATE",
            "Format": "TSV",
            "IncludeVAT": "NO",
        }
        response = await self._client.post(
            "/reports", json={"params": params},
            headers={"skipReportHeader": "true", "skipColumnHeader": "false", "skipReportSummary": "true", "returnMoneyInMicros": "false"},
        )
        response.raise_for_status()
        text = response.text.strip()
        if not text:
            return []
        if text.startswith("{"):
            data = response.json()
            if data.get("error"):
                details = data["error"] if isinstance(data["error"], dict) else {"error": data["error"]}
                raise YandexDirectAPIError(str(details.get("Description") or details.get("error_code") or details))
        rows = list(csv.reader(io.StringIO(text), delimiter="\t"))
        if not rows:
            return []
        headers = [header.strip() for header in rows[0]]
        return [dict(zip(headers, row)) for row in rows[1:] if row and any(cell.strip() for cell in row)]

    async def get_performance_report(
        self,
        report_type: str,
        *,
        campaign_id: int,
        date_from: str,
        date_to: str,
        fields: Iterable[str],
        report_name: str | None = None,
    ) -> tuple[list[dict[str, str]], str | None]:
        """Read one documented Direct report without retaining its raw body."""
        params: dict[str, Any] = {
            "SelectionCriteria": {
                "DateFrom": date_from,
                "DateTo": date_to,
                "Filter": [{"Field": "CampaignId", "Operator": "EQUALS", "Values": [str(campaign_id)]}],
            },
            "FieldNames": list(fields),
            "ReportName": report_name or f"MAXIBOT read-only {report_type} {campaign_id} {date_from} {date_to}",
            "ReportType": report_type,
            "DateRangeType": "CUSTOM_DATE",
            "Format": "TSV",
            "IncludeVAT": "NO",
        }
        response = await self._client.post(
            "/reports", json={"params": params},
            headers={"skipReportHeader": "true", "skipColumnHeader": "false", "skipReportSummary": "true", "returnMoneyInMicros": "false"},
        )
        response.raise_for_status()
        text = response.text.strip()
        if not text:
            return [], response.headers.get("x-request-id") or response.headers.get("request-id")
        if text.startswith("{"):
            data = response.json()
            if data.get("error"):
                details = data["error"] if isinstance(data["error"], dict) else {"error": data["error"]}
                raise YandexDirectAPIError(str(details.get("Description") or details.get("error_code") or details))
        rows = list(csv.reader(io.StringIO(text), delimiter="\t"))
        if not rows:
            return [], response.headers.get("x-request-id") or response.headers.get("request-id")
        headers = [header.strip() for header in rows[0]]
        return ([dict(zip(headers, row)) for row in rows[1:] if row and any(cell.strip() for cell in row)], response.headers.get("x-request-id") or response.headers.get("request-id"))
