"""RU-side client for the optional read-only EU Telegram executor."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.ads.providers.base import AdsProvider, ProviderReport
from app.ads.regional import AdsRegionalError, resolve_ads_account_route
from app.telegram_executor.protocol import ExecutionOperation, TelegramExecutionRequestV1, TelegramExecutionResponseV1, PROTOCOL_VERSION


class TelegramRemoteError(RuntimeError):
    pass


class RemoteTelegramAdsProvider(AdsProvider):
    provider_name = "TELEGRAM_ADS"

    def __init__(self, account: Any, *, identity_ref: UUID | str, owner_identifier: str, endpoint: str, ca_file: str, client_cert_file: str, client_key_file: str, expected_sha: str, client: httpx.AsyncClient | None = None):
        route = resolve_ads_account_route(account)
        if route.execution_region != "EU":
            raise AdsRegionalError("EXECUTION_REGION_UNAVAILABLE")
        if not all((endpoint, ca_file, client_cert_file, client_key_file)):
            raise TelegramRemoteError("REMOTE_EXECUTOR_NOT_READY")
        self.account, self.route, self.identity_ref, self.owner_identifier, self.endpoint, self.expected_sha = account, route, UUID(str(identity_ref)), owner_identifier, endpoint.rstrip("/"), expected_sha
        self._client = client or httpx.AsyncClient(verify=ca_file, cert=(client_cert_file, client_key_file), timeout=httpx.Timeout(30.0, connect=5.0))
        self._owned = client is None

    async def close(self) -> None:
        if self._owned:
            await self._client.aclose()

    async def _execute(self, operation: ExecutionOperation, *, campaign_id: int | None = None, date_from: date | None = None, date_to: date | None = None, slot_key: str = "manual") -> ProviderReport:
        request = TelegramExecutionRequestV1(request_id=uuid4(), sent_at=datetime.now(timezone.utc), account_id=UUID(str(self.route.account_id)), identity_ref=self.identity_ref, owner_identifier=self.owner_identifier, operation=operation, slot_key=slot_key, campaign_external_id=str(campaign_id) if campaign_id is not None else None, date_from=date_from.isoformat() if date_from else None, date_to=date_to.isoformat() if date_to else None)
        try:
            response = TelegramExecutionResponseV1.model_validate((await self._client.post(f"{self.endpoint}/v1/execute", json=request.model_dump(mode="json"))).json())
        except httpx.TimeoutException as exc:
            raise TelegramRemoteError("REMOTE_EXECUTOR_UNAVAILABLE") from exc
        except httpx.HTTPError as exc:
            raise TelegramRemoteError("REMOTE_EXECUTOR_UNAVAILABLE") from exc
        if response.protocol_version != PROTOCOL_VERSION or response.executor_region != "EU":
            raise TelegramRemoteError("PROTOCOL_VERSION_MISMATCH")
        if response.executor_version_sha != self.expected_sha:
            raise TelegramRemoteError("EXECUTOR_VERSION_MISMATCH")
        if response.account_id != UUID(str(self.route.account_id)) or response.request_id != request.request_id or response.operation != operation:
            raise TelegramRemoteError("REMOTE_RESPONSE_MISMATCH")
        if response.status != "SUCCESS" or not response.normalized_result:
            raise TelegramRemoteError(response.error.code if response.error else "PROVIDER_ERROR")
        result = response.normalized_result
        return ProviderReport(provider=self.provider_name, report_type=str(result.get("report_type") or operation.value), rows=list(result.get("rows") or []), requested_fields=tuple(result.get("requested_fields") or ()), date_from=date_from, date_to=date_to, fetched_at=datetime.fromisoformat(result["fetched_at"]) if result.get("fetched_at") else datetime.now(timezone.utc), status=str(result.get("status") or "SUCCESS"), error_code=result.get("error_code"))

    async def campaigns(self, campaign_ids: list[int] | None = None) -> ProviderReport:
        report = await self._execute(ExecutionOperation.ACCOUNT_INVENTORY)
        if campaign_ids:
            wanted = {str(x) for x in campaign_ids}
            report = ProviderReport(provider=report.provider, report_type=report.report_type, rows=[r for r in report.rows if str(r.get("Id")) in wanted], requested_fields=report.requested_fields, fetched_at=report.fetched_at, status=report.status, error_code=report.error_code)
        return report

    async def campaign_performance(self, campaign_id: int, *, date_from: date, date_to: date) -> ProviderReport:
        return await self._execute(ExecutionOperation.CAMPAIGN_PERFORMANCE, campaign_id=campaign_id, date_from=date_from, date_to=date_to)

    async def ads(self, campaign_ids: list[int]) -> ProviderReport:
        return await self.campaigns(campaign_ids)

