import pytest

from app.ads.replacement_campaign import (
    LEGACY_CAMPAIGN_ID,
    LegacyCampaignAudit,
    build_replacement_spec,
    cutover_plan,
    provision_replacement,
    YandexReplacementProvider,
)
from app.ads.autopilot import assess_provider_capabilities


def _spec(**overrides):
    values = {
        "name": "Москва 365 · управляемая",
        "landing_url": "https://example.test/join?foo=bar",
        "objective": "userDidSubscribe",
        "geo": [" Москва "],
        "strategy": {"type": "AVERAGE_CPA", "target": 20},
        "budget": {"Amount": 1000, "Mode": "STANDARD"},
    }
    values.update(overrides)
    return build_replacement_spec(**values)


def test_report_only_audit_is_not_directly_manageable():
    audit = LegacyCampaignAudit(LEGACY_CAMPAIGN_ID, "msk365_mk_msk_conv_test1", True, False, False, False, False)
    assert audit.product_type == "MASTER_CAMPAIGN_OR_WIZARD_REPORT_ONLY"
    assert audit.directly_manageable is False


def test_replacement_spec_is_deterministic_and_preserves_attribution():
    first = _spec()
    second = _spec()
    assert first == second
    assert "utm_source=yandex_direct" in first.attribution_url
    assert "utm_medium=cpc" in first.attribution_url
    assert "utm_campaign=%D0%9C%D0%BE%D1%81%D0%BA%D0%B2%D0%B0+365+%C2%B7+%D1%83%D0%BF%D1%80%D0%B0%D0%B2%D0%BB%D1%8F%D0%B5%D0%BC%D0%B0%D1%8F" in first.attribution_url
    assert first.campaign_payload()["State"] == "OFF"


@pytest.mark.asyncio
async def test_default_replacement_path_is_plan_only():
    class NeverCalled:
        def __getattr__(self, name):
            raise AssertionError(f"live provider call: {name}")

    result = await provision_replacement(spec=_spec(), provider=NeverCalled())
    assert result["status"] == "PLANNED"
    assert result["old_campaign_write"] == "DENIED"
    assert result["new_campaign_write"] == "READY_BUT_DISABLED"


@pytest.mark.asyncio
async def test_mocked_replacement_e2e_creates_and_rereads_all_objects():
    class MockProvider:
        def __init__(self):
            self.calls = []

        async def create_campaign(self, payload):
            self.calls.append(("create_campaign", payload)); return {"Id": 9001, **payload}

        async def reread_campaign(self, campaign_id):
            self.calls.append(("reread_campaign", campaign_id)); return {"Id": campaign_id, "State": "OFF"}

        async def create_ad_group(self, payload):
            self.calls.append(("create_ad_group", payload)); return {"Id": 9002, **payload}

        async def reread_ad_group(self, group_id):
            self.calls.append(("reread_ad_group", group_id)); return {"Id": group_id, "CampaignId": "9001"}

        async def create_ad(self, payload):
            self.calls.append(("create_ad", payload)); return {"Id": 9003, **payload}

        async def reread_ad(self, ad_id):
            self.calls.append(("reread_ad", ad_id)); return {"Id": ad_id, "State": "DRAFT", "Status": "MODERATION"}

    provider = MockProvider()
    result = await provision_replacement(spec=_spec(), provider=provider, owner_approved=True, live=True)
    assert result["status"] == "PROVISIONED"
    assert result["old_campaign_id"] == LEGACY_CAMPAIGN_ID
    assert result["new_campaign_write"] == "READY_BUT_DISABLED"
    assert [call[0] for call in provider.calls] == [
        "create_campaign", "reread_campaign", "create_ad_group", "reread_ad_group", "create_ad", "reread_ad",
    ]


@pytest.mark.asyncio
async def test_live_replacement_requires_owner_approval():
    with pytest.raises(PermissionError, match="OWNER_APPROVAL"):
        await provision_replacement(spec=_spec(), provider=object(), live=True)


@pytest.mark.asyncio
async def test_unknown_settings_block_replacement_instead_of_guessing():
    result = await provision_replacement(spec=_spec(unknown_fields=["geo", "target_cpa"]), provider=object(), live=True, owner_approved=True)
    assert result["status"] == "BLOCKED"
    assert result["old_campaign_write"] == "DENIED"


def test_cutover_plan_keeps_legacy_campaign_read_only():
    plan = cutover_plan(new_campaign_id="9001")
    assert "explicitly authorize 9001 for Autopilot" in plan
    assert f"keep legacy {LEGACY_CAMPAIGN_ID} read-only" in plan


@pytest.mark.asyncio
async def test_yandex_provider_adapter_uses_structured_add_then_reread():
    class Client:
        def __init__(self):
            self.calls = []

        async def mutate(self, resource, *, method, params):
            self.calls.append((resource, method, params))
            return {"AddResults": [{"Id": 9100 if resource == "campaigns" else 9200 if resource == "adgroups" else 9300}]}

        async def get_campaign(self, campaign_id):
            self.calls.append(("get_campaign", campaign_id)); return {"Id": campaign_id, "State": "OFF"}

        async def get_ad_groups_by_ids(self, ids):
            self.calls.append(("get_ad_groups", ids)); return [{"Id": ids[0], "CampaignId": "9100"}]

        async def get_ads_by_ids(self, ids):
            self.calls.append(("get_ads", ids)); return [{"Id": ids[0], "CampaignId": "9100", "State": "DRAFT"}]

    client = Client()
    provider = YandexReplacementProvider(client)
    assert (await provider.create_campaign({"Name": "x"}))["Id"] == 9100
    assert (await provider.reread_campaign("9100"))["Id"] == 9100
    assert (await provider.create_ad_group({"Name": "g"}))["Id"] == 9200
    assert (await provider.reread_ad_group("9200"))["CampaignId"] == "9100"
    assert (await provider.create_ad({"TextAd": {}}))["Id"] == 9300
    assert (await provider.reread_ad("9300"))["State"] == "DRAFT"
    assert [item[0] for item in client.calls if isinstance(item, tuple)] == [
        "campaigns", "get_campaign", "adgroups", "get_ad_groups", "ads", "get_ads",
    ]


def test_replacement_children_feed_existing_autopilot_capability_path():
    assessment = assess_provider_capabilities(
        campaign_id="9100", campaign_state="ON",
        adgroups=[{"Id": "9200", "CampaignId": "9100"}],
        ads=[{"Id": "9300", "AdGroupId": "9200", "CampaignId": "9100"}],
        placement_state_visible=True, budget_state_visible=True,
    )
    assert assessment["provider_manageability"] == "FULL"
    assert set(assessment["verified_capabilities"]) == {
        "CREATE_AD_TEST", "CREATE_CREATIVE_TEST", "ENABLE_AD", "EXCLUDE_PLACEMENT",
        "PAUSE_AD", "RESTORE_PLACEMENT", "BOUNDED_BUDGET_REALLOCATION",
    }
