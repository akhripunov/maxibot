import pytest

from app.ads.yandex_autopilot import YandexAutopilotAdapter


class FakeDirect:
    def __init__(self):
        self.ads = {11: {"Id": 11, "CampaignId": 101, "State": "ON", "Status": "ACCEPTED"}}
        self.campaign = {"Id": 101, "ExcludedSites": {"Items": []}, "DailyBudget": {"Amount": 1_000_000, "Mode": "STANDARD"}}

    async def get_ads_by_ids(self, ids):
        return [dict(self.ads[item]) for item in ids if item in self.ads]

    async def get_campaign_configuration(self, campaign_id):
        return dict(self.campaign) if int(campaign_id) == 101 else None

    async def mutate(self, resource, *, method, params):
        if resource == "ads" and method in {"suspend", "resume"}:
            for item in params["SelectionCriteria"]["Ids"]:
                self.ads[item]["State"] = "OFF" if method == "suspend" else "ON"
            return {}
        if resource == "campaigns":
            update = params["Campaigns"][0]
            if "ExcludedSites" in update:
                self.campaign["ExcludedSites"] = update["ExcludedSites"]
            if "DailyBudget" in update:
                self.campaign["DailyBudget"] = update["DailyBudget"]
            return {}
        if resource == "ads" and method == "add":
            self.ads[12] = {"Id": 12, "CampaignId": 101, "State": "ON", "Status": "DRAFT"}
            return {"AddResults": [{"Id": 12}]}
        raise AssertionError((resource, method, params))


@pytest.mark.asyncio
async def test_pause_ad_collects_verified_before_after_and_real_rollback():
    adapter = YandexAutopilotAdapter(FakeDirect())
    result = await adapter.execute({"action_type": "PAUSE_AD", "provider_campaign_id": 101, "target": {"ad_id": 11}})
    assert result["before_state"]["ad"]["State"] == "ON"
    assert result["verified_after_state"]["ad"]["State"] == "OFF"
    rollback = await adapter.rollback({"action_type": "PAUSE_AD", "target": {"provider_campaign_id": 101, "ad_id": 11}, "before_state": result["before_state"]})
    assert rollback["verified_after_state"]["ad"]["State"] == "ON"


@pytest.mark.asyncio
async def test_enable_ad_and_restore_placement_are_verified():
    direct = FakeDirect()
    direct.ads[11]["State"] = "OFF"
    direct.campaign["ExcludedSites"] = {"Items": ["example.test"]}
    adapter = YandexAutopilotAdapter(direct)
    enabled = await adapter.execute({"action_type": "ENABLE_AD", "provider_campaign_id": 101, "target": {"ad_id": 11}})
    assert enabled["verified_after_state"]["ad"]["State"] == "ON"
    restored = await adapter.execute({"action_type": "RESTORE_PLACEMENT", "provider_campaign_id": 101, "target": {"placement": "example.test"}})
    assert restored["verified_after_state"]["campaign"]["ExcludedSites"]["Items"] == []


@pytest.mark.asyncio
async def test_placement_and_budget_mutations_verify_provider_state():
    adapter = YandexAutopilotAdapter(FakeDirect())
    placement = await adapter.execute({"action_type": "EXCLUDE_PLACEMENT", "provider_campaign_id": 101, "target": {"placement": "example.test"}})
    assert placement["verified_after_state"]["campaign"]["ExcludedSites"]["Items"] == ["example.test"]
    budget = await adapter.execute({"action_type": "BOUNDED_BUDGET_REALLOCATION", "provider_campaign_id": 101, "target": {"daily_budget_amount": 2_000_000}})
    assert budget["verified_after_state"]["campaign"]["DailyBudget"]["Amount"] == 2_000_000


class PayForConversionDirect(FakeDirect):
    def __init__(self):
        super().__init__()
        self.campaign = {"Id": 101, "ExcludedSites": {"Items": []}, "DailyBudget": None,
                         "TextCampaign": {"BiddingStrategy": {"Network": {
                             "BiddingStrategyType": "PAY_FOR_CONVERSION",
                             "PayForConversion": {"GoalId": 7, "Cpa": 20_000_000,
                                                   "WeeklySpendLimit": 2_000_000_000},
                         }, "Search": {"BiddingStrategyType": "SERVING_OFF"}}}}

    async def mutate(self, resource, *, method, params):
        if resource == "campaigns" and method == "update":
            update = params["Campaigns"][0]
            if "TextCampaign" in update:
                self.campaign["TextCampaign"] = update["TextCampaign"]
            return {}
        return await super().mutate(resource, method=method, params=params)


@pytest.mark.asyncio
@pytest.mark.parametrize("amount,micros", [(1800, 1_800_000_000), (2200, 2_200_000_000)])
async def test_pay_for_conversion_uses_weekly_spend_limit_and_preserves_strategy(amount, micros):
    direct = PayForConversionDirect()
    adapter = YandexAutopilotAdapter(direct)
    result = await adapter.execute({"action_type": "BOUNDED_BUDGET_REALLOCATION", "provider_campaign_id": 101,
                                    "target": {"weekly_budget_amount": amount}})
    payload = direct.campaign["TextCampaign"]["BiddingStrategy"]["Network"]["PayForConversion"]
    assert payload["WeeklySpendLimit"] == micros
    assert payload["GoalId"] == 7
    assert payload["Cpa"] == 20_000_000
    assert result["verified_after_state"]["campaign"]["TextCampaign"]["BiddingStrategy"]["Network"]["BiddingStrategyType"] == "PAY_FOR_CONVERSION"


