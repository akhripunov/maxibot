from __future__ import annotations

import httpx
import pytest
from datetime import date, datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.db.models import AdsAccount
from app.db.models import AdsCampaign, AdsCollectionRun, AdsDailyAggregate, AdsObservation, AdsReportRun, AdsDataHealth, Base
from app.ads.telegram_collection import persist_telegram_report
from app.ads.telegram_normalization import classify_provider_error, data_health_for_error, logical_observation_key, normalize_creative, normalize_stats

from app.ads.providers.telegram import (
    TelegramAdsContractError,
    TelegramAdsGateway,
    TelegramAdsOwnerContext,
    TelegramAdsProvider,
    TelegramAdsSessionState,
    decrypt_session_cookies,
    encrypt_session_cookies,
    parse_accounts,
    parse_ads,
    parse_stats,
)


def test_provider_registration_and_parsers_are_strict_and_sanitized():
    html = '''<header class="pr-header-account-name">GPoint News</header>
    <a href="/choose_account/org-1">GPoint News</a>
    <a href="/account/ad/42">Launch</a>'''
    assert parse_accounts(html) == [{"external_id": "org-1", "display_name": "GPoint News"}]
    assert parse_ads(html)[0]["Id"] == "42"
    assert "stel_token" not in parse_ads(html)[0]


def test_multiple_telegram_organizations_per_admin_use_external_identity_key():
    constraints = {constraint.name: tuple(column.name for column in constraint.columns) for constraint in AdsAccount.__table__.constraints if constraint.name}
    assert constraints["uq_ads_account_provider_external"] == ("admin_id", "provider", "external_account_id")


def test_telegram_normalization_preserves_missing_metrics_and_creative_hash():
    creative = normalize_creative({"Id": "42", "title": "Title", "promote_url": "https://example.test"})
    assert creative["title"] == "Title"
    assert creative["content_hash"]
    stats = normalize_stats({"Impressions": 4, "Clicks": 2})
    assert stats["impressions"] == 4
    assert stats["clicks"] == 2
    assert stats["cost"] is None
    assert logical_observation_key(account_id="a", external_scope_id="42", report_type="STATS", date_from=None, date_to=None) == logical_observation_key(account_id="a", external_scope_id="42", report_type="STATS", date_from=None, date_to=None)
    assert data_health_for_error("TELEGRAM_ADS_OWNER_MISMATCH") == ("DEGRADED", "OWNER_MISMATCH")


def test_missing_provider_selector_fails_closed():
    with pytest.raises(TelegramAdsContractError, match="ACCOUNT_SELECTOR"):
        parse_accounts('<a href="/choose_account/org-1"></a>')
    with pytest.raises(TelegramAdsContractError, match="AD_SELECTOR"):
        parse_ads('<a href="/account/ad/42"></a>')


def test_valid_organization_empty_inventory_with_create_cta_is_empty():
    html = '''<header class="pr-header-account-name">GPoint News</header>
    <h1>You have no ads yet…</h1>
    <p>Create your first ad by clicking the button below.</p>
    <a href="/account/ad/new">Create a new ad</a>'''
    assert parse_ads(html) == []


def test_create_cta_without_owner_or_empty_heading_fails_closed():
    with pytest.raises(TelegramAdsContractError, match="AD_SELECTOR"):
        parse_ads('<a href="/account/ad/new">Create a new ad</a>')
    with pytest.raises(TelegramAdsContractError, match="AD_SELECTOR"):
        parse_ads('<header class="pr-header-account-name">GPoint News</header><a href="/account/ad/new">Create a new ad</a>')


def test_unknown_inventory_markup_with_owner_fails_closed():
    html = '''<header class="pr-header-account-name">GPoint News</header>
    <div class="new-ad-layout">Create a new ad</div>
    <a href="/account/ad/42"></a>'''
    with pytest.raises(TelegramAdsContractError, match="AD_SELECTOR"):
        parse_ads(html)


def test_stats_keep_unavailable_metrics_unknown_not_zero():
    assert parse_stats("Impressions: 12 Clicks: 3 Spend: 4,50") == {
        "Impressions": 12.0, "Views": None, "Clicks": 3.0,
        "Cost": 4.5, "Conversions": None, "CPM": None,
    }


