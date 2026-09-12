"""Verified, bounded Yandex Direct mutations for Autopilot V1.

The adapter knows no UI or policy.  It accepts only a Control-Plane request
that has already passed object/campaign/account guards, then reads provider
state before and after the one mutation.  Missing target details are denied.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.integrations.yandex_direct import YandexDirectClient
from app.ads.creative_experiment import measurement_start, provider_lifecycle


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

    async def refresh_ad_lifecycle(self, campaign_id: int | str, ad_id: int | str) -> dict[str, Any]:
        """Reread a created treatment and expose the canonical moderation state."""
        row = await self._ad(_id(campaign_id, "PROVIDER_CAMPAIGN_ID"), _id(ad_id, "AD_ID"))
        return {"ad": row, "provider_lifecycle": provider_lifecycle(row)}

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
            after_state = evidence.get("after_state") or evidence.get("verified_after_state") or {}
            ad_id = _id(after_state.get("created_ad_id"), "CREATED_AD_ID")
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
        control_ad_id = target.get("control_ad_id")
        control = await self._ad(campaign_id, _id(control_ad_id, "CONTROL_AD_ID")) if control_ad_id else None
        get_groups = getattr(self.client, "get_ad_groups_by_ids", None)
        if get_groups is not None:
            groups = await get_groups([ad_group_id])
            group = next((row for row in groups if int(row.get("Id", 0)) == ad_group_id), None)
            if not group or int(group.get("CampaignId", 0)) != campaign_id:
                raise ProviderVerificationError("AD_GROUP_CAMPAIGN_SCOPE_NOT_VERIFIED")
        # CREATE_* is a child-object action.  Campaigns.get may be unavailable
        # for report-only campaign types; the verified AdGroup ownership and
        # post-create ad reread are the required scope proofs instead.
        before_campaign = None
        image_hash = target.get("provider_image_hash")
        image_bytes = target.get("image_bytes")
        if image_bytes and not image_hash:
            add_image = getattr(self.client, "add_ad_image", None)
            if add_image is None:
                raise ProviderVerificationError("PROVIDER_IMAGE_UPLOAD_UNAVAILABLE")
            image_result = await add_image(image_bytes)
            rows = image_result.get("AddResults") or image_result.get("addResults") or []
            image_hash = rows[0].get("AdImageHash") if rows and isinstance(rows[0], dict) else None
            if not image_hash:
                raise ProviderVerificationError("PROVIDER_IMAGE_ID_MISSING")
            target["provider_image_hash"] = image_hash
        request_ad = {"AdGroupId": ad_group_id, **deepcopy(ad_payload)}
        if image_hash:
            for key in ("TextImageAd", "TextAd", "TextAdBuilderAd", "ResponsiveAd"):
                if isinstance(request_ad.get(key), dict):
                    request_ad[key].setdefault("AdImageHash", image_hash)
        response = await self.client.mutate("ads", method="add", params={"Ads": [request_ad]})
        created_ad_id = _result_id(response)
        after = await self._ad(campaign_id, created_ad_id)
        lifecycle = provider_lifecycle(after, created=True)
        return {
            "before_state": {"campaign": before_campaign, "control_ad": control},
            "requested_mutation": {"resource": "ads", "method": "add", "ad_group_id": ad_group_id, "action": action},
            "provider_response": response,
            "verified_after_state": {
                "created_ad_id": created_ad_id, "ad": after, "provider_lifecycle": lifecycle,
                "provider_image_hash": image_hash,
                "measurement_started_at": measurement_start(lifecycle).isoformat() if measurement_start(lifecycle) else None,
            },
        }


def yandex_autopilot_adapter(token: str) -> YandexAutopilotAdapter:
    """The sole construction point for an intentionally write-enabled client."""
    return YandexAutopilotAdapter(YandexDirectClient(token, allow_write=True))
