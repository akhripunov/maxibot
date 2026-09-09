from datetime import date

import pytest

from app.ads.providers.yandex import GRANULAR_REPORTS, YandexAdsProvider
from app.ads.v2 import _identity


def test_capability_matrix_uses_documented_report_types_and_hierarchy():
    assert GRANULAR_REPORTS["AD_GROUP"]["report_type"] == "ADGROUP_PERFORMANCE_REPORT"
    assert GRANULAR_REPORTS["AD"]["report_type"] == "AD_PERFORMANCE_REPORT"
    assert GRANULAR_REPORTS["CRITERIA"]["report_type"] == "CRITERIA_PERFORMANCE_REPORT"
    assert GRANULAR_REPORTS["SEARCH_QUERY"]["report_type"] == "SEARCH_QUERY_PERFORMANCE_REPORT"
    for spec in GRANULAR_REPORTS.values():
        assert {"Date", "Impressions", "Clicks", "Cost", "Conversions"}.issubset(spec["fields"])


def test_granular_identity_does_not_invent_hierarchy():
    assert _identity("AD_GROUP", {"AdGroupId": "10"}) == ("AD_GROUP", "10", "10")
    assert _identity("AD", {"AdId": "20"}) == ("AD", "20", "20")
    assert _identity("PLACEMENT", {"Placement": "example.org"}) == ("PLACEMENT", "placement:example.org", "example.org")
    assert _identity("REGION", {"LocationOfPresenceId": "213", "LocationOfPresenceName": "Москва"}) == ("REGION", "region:213", "213")
    assert _identity("DEVICE", {"Device": "MOBILE", "MobilePlatform": "IOS"}) == ("DEVICE", None, "MOBILE|IOS")


class FakeClient:
    async def get_performance_report(self, report_type, **kwargs):
        return ([{"Date": "2026-09-03", "Impressions": "1", "Clicks": "2", "Cost": "3", "Conversions": "4"}], "request-1")

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_provider_granular_report_preserves_report_metadata(monkeypatch):
    provider = YandexAdsProvider("redacted")
    provider.client = FakeClient()
    result = await provider.granular_report("AD_GROUP", 123, date_from=date(2026, 9, 1), date_to=date(2026, 9, 3))
    assert result.report_type == "ADGROUP_PERFORMANCE_REPORT"
    assert result.provider_report_id == "request-1"
    assert result.requested_fields == GRANULAR_REPORTS["AD_GROUP"]["fields"]
    assert result.rows[0]["Clicks"] == "2"