def test_network_errors_are_not_auth_errors():
    assert classify_provider_error(httpx.ConnectTimeout("timeout")) == "NETWORK_UNAVAILABLE"
    assert data_health_for_error("NETWORK_UNAVAILABLE")[1] == "NETWORK_UNAVAILABLE"
    assert data_health_for_error("EXECUTION_REGION_UNAVAILABLE")[1] == "EXECUTION_REGION_UNAVAILABLE"


def test_session_cookies_are_encrypted_and_round_trip_without_plaintext_storage():
    ciphertext = encrypt_session_cookies({"stel_token": "secret", "stel_ssid": "sid"})
    assert "secret" not in ciphertext
    assert decrypt_session_cookies(ciphertext) == {"stel_token": "secret", "stel_ssid": "sid"}


@pytest.mark.asyncio
async def test_owner_assertion_and_302_auth_are_fail_closed():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/account":
            return httpx.Response(302, headers={"location": "/auth"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://ads.telegram.org")
    gateway = TelegramAdsGateway(TelegramAdsOwnerContext("org-1"), client=client)
    with pytest.raises(TelegramAdsContractError, match="REAUTH_REQUIRED"):
        await gateway.assert_owner()
    assert gateway.context.session_state == TelegramAdsSessionState.REAUTH_REQUIRED
    await gateway.close()


@pytest.mark.asyncio
async def test_owner_context_isolation_and_provider_read_path():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text='<header class="pr-header-account-name">GPoint News</header><a href="/account/ad/42">A</a>')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://ads.telegram.org")
    context = TelegramAdsOwnerContext("org-1", "GPoint News", {"stel_token": "redacted-in-test"})
    provider = TelegramAdsProvider(context, gateway=TelegramAdsGateway(context, client=client))
    report = await provider.campaigns()
    assert provider.provider_name == "TELEGRAM_ADS"
    assert report.rows[0]["Id"] == "42"
    assert provider.gateway.context.session_state == TelegramAdsSessionState.VALID
    await provider.close()


@pytest.mark.asyncio
async def test_owner_selection_requires_exact_external_id():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text='<header class="pr-header-account-name">GPoint News</header>')), base_url="https://ads.telegram.org")
    gateway = TelegramAdsGateway(TelegramAdsOwnerContext("org-1", "GPoint News"), client=client)
    with pytest.raises(TelegramAdsContractError, match="SELECTION_MISMATCH"):
        await gateway.select_account("other-org")
    await gateway.close()


@pytest.mark.asyncio
async def test_telegram_report_persistence_is_idempotent_for_same_collection_slot():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        account = AdsAccount(id="acct-telegram", admin_id="admin-1", provider="TELEGRAM_ADS", external_account_id="org-1")
        campaign = AdsCampaign(id="campaign-telegram", account_id=account.id, ads_account_id=account.id, external_id="ad-42", external_campaign_id="ad-42", name="Launch", campaign_type="TELEGRAM_AD")
        run = AdsCollectionRun(id="run-telegram", provider="TELEGRAM_ADS", account_id=account.id, campaign_id=campaign.id, destination_platform="TELEGRAM_ADS", collection_type="CAMPAIGN", run_type="PERIODIC_CAMPAIGN", trigger="PERIODIC", scheduled_at=datetime.now(timezone.utc), slot_key="slot-telegram")
        db.add_all([account, campaign, run])
        await db.flush()
        payload = [{"CampaignId": "ad-42", "Impressions": 12, "Clicks": 3, "Cost": "4.50"}]
        kwargs = dict(account=account, campaign=campaign, collection_run=run, rows=payload, date_from=date(2026, 9, 14), date_to=date(2026, 9, 15), fetched_at=datetime(2026, 9, 15, tzinfo=timezone.utc))
        first = await persist_telegram_report(db, **kwargs)
        await db.commit()
        second = await persist_telegram_report(db, **kwargs)
        await db.commit()
        assert first["observations_created"] == 1
        assert second["observations_created"] == 0
        assert await db.scalar(select(func.count()).select_from(AdsReportRun)) == 1
        assert await db.scalar(select(func.count()).select_from(AdsObservation)) == 1
        assert await db.scalar(select(func.count()).select_from(AdsDailyAggregate)) == 1
        assert await db.scalar(select(func.count()).select_from(AdsDataHealth)) == 1
        observation = await db.scalar(select(AdsObservation))
        assert observation.impressions == 12
        assert observation.cost == 4.5
    await engine.dispose()