@pytest.mark.asyncio
async def test_pay_for_conversion_rejects_budget_below_cpa_minimum():
    adapter = YandexAutopilotAdapter(PayForConversionDirect())
    with pytest.raises(ValueError, match="WEEKLY_BUDGET_BELOW_CPA_MINIMUM"):
        await adapter.execute({"action_type": "BOUNDED_BUDGET_REALLOCATION", "provider_campaign_id": 101,
                               "target": {"weekly_budget_amount": 399}})


@pytest.mark.asyncio
async def test_pay_for_conversion_accepts_exact_cpa_minimum():
    result = await YandexAutopilotAdapter(PayForConversionDirect()).execute(
        {"action_type": "BOUNDED_BUDGET_REALLOCATION", "provider_campaign_id": 101,
         "target": {"weekly_budget_amount": 400}}
    )
    assert result["verified_after_state"]["campaign"]["TextCampaign"]["BiddingStrategy"]["Network"]["PayForConversion"]["WeeklySpendLimit"] == 400_000_000


@pytest.mark.asyncio
async def test_pay_for_conversion_reread_mismatch_is_not_verified():
    class Mismatch(PayForConversionDirect):
        async def get_campaign_configuration(self, campaign_id):
            row = await super().get_campaign_configuration(campaign_id)
            if row:
                row["TextCampaign"]["BiddingStrategy"]["Network"]["PayForConversion"]["WeeklySpendLimit"] += 1
            return row
    with pytest.raises(Exception, match="BUDGET_NOT_VERIFIED"):
        await YandexAutopilotAdapter(Mismatch()).execute(
            {"action_type": "BOUNDED_BUDGET_REALLOCATION", "provider_campaign_id": 101,
             "target": {"weekly_budget_amount": 1800}}
        )


@pytest.mark.asyncio
async def test_pay_for_conversion_rollback_restores_weekly_budget():
    direct = PayForConversionDirect()
    adapter = YandexAutopilotAdapter(direct)
    result = await adapter.execute({"action_type": "BOUNDED_BUDGET_REALLOCATION", "provider_campaign_id": 101,
                                    "target": {"weekly_budget_amount": 1800}})
    rollback = await adapter.rollback({"action_type": "BOUNDED_BUDGET_REALLOCATION",
                                       "target": {"provider_campaign_id": 101},
                                       "before_state": result["before_state"]})
    assert rollback["verified_after_state"]["campaign"]["TextCampaign"]["BiddingStrategy"]["Network"]["PayForConversion"]["WeeklySpendLimit"] == 2_000_000_000


@pytest.mark.asyncio
async def test_create_ad_test_requires_precise_payload_and_verifies_created_ad():
    adapter = YandexAutopilotAdapter(FakeDirect())
    result = await adapter.execute({"action_type": "CREATE_AD_TEST", "provider_campaign_id": 101, "target": {"ad_group_id": 7, "ad_payload": {"TextAd": {"Title": "Тест", "Text": "Текст", "Href": "https://example.test", "Mobile": "NO"}}}})
    assert result["verified_after_state"]["created_ad_id"] == 12


@pytest.mark.asyncio
async def test_create_creative_test_uses_same_verified_ad_creation_path():
    adapter = YandexAutopilotAdapter(FakeDirect())
    result = await adapter.execute({"action_type": "CREATE_CREATIVE_TEST", "provider_campaign_id": 101, "target": {"ad_group_id": 7, "ad_payload": {"TextImageAd": {"AdImageHash": "image-hash", "Href": "https://example.test"}}}})
    assert result["verified_after_state"]["ad"]["CampaignId"] == 101


@pytest.mark.asyncio
async def test_created_ad_rollback_accepts_verified_after_state_contract():
    direct = FakeDirect()
    adapter = YandexAutopilotAdapter(direct)
    result = await adapter.execute({"action_type": "CREATE_AD_TEST", "provider_campaign_id": 101, "target": {"ad_group_id": 7, "ad_payload": {"TextAd": {"Title": "Тест", "Text": "Текст", "Href": "https://example.test"}}}})
    rollback = await adapter.rollback({"action_type": "CREATE_AD_TEST", "target": {"provider_campaign_id": 101}, "before_state": result["before_state"], "verified_after_state": result["verified_after_state"]})
    assert rollback["verified_after_state"]["ad"]["State"] == "OFF"


@pytest.mark.asyncio
async def test_adgroup_campaign_scope_is_fail_closed_when_provider_can_verify():
    class GroupAware(FakeDirect):
        async def get_ad_groups_by_ids(self, ids):
            return [{"Id": ids[0], "CampaignId": 999}]

    adapter = YandexAutopilotAdapter(GroupAware())
    with pytest.raises(Exception, match="AD_GROUP_CAMPAIGN_SCOPE_NOT_VERIFIED"):
        await adapter.execute({"action_type": "CREATE_AD_TEST", "provider_campaign_id": 101, "target": {"ad_group_id": 7, "ad_payload": {"TextAd": {"Title": "Тест", "Text": "Текст", "Href": "https://example.test"}}}})
