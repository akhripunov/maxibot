from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.ads.providers.telegram import TelegramAdsOwnerContext, TelegramAdsProvider
from .identity_store import IdentityStore, IdentityStoreError
from .protocol import TelegramExecutionRequestV1, TelegramExecutionResponseV1, StructuredExecutionError, PROTOCOL_VERSION


class ExecutorError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


class TelegramExecutorService:
    def __init__(self, store: IdentityStore, *, region: str = "EU", version_sha: str = "UNKNOWN", base_url: str = "https://ads.telegram.org"):
        self.store, self.region, self.version_sha, self.base_url = store, region, version_sha, base_url

    async def execute(self, request: TelegramExecutionRequestV1) -> TelegramExecutionResponseV1:
        started = datetime.now(timezone.utc)
        try:
            request.assert_supported()
            payload = self.store.get(request.identity_ref)
            if request.owner_identifier not in payload.get("allowed_owner_identifiers", []):
                raise ExecutorError("OWNER_MISMATCH", "requested owner is not allowed")
            context = TelegramAdsOwnerContext(request.owner_identifier, request.owner_identifier, payload["session"])
            provider = TelegramAdsProvider(context)
            try:
                if request.operation.value == "ACCOUNT_INVENTORY":
                    report = await provider.campaigns()
                else:
                    report = await provider.campaign_performance(int(request.campaign_external_id), date_from=date.fromisoformat(request.date_from or date.today().isoformat()), date_to=date.fromisoformat(request.date_to or date.today().isoformat()))
            finally:
                await provider.close()
            result: dict[str, Any] = {"provider": report.provider, "report_type": report.report_type, "rows": report.rows, "requested_fields": list(report.requested_fields), "date_from": report.date_from.isoformat() if report.date_from else None, "date_to": report.date_to.isoformat() if report.date_to else None, "fetched_at": report.fetched_at.isoformat() if report.fetched_at else None, "status": report.status, "error_code": report.error_code}
            status = "SUCCESS"
            error = None
        except ExecutorError as exc:
            result, status = None, "ERROR"
            error = StructuredExecutionError(code=exc.code, retryable=exc.retryable, message_sanitized=str(exc))
        except IdentityStoreError as exc:
            result, status = None, "ERROR"
            error = StructuredExecutionError(code=str(exc), retryable=False, message_sanitized="identity is unavailable")
        except Exception as exc:
            code = "AUTH_REQUIRED" if "REAUTH_REQUIRED" in str(exc) else "PROVIDER_ERROR"
            result, status = None, "ERROR"
            error = StructuredExecutionError(code=code, retryable=code == "PROVIDER_ERROR", message_sanitized=code)
        return TelegramExecutionResponseV1(protocol_version=PROTOCOL_VERSION, request_id=request.request_id, account_id=request.account_id, operation=request.operation, executor_region=self.region, executor_version_sha=self.version_sha, started_at=started, finished_at=datetime.now(timezone.utc), status=status, owner_assertion=request.owner_identifier, normalized_result=result, error=error)

