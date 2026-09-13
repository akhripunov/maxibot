"""Verified, bounded Yandex Direct mutations for Autopilot V1.

The adapter knows no UI or policy.  It accepts only a Control-Plane request
that has already passed object/campaign/account guards, then reads provider
state before and after the one mutation.  Missing target details are denied.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timezone
import json
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


def _micros(amount: Any, field: str) -> int:
    """Convert a currency amount to provider micros without float drift."""
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{field}_REQUIRED")
    if value <= 0:
        raise ValueError(f"{field}_REQUIRED")
    return int((value * Decimal("1000000")).to_integral_value(rounding=ROUND_HALF_UP))


def _pay_for_conversion(campaign: dict[str, Any]) -> dict[str, Any] | None:
    network = (((campaign.get("TextCampaign") or {}).get("BiddingStrategy") or {}).get("Network") or {})
    if str(network.get("BiddingStrategyType") or "").upper() != "PAY_FOR_CONVERSION":
        return None
    return network.get("PayForConversion") if isinstance(network.get("PayForConversion"), dict) else {}


def _network_strategy(campaign: dict[str, Any]) -> tuple[str, dict[str, Any], str] | None:
    """Return explicit strategy, mutable settings object and budget field."""
    network = (((campaign.get("TextCampaign") or {}).get("BiddingStrategy") or {}).get("Network") or {})
    kind = str(network.get("BiddingStrategyType") or "").upper()
    if kind == "PAY_FOR_CONVERSION" and isinstance(network.get("PayForConversion"), dict):
        return kind, network["PayForConversion"], "PayForConversion.WeeklySpendLimit"
    if kind == "WB_MAXIMUM_CONVERSION_RATE" and isinstance(network.get("WbMaximumConversionRate"), dict):
        return kind, network["WbMaximumConversionRate"], "WbMaximumConversionRate.WeeklySpendLimit"
    if campaign.get("DailyBudget") is not None:
        return "DAILY_BUDGET", campaign, "DailyBudget"
    return None


def budget_capability_attestation(campaign: dict[str, Any]) -> dict[str, Any]:
    """Attest only an independently readable campaign-level budget primitive."""
    route = _network_strategy(campaign)
    if route:
        strategy, budget, field = route
        if strategy in {"PAY_FOR_CONVERSION", "WB_MAXIMUM_CONVERSION_RATE"} and budget.get("WeeklySpendLimit") is not None:
            return {"verified": True, "strategy": strategy, "budget_field": field, "current_value_micros": budget["WeeklySpendLimit"]}
        if strategy == "DAILY_BUDGET":
            daily = campaign.get("DailyBudget") or {}
            if isinstance(daily, dict) and daily.get("Amount") is not None:
                return {"verified": True, "strategy": strategy, "budget_field": field, "current_value_micros": daily["Amount"]}
    return {"verified": False, "reason": "UNSUPPORTED_OR_UNREADABLE_BUDGET_STRATEGY"}


def record_strategy_observation(campaign: Any, provider_campaign: dict[str, Any], *, observed_at: datetime | None = None) -> dict[str, Any]:
    """Append a provider strategy observation to existing campaign metadata."""
    observed = observed_at or datetime.now(timezone.utc)
    attestation = budget_capability_attestation(provider_campaign)
    strategy = _network_strategy(provider_campaign)
    if strategy:
        strategy_name, settings, field = strategy
        # Keep the provider's operational budget separate from MaxiBot's
        # promotion guardrails (max_test_spend / experiment cap).  These
        # normalized columns are the source consumed by analysis; the raw
        # provider payload remains available in metadata for audit.
        campaign.strategy_type = strategy_name
        campaign.budget_type = settings.get("BudgetType")
        weekly_micros = settings.get("WeeklySpendLimit")
        if strategy_name in {"PAY_FOR_CONVERSION", "WB_MAXIMUM_CONVERSION_RATE"} and weekly_micros is not None:
            campaign.weekly_budget = (Decimal(str(weekly_micros)) / Decimal("1000000")).quantize(Decimal("0.000001"))
            campaign.daily_budget = None
        elif strategy_name == "DAILY_BUDGET":
            daily = provider_campaign.get("DailyBudget") or {}
            campaign.daily_budget = (Decimal(str(daily.get("Amount"))) / Decimal("1000000")).quantize(Decimal("0.000001")) if daily.get("Amount") is not None else None
            campaign.weekly_budget = None
        campaign.currency = str(settings.get("Currency") or provider_campaign.get("Currency") or "RUB")
        observation = {
            "observed_at": observed.isoformat(),
            "provider_campaign_id": str(provider_campaign.get("Id") or campaign.external_id),
            "strategy_type": strategy_name,
            "budget_field": field,
            "budget_value_micros": settings.get("WeeklySpendLimit") if strategy_name != "DAILY_BUDGET" else (provider_campaign.get("DailyBudget") or {}).get("Amount"),
            "budget_type": settings.get("BudgetType") if strategy_name != "DAILY_BUDGET" else None,
            "goal_id": settings.get("GoalId") if strategy_name != "DAILY_BUDGET" else None,
        }
    else:
        # A successful provider reread that no longer exposes a supported
        # budget primitive must not leave an old campaign budget usable.
        campaign.strategy_type = None
        campaign.budget_type = None
        campaign.weekly_budget = None
        campaign.daily_budget = None
        observation = {"observed_at": observed.isoformat(), "provider_campaign_id": str(provider_campaign.get("Id") or campaign.external_id), "strategy_type": None, "budget_field": None, "budget_value_micros": None, "budget_type": None, "goal_id": None}
    try:
        metadata = json.loads(campaign.provider_metadata_json or "{}")
    except (TypeError, ValueError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    history = metadata.setdefault("strategy_observations", [])
    if not isinstance(history, list):
        history = []
        metadata["strategy_observations"] = history
    history.append(observation)
    metadata["latest_strategy_observation"] = observation
    campaign.provider_metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    campaign.metadata_updated_at = observed
    return {"observation": observation, "attestation": attestation}


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
            route = _network_strategy(before.get("campaign") or {})
            if route and route[0] in {"PAY_FOR_CONVERSION", "WB_MAXIMUM_CONVERSION_RATE"}:
                strategy_name, settings, _ = route
                previous = settings.get("WeeklySpendLimit")
                if previous is None:
                    raise ProviderVerificationError("ROLLBACK_BUDGET_FIELD_UNAVAILABLE")
                key = "PayForConversion" if strategy_name == "PAY_FOR_CONVERSION" else "WbMaximumConversionRate"
                restored = deepcopy(settings)
                restored["WeeklySpendLimit"] = previous
                payload = {"Id": campaign_id, "TextCampaign": {"BiddingStrategy": {"Network": {"BiddingStrategyType": strategy_name, key: restored}}}}
                response = await self.client.mutate("campaigns", method="update", params={"Campaigns": [payload]})
                after = await self._campaign(campaign_id)
                after_route = _network_strategy(after)
                if not after_route or after_route[0] != strategy_name or after_route[1].get("WeeklySpendLimit") != previous:
                    raise ProviderVerificationError("ROLLBACK_BUDGET_NOT_VERIFIED")
                return {"rollback_action": f"RESTORE_{key.upper()}_WEEKLY_SPEND_LIMIT", "provider_response": response, "verified_after_state": {"campaign": after}}
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
        before = await self._campaign(campaign_id)
        expected_strategy = str(target.get("expected_strategy_type") or "").upper()
        observed_network = (((before.get("TextCampaign") or {}).get("BiddingStrategy") or {}).get("Network") or {})
        observed_strategy = str(observed_network.get("BiddingStrategyType") or "").upper()
        if expected_strategy and expected_strategy != observed_strategy:
            raise ProviderVerificationError("PROVIDER_CONFIG_DRIFT")
        route = _network_strategy(before)
        if route and route[0] in {"PAY_FOR_CONVERSION", "WB_MAXIMUM_CONVERSION_RATE"}:
            strategy_name, current_settings, _ = route
            amount = _micros(target.get("weekly_budget_amount"), "WEEKLY_BUDGET_AMOUNT")
            cpa = current_settings.get("Cpa")
            if strategy_name == "PAY_FOR_CONVERSION" and cpa is not None and amount < int(cpa) * 20:
                raise ValueError("WEEKLY_BUDGET_BELOW_CPA_MINIMUM")
            expected_field = str(target.get("expected_budget_field") or "")
            expected_current = target.get("expected_current_weekly_budget_micros")
            actual_field = f"TextCampaign.BiddingStrategy.Network.{strategy_name.title().replace('_', '')}.WeeklySpendLimit"
            if expected_strategy and expected_strategy != strategy_name:
                raise ProviderVerificationError("PROVIDER_CONFIG_DRIFT")
            if expected_field and expected_field not in {actual_field, f"{strategy_name}.WeeklySpendLimit"}:
                raise ProviderVerificationError("PROVIDER_CONFIG_DRIFT")
            current_amount = current_settings.get("WeeklySpendLimit")
            if expected_current is not None and int(expected_current) != int(current_amount or -1):
                raise ProviderVerificationError("PROVIDER_CONFIG_DRIFT")
            if current_amount is None:
                raise ProviderVerificationError("BUDGET_FIELD_UNAVAILABLE")
            max_change = target.get("max_budget_change_percent")
            if max_change is not None and abs(amount - int(current_amount)) / int(current_amount) * 100 > float(max_change):
                raise ValueError("BUDGET_CHANGE_EXCEEDS_BOUND")
            strategy = deepcopy((before.get("TextCampaign") or {}).get("BiddingStrategy") or {})
            network = strategy.setdefault("Network", {})
            key = "PayForConversion" if strategy_name == "PAY_FOR_CONVERSION" else "WbMaximumConversionRate"
            current = deepcopy(network.get(key) or {})
            current["WeeklySpendLimit"] = amount
            network["BiddingStrategyType"] = strategy_name
            network[key] = current
            payload = {"Id": campaign_id, "TextCampaign": {"BiddingStrategy": strategy}}
            response = await self.client.mutate("campaigns", method="update", params={"Campaigns": [payload]})
            after = await self._campaign(campaign_id)
            after_route = _network_strategy(after)
            if not after_route or after_route[0] != strategy_name or after_route[1].get("WeeklySpendLimit") != amount:
                raise ProviderVerificationError("BUDGET_NOT_VERIFIED")
            return {"before_state": {"campaign": before}, "requested_mutation": {"resource": "campaigns", "method": "update", "weekly_spend_limit": amount}, "provider_response": response, "verified_after_state": {"campaign": after}}
        amount = _id(target.get("daily_budget_amount"), "DAILY_BUDGET_AMOUNT")
        mode = str(target.get("daily_budget_mode") or "STANDARD").upper()
        if mode not in {"STANDARD", "DISTRIBUTED"}:
            raise ValueError("DAILY_BUDGET_MODE_INVALID")
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
