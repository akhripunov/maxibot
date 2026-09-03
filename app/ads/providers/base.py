from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class ProviderReport:
    provider: str
    report_type: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    requested_fields: tuple[str, ...] = ()
    date_from: date | None = None
    date_to: date | None = None
    timezone: str = "Europe/Moscow"
    fetched_at: datetime | None = None
    status: str = "SUCCESS"
    error_code: str | None = None
    freshness_caveat: str | None = None
    provider_report_id: str | None = None


class AdsProvider(ABC):
    provider_name: str

    @abstractmethod
    async def campaigns(self, campaign_ids: list[int] | None = None) -> ProviderReport: ...

    @abstractmethod
    async def campaign_performance(self, campaign_id: int, *, date_from: date, date_to: date) -> ProviderReport: ...

    @abstractmethod
    async def ads(self, campaign_ids: list[int]) -> ProviderReport: ...

    async def unsupported(self, report_type: str) -> ProviderReport:
        return ProviderReport(provider=self.provider_name, report_type=report_type, status="NOT_SUPPORTED", error_code="PROVIDER_DIMENSION_UNAVAILABLE")
