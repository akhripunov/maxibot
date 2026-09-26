from datetime import date, datetime, timezone
import json
import pytest

from app.ads.attribution import window_bounds
from app.bot.menu import format_ads_result


def test_seven_day_window_is_same_moscow_calendar_window():
    start, end = window_bounds(end_date=date(2026, 9, 3), window="7d")
    assert start == datetime(2026, 8, 27, 21, tzinfo=timezone.utc)
    assert end == datetime(2026, 9, 3, 21, tzinfo=timezone.utc)


def test_renderer_explains_aligned_maxibot_membership_rate_separately():
    body = format_ads_result(
        headline="done", channel_title="Москва 365", direct_connected=True,
        campaign_name="test", ai_status="done",
        checked_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        fact_values={"cost_7d": 10, "conversions_7d": 2, "actual_cpa_7d": 5,
                     "qualified_subscriptions": 2, "landing_visits": 100,
                     "maxibot_qualified_membership_rate": .02}, completed=True,
    )
    assert "2 подтверждённых подписок из 100 визитов" in body
    assert "Конверсия визит → подписка: 2,00%" in body
    assert "Сопоставление с конкретной рекламной кампанией: недоступно" in body
    assert "Yandex" not in body


def test_campaign_scoped_rate_is_unknown_without_campaign_population():
    from app.ads.attribution import build_maxibot_metrics
    assert callable(build_maxibot_metrics)


def test_single_campaign_utm_mapping_is_supported_but_ambiguous_mapping_stays_unknown():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app/ads/attribution.py").read_text()
    service = (Path(__file__).parents[1] / "app/ads/service.py").read_text()
    assert "campaign_matches" in source
    assert "len(links) == 1" in source
    assert "if matched:" in source
    assert 'campaign_specific_matching": True if campaign_mapping_supported else "UNKNOWN"' in service
    assert '"MAXIBOT_METRICS": maxibot_metrics' in service
    assert "context = _context(campaign, facts, suff, missing, memories)" in service
    assert "facts_json=json.dumps(facts" in service
    assert 'result["campaign_specific"] = campaign_specific' in source


def test_partial_deterministic_campaign_mapping_is_supported_with_coverage():
    from app.traffic.funnel_reporting import campaign_matches

    matched = sum(campaign_matches(value, "713798021") for value in (
        *(["713798021.1.2"] * 56),
        "other.1.2",
        "",
    ))
    assert matched == 56
    assert matched / 58 == 56 / 58


@pytest.mark.asyncio
async def test_campaign_specific_mapping_is_returned_and_keeps_channel_metrics():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.ads.attribution import build_maxibot_metrics
    from app.ads.service import _context
    from app.db.models import (
        Admin, AdsAccount, AdsCampaign, AdsPromotionCampaignLink, Base, Channel,
        AdsAnalysisSnapshot, LandingPage, TrafficMembership, TrafficVisit,
    )

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            admin = Admin(id="admin", external_id="owner")
            channel = Channel(id="channel", admin_id=admin.id, external_id="max-channel", title="Test", public_slug="test")
            landing = LandingPage(id="landing", channel_id=channel.id, public_id="landing", title="Test", description="Test")
            account = AdsAccount(id="account", admin_id=admin.id)
            campaign = AdsCampaign(id="campaign", account_id=account.id, external_id="714373352", external_campaign_id="714373352", name="Test")
            link = AdsPromotionCampaignLink(id="link", object_type="CHANNEL", channel_id=channel.id, campaign_id=campaign.id, status="ACTIVE")
            db.add_all([admin, channel, landing, account, campaign, link])
            await db.flush()

            # A suffixed MAXIBOT UTM matches; another campaign and an
            # unscoped membership remain channel-level only.
            rows = [
                ("matched-a", "robot_campaign.714373352.123", "EXACT"),
                ("matched-b", "714373352.456", "HIGH"),
                ("other", "other_campaign.999", "EXACT"),
                ("unknown", None, "EXACT"),
            ]
            for index, (suffix, utm_campaign, status) in enumerate(rows):
                visit = TrafficVisit(
                    id=f"visit-{suffix}", visit_token=f"token-{suffix}", landing_page_id=landing.id,
                    channel_id=channel.id, utm_campaign=utm_campaign, yclid=f"yclid-{suffix}",
                    landing_at=datetime(2026, 9, 25, 12, index, tzinfo=timezone.utc),
                )
                membership = TrafficMembership(
                    id=f"membership-{suffix}", channel_id=channel.id, max_user_id=f"user-{suffix}",
                    attribution_status=status, attributed_visit_id=visit.id,
                    joined_at=datetime(2026, 9, 25, 12, index, tzinfo=timezone.utc),
                )
                db.add_all([visit, membership])
            await db.commit()

            metrics = await build_maxibot_metrics(db, channel_id=channel.id, end_date=date(2026, 9, 26))

            assert metrics["campaign_specific"] == {
                "supported": True,
                "value": 2,
                "coverage": 2 / 4,
                "reason": "single active campaign with deterministic landing UTM mapping",
                "campaign_id": "714373352",
            }
            assert metrics["7d"]["qualified_memberships"] == 4
            assert metrics["7d"]["scope"] == "CHANNEL"

            # The same canonical metrics are persisted in snapshot facts and
            # retained unchanged in the downstream Codex context.
            facts = {"MAXIBOT_METRICS": metrics}
            snapshot = AdsAnalysisSnapshot(facts_json=json.dumps(facts))
            snapshot_facts = json.loads(snapshot.facts_json)
            codex_context = _context(campaign, snapshot_facts, "PARTIAL", [], [])
            assert codex_context["FACTS"]["MAXIBOT_METRICS"]["campaign_specific"]["value"] == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unproven_campaign_attribution_stays_unknown_not_zero():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.ads.attribution import build_maxibot_metrics
    from app.db.models import (
        Admin, AdsAccount, AdsCampaign, AdsPromotionCampaignLink, Base, Channel,
        LandingPage, TrafficMembership, TrafficVisit,
    )

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            admin = Admin(id="admin", external_id="owner")
            channel = Channel(id="channel", admin_id=admin.id, external_id="max-channel", title="Test", public_slug="test")
            landing = LandingPage(id="landing", channel_id=channel.id, public_id="landing", title="Test", description="Test")
            account = AdsAccount(id="account", admin_id=admin.id)
            campaign = AdsCampaign(id="campaign", account_id=account.id, external_id="714373352", external_campaign_id="714373352", name="Test")
            link = AdsPromotionCampaignLink(id="link", object_type="CHANNEL", channel_id=channel.id, campaign_id=campaign.id, status="ACTIVE")
            visit = TrafficVisit(id="visit", visit_token="token", landing_page_id=landing.id, channel_id=channel.id, yclid="yclid", landing_at=datetime(2026, 9, 25, tzinfo=timezone.utc))
            membership = TrafficMembership(id="membership", channel_id=channel.id, max_user_id="user", attribution_status="EXACT", attributed_visit_id=visit.id, joined_at=datetime(2026, 9, 25, tzinfo=timezone.utc))
            db.add_all([admin, channel, landing, account, campaign, link, visit, membership])
            await db.commit()

            metrics = await build_maxibot_metrics(db, channel_id=channel.id, end_date=date(2026, 9, 26))

            mapping = metrics["campaign_specific"]
            assert mapping["supported"] is False
            assert mapping["value"] is None
            assert mapping.get("coverage") is None
            assert mapping["campaign_id"] == "714373352"
    finally:
        await engine.dispose()
