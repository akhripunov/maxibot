"""Provider-neutral account routing and regional safety boundary.

Release A supports only the RU runtime.  Non-local accounts fail closed until
an explicit remote executor is installed; they must never silently fall back
to the local worker.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

LOCAL_EXECUTION_REGION = "RU"
SUPPORTED_REGIONS = frozenset({"RU", "EU"})


class AdsRegionalError(RuntimeError):
    """Typed fail-closed regional execution/storage error."""


@dataclass(frozen=True)
class AdsAccountRoute:
    account_id: str
    storage_region: str
    execution_region: str


def _region(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip().upper()
    if normalized not in SUPPORTED_REGIONS:
        raise AdsRegionalError(f"INVALID_{field.upper()}")
    return normalized


def resolve_ads_account_route(account: Any) -> AdsAccountRoute:
    if account is None or not getattr(account, "id", None):
        raise AdsRegionalError("ADS_ACCOUNT_REQUIRED")
    return AdsAccountRoute(
        account_id=str(account.id),
        storage_region=_region(getattr(account, "storage_region", None) or "RU", field="storage_region"),
        execution_region=_region(getattr(account, "execution_region", None) or "RU", field="execution_region"),
    )


def assert_storage_supported(account: Any) -> AdsAccountRoute:
    route = resolve_ads_account_route(account)
    if route.storage_region != LOCAL_EXECUTION_REGION:
        raise AdsRegionalError("STORAGE_REGION_UNAVAILABLE")
    return route


def assert_execution_supported(account: Any) -> AdsAccountRoute:
    route = assert_storage_supported(account)
    if route.execution_region != LOCAL_EXECUTION_REGION:
        raise AdsRegionalError("EXECUTION_REGION_UNAVAILABLE")
    return route
