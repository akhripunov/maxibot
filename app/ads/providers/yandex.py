from __future__ import annotations

from datetime import date, datetime, timezone

from app.ads.providers.base import AdsProvider, ProviderReport
from app.core.config import settings
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

    def __init__(self, token: str, *, client_login: str | None = None):
        # The API principal is the OAuth token; for agency accounts the
        # advertiser context must additionally be supplied as Client-Login.
        # Keep both API generations because unified performance campaigns are
        # exposed by v501 while legacy campaigns remain on v5.
        login = (client_login or settings.yandex_direct_client_login or "").strip() or None
        self.client = YandexDirectClient(token, client_login=login)
        self.unified_client = YandexDirectClient(token, api_version="v501", client_login=login)

    async def close(self):
        await self.client.close()
        await self.unified_client.close()

    async def campaigns(self, campaign_ids: list[int] | None = None) -> ProviderReport:
        fields = ("Id", "Name", "Type", "State", "Status", "StatusPayment", "StartDate", "EndDate")
        catalogs = [await self.client.get_campaigns(campaign_ids)]
        try:
            catalogs.append(await self.unified_client.get_campaigns(campaign_ids))
        except Exception:
            # Older accounts/tokens may not expose v501. Legacy discovery is
            # still valid, but must not hide the v5 result.
            pass
        rows = []
        seen: set[str] = set()
        for catalog in catalogs:
            for row in catalog:
                ident = str(row.get("Id") or "")
                if ident and ident not in seen:
                    seen.add(ident)
                    rows.append(row)
        return ProviderReport(self.provider_name, "CAMPAIGNS", rows, fields, fetched_at=datetime.now(timezone.utc))

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
