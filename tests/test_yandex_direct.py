import json

import httpx
import pytest

from app.integrations.yandex_direct import YandexDirectAPIError, YandexDirectClient


@pytest.mark.asyncio
async def test_get_campaign_uses_direct_api_v5_and_only_read_payload(monkeypatch):
    requests = []

    async def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(200, json={"result": {"Campaigns": [{"Id": 713735649, "Name": "m365_rsya_msk_conv_test2", "State": "ON"}]}})

    transport = httpx.MockTransport(handler)
    client = YandexDirectClient("opaque", "https://direct.test", "v5")
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="https://direct.test/v5", transport=transport, headers={"Authorization": "Bearer opaque"})
    result = await client.get_campaign(713735649)
    await client.close()

    assert result["Name"] == "m365_rsya_msk_conv_test2"
    assert requests[0].url == "https://direct.test/v5/campaigns"
    assert requests[0].headers["authorization"] == "Bearer opaque"
    payload = requests[0].content.decode()
    assert '"method":"get"' in payload
    assert "Update" not in payload
    assert "Campaigns" not in payload


@pytest.mark.asyncio
async def test_direct_api_errors_are_not_silenced():
    async def handler(request: httpx.Request):
        return httpx.Response(403, json={"error": {"Code": "AuthorizationError"}})

    client = YandexDirectClient("opaque", "https://direct.test", "v5")
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="https://direct.test/v5", transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_campaign(713735649)
    await client.close()


@pytest.mark.asyncio
async def test_direct_api_body_error_is_not_treated_as_empty_success():
    async def handler(request: httpx.Request):
        return httpx.Response(200, json={"error": {"error_code": "AuthorizationError", "Description": "denied"}})

    client = YandexDirectClient("opaque", "https://direct.test", "v5")
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="https://direct.test/v5", transport=httpx.MockTransport(handler))
    with pytest.raises(YandexDirectAPIError):
        await client.get_campaign(713735649)
    await client.close()


@pytest.mark.asyncio
async def test_campaign_report_is_read_only_and_parses_tsv(monkeypatch):
    requests = []

    async def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(
            200,
            text="Date\tCampaignId\tCampaignName\tImpressions\tClicks\tCost\tConversions\n2026-08-29\t713798021\tm365\t100\t32\t12.50\t2\n",
        )

    client = YandexDirectClient("opaque", "https://direct.test", "v5")
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="https://direct.test/v5", transport=httpx.MockTransport(handler))
    rows = await client.get_campaign_report(713798021, date_from="2026-08-29", date_to="2026-08-29", goal_id=602337706)
    await client.close()

    assert rows[0]["Clicks"] == "32"
    assert rows[0]["Conversions"] == "2"
    assert requests[0].url == "https://direct.test/v5/reports"
    payload = requests[0].content.decode()
    assert '"ReportType":"CAMPAIGN_PERFORMANCE_REPORT"' in payload
    assert '"Goals":["602337706"]' in payload
    assert "Update" not in payload


@pytest.mark.asyncio
async def test_campaign_report_json_error_with_http_200_is_not_empty_success():
    async def handler(request: httpx.Request):
        return httpx.Response(200, json={"error": {"error_code": "AuthorizationError", "Description": "denied"}})

    client = YandexDirectClient("opaque", "https://direct.test", "v5")
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="https://direct.test/v5", transport=httpx.MockTransport(handler))
    with pytest.raises(YandexDirectAPIError):
        await client.get_campaign_report(713798021, date_from="2026-08-29", date_to="2026-08-29")
    await client.close()


@pytest.mark.asyncio
async def test_get_ads_requests_text_ad_image_hash_field():
    requests = []

    async def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(200, json={"result": {"Ads": [{"Id": 1, "Type": "TEXT_AD", "TextAd": {"AdImageHash": "image-hash"}}]}})

    client = YandexDirectClient("opaque", "https://direct.test", "v5")
    await client._client.aclose()
    client._client = httpx.AsyncClient(base_url="https://direct.test/v5", transport=httpx.MockTransport(handler))
    ads = await client.get_ads([713798021])
    await client.close()

    assert ads[0]["TextAd"]["AdImageHash"] == "image-hash"
    payload = json.loads(requests[0].content)
    assert "AdImageHash" in payload["params"]["TextAdFieldNames"]
    assert payload["params"]["ResponsiveAdFieldNames"] == ["AdImages"]
    assert "Href" not in payload["params"]["MobileAppAdFieldNames"]
    assert payload["params"]["MobileAppImageAdFieldNames"] == ["AdImageHash"]
    assert "CpcVideoAdFieldNames" not in payload["params"]
    assert "Update" not in str(payload)
