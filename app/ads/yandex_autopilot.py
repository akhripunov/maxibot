"""Verified, bounded Yandex Direct mutations for Autopilot V1.

The adapter knows no UI or policy.  It accepts only a Control-Plane request
that has already passed object/campaign/account guards, then reads provider
state before and after the one mutation.  Missing target details are denied.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.integrations.yandex_direct import YandexDirectClient


class ProviderVerificationError(RuntimeError):
    pass


def _id(value: Any, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field}_REQUIRED")
    if result <= 0:
        raise ValueError(f"{field}_REQUIRED")
    return result


def _items(row: dict[str, Any] | None, key: str) -> list[str]:
    value = (row or {}).get(key)
    if isinstance(value, dict):
        value = value.get("Items")
    return [str(item) for item in value or []]


def _result_id(response: dict[str, Any]) -> int:
    rows = response.get("AddResults") or response.get("addResults") or []
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ProviderVerificationError("PROVIDER_ADD_RESPONSE_INVALID")
    return _id(rows[0].get("Id"), "PROVIDER_CREATED_AD_ID")


class YandexAutopilotAdapter:
    """Concrete Yandex Direct V1 action adapter with verified rollback data."""

    def __init__(self, client: YandexDirectClient):
        self.client = client

    async def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        action = str(request.get("action_type") or "").upper()
        target = deepcopy(request.get("target") or {})
        campaign_id = _id(target.get("provider_campaign_id") or request.get("provider_campaign_id"), "PROVIDER_CAMPAIGN_ID")
        if action in {"PAUSE_AD", "ENABLE_AD"}:
            return await self._ad_state(action, campaign_id, target)
        if action in {"EXCLUDE_PLACEMENT", "RESTORE_PLACEMENT"}:
            return await self._placement(action, campaign_id, target)
        if action == "BOUNDED_BUDGET_REALLOCATION":
            return await self._budget(campaign_id, target)
        if action in {"CREATE_AD_TEST", "CREATE_CREATIVE_TEST"}:
            return await self._create_ad_test(action, campaign_id, target)
        raise ValueError("ACTION_NOT_ALLOWLISTED")

    async def rollback(self, evidence: dict[str, Any]) -> dict[str, Any]:
        """Non-destructive rollback using the immutable before-state evidence."""
        action = str(evidence.get("action_type") or "").upper()
        before = evidence.get("before_state") or {}
        target = evidence.get("target") or {}
        campaign_id = _id(target.get("provider_campaign_id"), "PROVIDER_CAMPAIGN_ID")
        if action in {"PAUSE_AD", "ENABLE_AD"}:
            ad_id = _id(target.get("ad_id"), "AD_ID")
            state = str((before.get("ad") or {}).get("State") or "").upper()
            method = "resume" if state == "ON" else "suspend"
            response = await self.client.mutate("ads", method=method, params={"SelectionCriteria": {"Ids": [ad_id]}})
            after = await self._ad(campaign_id, ad_id)
            if str(after.get("State") or "").upper() != state:
                raise ProviderVerificationError("ROLLBACK_AD_STATE_NOT_VERIFIED")
            return {"rollback_action": method.upper(), "provider_response": response, "verified_after_state": {"ad": after}}
        if action in {"EXCLUDE_PLACEMENT", "RESTORE_PLACEMENT"}:
            items = _items(before.get("campaign"), "ExcludedSites")
            response = await self.client.mutate("campaigns", method="update", params={"Campaigns": [{"Id": campaign_id, "ExcludedSites": {"Items": items}}]})
            after = await self._campaign(campaign_id)
            if _items(after, "ExcludedSites") != items:
                raise ProviderVerificationError("ROLLBACK_PLACEMENTS_NOT_VERIFIED")
            return {"rollback_action": "RESTORE_EXCLUDED_SITES", "provider_response": response, "verified_after_state": {"campaign": after}}
        if action == "BOUNDED_BUDGET_REALLOCATION":
            budget = (before.get("campaign") or {}).get("DailyBudget")
            response = await self.client.mutate("campaigns", method="update", params={"Campaigns": [{"Id": campaign_id, "DailyBudget": budget}]})
            after = await self._campaign(campaign_id)
            if after.get("DailyBudget") != budget:
                raise ProviderVerificationError("ROLLBACK_BUDGET_NOT_VERIFIED")
            return {"rollback_action": "RESTORE_DAILY_BUDGET", "provider_response": response, "verified_after_state": {"campaign": after}}
        if action in {"CREATE_AD_TEST", "CREATE_CREATIVE_TEST"}:
            ad_id = _id((evidence.get("after_state") or {}).get("created_ad_id"), "CREATED_AD_ID")
            response = await self.client.mutate("ads", method="suspend", params={"SelectionCriteria": {"Ids": [ad_id]}})
            after = await self._ad(campaign_id, ad_id)
            if str(after.get("State") or "").upper() != "OFF":
                raise ProviderVerificationError("ROLLBACK_CREATED_AD_NOT_PAUSED")
            return {"rollback_action": "PAUSE_CREATED_TEST_AD", "provider_response": response, "verified_after_state": {"ad": after}}
        raise ValueError("ROLLBACK_ACTION_UNSUPPORTED")

    async def _ad(self, campaign_id: int, ad_id: int) -> dict[str, Any]:
        rows = await self.client.get_ads_by_ids([ad_id])
        row = next((item for item in rows if int(item.get("Id", 0)) == ad_id), None)
        if not row or int(row.get("CampaignId", 0)) != campaign_id:
            raise ProviderVerificationError("AD_SCOPE_NOT_VERIFIED")
        return row

    async def _campaign(self, campaign_id: int) -> dict[str, Any]:
        row = await self.client.get_campaign_configuration(campaign_id)
        if not row:
            raise ProviderVerificationError("CAMPAIGN_NOT_FOUND")
        return row

    async def _ad_state(self, action: str, campaign_id: int, target: dict[str, Any]) -> dict[str, Any]:
        ad_id = _id(target.get("ad_id"), "AD_ID")
        before = await self._ad(campaign_id, ad_id)
        method, expected = ("suspend", "OFF") if action == "PAUSE_AD" else ("resume", "ON")
        response = await self.client.mutate("ads", method=method, params={"SelectionCriteria": {"Ids": [ad_id]}})
        after = await self._ad(campaign_id, ad_id)
        if str(after.get("State") or "").upper() != expected:
            raise ProviderVerificationError("AD_STATE_NOT_VERIFIED")
        return {"before_state": {"ad": before}, "requested_mutation": {"resource": "ads", "method": method, "ad_id": ad_id}, "provider_response": response, "verified_after_state": {"ad": after}}

    async def _placement(self, action: str, campaign_id: int, target: dict[str, Any]) -> dict[str, Any]:
        placement = str(target.get("placement") or "").strip()
        if not placement or len(placement) > 255:
            raise ValueError("PLACEMENT_REQUIRED")
        before = await self._campaign(campaign_id)
        old = _items(before, "ExcludedSites")
        items = list(dict.fromkeys(old + [placement])) if action == "EXCLUDE_PLACEMENT" else [item for item in old if item != placement]
        if len(items) > 1000:
            raise ValueError("EXCLUDED_SITES_LIMIT")
        response = await self.client.mutate("campaigns", method="update", params={"Campaigns": [{"Id": campaign_id, "ExcludedSites": {"Items": items}}]})
        after = await self._campaign(campaign_id)
        if _items(after, "ExcludedSites") != items:
            raise ProviderVerificationError("PLACEMENT_NOT_VERIFIED")
        return {"before_state": {"campaign": before}, "requested_mutation": {"resource": "campaigns", "method": "update", "excluded_sites": items}, "provider_response": response, "verified_after_state": {"campaign": after}}

    async def _budget(self, campaign_id: int, target: dict[str, Any]) -> dict[str, Any]:
        amount = _id(target.get("daily_budget_amount"), "DAILY_BUDGET_AMOUNT")
        mode = str(target.get("daily_budget_mode") or "STANDARD").upper()
        if mode not in {"STANDARD", "DISTRIBUTED"}:
            raise ValueError("DAILY_BUDGET_MODE_INVALID")
        before = await self._campaign(campaign_id)
        response = await self.client.mutate("campaigns", method="update", params={"Campaigns": [{"Id": campaign_id, "DailyBudget": {"Amount": amount, "Mode": mode}}]})
        after = await self._campaign(campaign_id)
        if (after.get("DailyBudget") or {}).get("Amount") != amount:
            raise ProviderVerificationError("BUDGET_NOT_VERIFIED")
        return {"before_state": {"campaign": before}, "requested_mutation": {"resource": "campaigns", "method": "update", "daily_budget": {"Amount": amount, "Mode": mode}}, "provider_response": response, "verified_after_state": {"campaign": after}}

    async def _create_ad_test(self, action: str, campaign_id: int, target: dict[str, Any]) -> dict[str, Any]:
        ad_group_id = _id(target.get("ad_group_id"), "AD_GROUP_ID")
        ad_payload = target.get("ad_payload")
        if not isinstance(ad_payload, dict) or not any(key in ad_payload for key in ("TextAd", "TextImageAd", "TextAdBuilderAd", "ResponsiveAd")):
            raise ValueError("TEST_AD_PAYLOAD_REQUIRED")
        request_ad = {"AdGroupId": ad_group_id, **deepcopy(ad_payload)}
        response = await self.client.mutate("ads", method="add", params={"Ads": [request_ad]})
        created_ad_id = _result_id(response)
        after = await self._ad(campaign_id, created_ad_id)
        return {"before_state": {"campaign": await self._campaign(campaign_id)}, "requested_mutation": {"resource": "ads", "method": "add", "ad_group_id": ad_group_id, "action": action}, "provider_response": response, "verified_after_state": {"created_ad_id": created_ad_id, "ad": after}}


def yandex_autopilot_adapter(token: str) -> YandexAutopilotAdapter:
    """The sole construction point for an intentionally write-enabled client."""
    return YandexAutopilotAdapter(YandexDirectClient(token, allow_write=True))
