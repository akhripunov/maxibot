from __future__ import annotations

from datetime import date, datetime, timezone

from app.ads.providers.base import AdsProvider, ProviderReport
from app.integrations.yandex_direct import YandexDirectClient


GRANULAR_REPORTS = {
    "AD_GROUP": {
        "report_type": "ADGROUP_PERFORMANCE_REPORT",
        "fields": ("Date", "CampaignId", "AdGroupId", "AdGroupName", "Impressions", "Clicks", "Cost", "Conversions"),
    },
    "AD": {
        "report_type": "AD_PERFORMANCE_REPORT",
        "fields": ("Date", "CampaignId", "AdGroupId", "AdId", "Impressions", "Clicks", "Cost", "Conversions"),
    },
    "PLACEMENT": {
        "report_type": "CUSTOM_REPORT",
        "fields": ("Date", "CampaignId", "AdGroupId", "AdId", "Placement", "Impressions", "Clicks", "Cost", "Conversions"),
    },
    "REGION": {
        "report_type": "CUSTOM_REPORT",
        "fields": ("Date", "CampaignId", "LocationOfPresenceId", "LocationOfPresenceName", "Impressions", "Clicks", "Cost", "Conversions"),
    },
    "DEVICE": {
        "report_type": "CUSTOM_REPORT",
        "fields": ("Date", "CampaignId", "Device", "MobilePlatform", "Impressions", "Clicks", "Cost", "Conversions"),
    },
    "DEMOGRAPHICS": {
        "report_type": "CUSTOM_REPORT",
        "fields": ("Date", "CampaignId", "Age", "Gender", "Impressions", "Clicks", "Cost", "Conversions"),
    },
    "CRITERIA": {
        "report_type": "CRITERIA_PERFORMANCE_REPORT",
        "fields": ("Date", "CampaignId", "AdGroupId", "CriteriaId", "Criteria", "CriteriaType", "Impressions", "Clicks", "Cost", "Conversions"),
    },
    "SEARCH_QUERY": {
        "report_type": "SEARCH_QUERY_PERFORMANCE_REPORT",
        "fields": ("Date", "CampaignId", "AdGroupId", "AdId", "CriteriaId", "Query", "Impressions", "Clicks", "Cost", "Conversions"),
    },
}


class YandexAdsProvider(AdsProvider):
    """Read-only adapter; Yandex report names stay outside ADS core."""
    provider_name = "YANDEX_DIRECT"

    def __init__(self, token: str):
        self.client = YandexDirectClient(token)

    async def close(self):
        await self.client.close()

    async def campaigns(self, campaign_ids: list[int] | None = None) -> ProviderReport:
        fields = ("Id", "Name", "Type", "State", "Status", "StatusPayment", "StartDate", "EndDate")
        return ProviderReport(self.provider_name, "CAMPAIGNS", await self.client.get_campaigns(campaign_ids), fields, fetched_at=datetime.now(timezone.utc))

    async def campaign_performance(self, campaign_id: int, *, date_from: date, date_to: date) -> ProviderReport:
        fields = ("Date", "CampaignId", "CampaignName", "Impressions", "Clicks", "Cost", "Conversions")
        return ProviderReport(self.provider_name, "CAMPAIGN_PERFORMANCE_REPORT", await self.client.get_campaign_report(campaign_id, date_from=date_from.isoformat(), date_to=date_to.isoformat()), fields, date_from, date_to, fetched_at=datetime.now(timezone.utc), freshness_caveat="current calendar day may be incomplete or delayed")

    async def ads(self, campaign_ids: list[int]) -> ProviderReport:
        fields = ("Id", "CampaignId", "Type", "Subtype", "State", "Status", "StatusClarification")
        return ProviderReport(self.provider_name, "ADS", await self.client.get_ads(campaign_ids), fields, fetched_at=datetime.now(timezone.utc))

    async def ad_groups(self) -> ProviderReport:
        return await self.unsupported("AD_GROUPS")

    async def creatives(self) -> ProviderReport:
        return await self.unsupported("CREATIVES")

    async def placements(self) -> ProviderReport:
        return await self.unsupported("PLACEMENTS")

    async def targeting(self) -> ProviderReport:
        return await self.unsupported("TARGETING")

    async def devices(self) -> ProviderReport:
        return await self.unsupported("DEVICES")

    async def regions(self) -> ProviderReport:
        return await self.unsupported("REGIONS")

    async def time_dimensions(self) -> ProviderReport:
        return await self.unsupported("TIME_DIMENSIONS")

    async def granular_report(self, dimension: str, campaign_id: int, *, date_from: date, date_to: date) -> ProviderReport:
        spec = GRANULAR_REPORTS[dimension]
        rows, report_id = await self.client.get_performance_report(
            spec["report_type"], campaign_id=campaign_id, date_from=date_from.isoformat(),
            date_to=date_to.isoformat(), fields=spec["fields"],
        )
        return ProviderReport(
            self.provider_name, spec["report_type"], rows, spec["fields"], date_from, date_to,
            fetched_at=datetime.now(timezone.utc), freshness_caveat="current calendar day may be incomplete or delayed",
            provider_report_id=report_id,
        )
